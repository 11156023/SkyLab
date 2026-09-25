"""Per-request context for capturing client IP / User-Agent.

Stores the current request's client IP and user agent in a ContextVar so that
service-layer code (which doesn't receive the FastAPI Request object) can
attach them to audit log entries automatically.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SUPPORTED_LANGUAGES = ("zh-TW", "en", "ja")
DEFAULT_LANGUAGE = "zh-TW"

REQUEST_ID_HEADER = b"x-request-id"
# nginx 的 $request_id 是 32 位 hex；也接受一般 UUID／自訂追蹤 id。長度與字元
# 受限，避免客戶端把換行或超長字串塞進日誌。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def resolve_language(accept_language: str | None) -> str:
    """Pick a supported language from an Accept-Language header value."""
    if not accept_language:
        return DEFAULT_LANGUAGE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip()
        if tag in SUPPORTED_LANGUAGES:
            return tag
        base = tag.split("-")[0].lower()
        for lang in SUPPORTED_LANGUAGES:
            if lang.split("-")[0].lower() == base:
                return lang
    return DEFAULT_LANGUAGE


@dataclass
class RequestContext:
    ip_address: str | None = None
    user_agent: str | None = None
    language: str = DEFAULT_LANGUAGE
    request_id: str | None = None


def resolve_request_id(incoming: str | None) -> str:
    """沿用上游（nginx）帶來的合法 X-Request-ID，否則新產生一個。"""
    if incoming and _REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex


_request_context: ContextVar[RequestContext] = ContextVar(
    "request_context", default=RequestContext()  # noqa: B039 - RequestContext is an immutable default snapshot, never mutated in place
)


def get_request_context() -> RequestContext:
    return _request_context.get()


def set_request_context(ctx: RequestContext) -> None:
    _request_context.set(ctx)


def _extract_client_ip(headers: list[tuple[bytes, bytes]], client_host: str | None) -> str | None:
    """Resolve the real client IP from proxy headers, falling back to socket peer."""
    header_map: dict[str, str] = {}
    for name, value in headers:
        try:
            header_map[name.decode("latin-1").lower()] = value.decode("latin-1")
        except Exception:
            continue

    # Trust X-Real-IP first: nginx sets it to $remote_addr, which the client
    # cannot forge. X-Forwarded-For is built with $proxy_add_x_forwarded_for,
    # so a client-supplied XFF header is preserved as the *leading* entries and
    # only the LAST hop (appended by our nginx) is trustworthy. Never take the
    # first XFF value — it is attacker-controlled and would let a caller spoof
    # their source IP (bypassing per-IP rate limits and poisoning audit logs).
    real_ip = header_map.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()

    forwarded_for = header_map.get("x-forwarded-for")
    if forwarded_for:
        hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
        if hops:
            return hops[-1]

    return client_host


def _extract_user_agent(headers: list[tuple[bytes, bytes]]) -> str | None:
    return _extract_header(headers, b"user-agent", max_len=512)


def _extract_header(headers: list[tuple[bytes, bytes]], name: bytes, max_len: int = 256) -> str | None:
    for header_name, value in headers:
        if header_name.lower() == name:
            try:
                return value.decode("latin-1")[:max_len]
            except Exception:
                return None
    return None


class RequestContextMiddleware:
    """Pure-ASGI middleware that captures client IP/UA into a ContextVar.

    Must be added before any code that calls audit_service.log_action.

    也負責 request id：沿用 nginx 帶來的 ``X-Request-ID``（或自行產生），寫進
    ContextVar 讓 JSON 日誌帶上，並回寫到回應標頭，前端／nginx 日誌／後端
    日誌三邊可以用同一個 id 對上。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        client = scope.get("client")
        client_host = client[0] if client else None
        request_id = resolve_request_id(
            _extract_header(headers, REQUEST_ID_HEADER, max_len=128)
        )

        ctx = RequestContext(
            ip_address=_extract_client_ip(headers, client_host),
            user_agent=_extract_user_agent(headers),
            language=resolve_language(_extract_header(headers, b"accept-language")),
            request_id=request_id,
        )
        _tag_sentry_scope(request_id)
        token = _request_context.set(ctx)
        try:
            if scope["type"] == "http":
                await self.app(scope, receive, _with_request_id_header(send, request_id))
            else:
                await self.app(scope, receive, send)
        finally:
            _request_context.reset(token)


def _with_request_id_header(send: Send, request_id: str) -> Send:
    encoded = request_id.encode("latin-1")

    async def send_wrapper(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers: list[Any] = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != REQUEST_ID_HEADER
            ]
            headers.append((REQUEST_ID_HEADER, encoded))
            message = {**message, "headers": headers}
        await send(message)

    return send_wrapper


def _tag_sentry_scope(request_id: str) -> None:
    """有啟用 Sentry 時把 request id 掛成 tag，事件可以反查到日誌。"""
    try:
        import sentry_sdk

        if sentry_sdk.get_client().is_active():
            sentry_sdk.get_isolation_scope().set_tag("request_id", request_id)
    except Exception:  # noqa: S110 - Sentry 是選用的，失敗不影響請求
        pass
