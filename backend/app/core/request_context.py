"""Per-request context for capturing client IP / User-Agent.

Stores the current request's client IP and user agent in a ContextVar so that
service-layer code (which doesn't receive the FastAPI Request object) can
attach them to audit log entries automatically.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass
class RequestContext:
    ip_address: str | None = None
    user_agent: str | None = None


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
    for name, value in headers:
        if name.lower() == b"user-agent":
            try:
                return value.decode("latin-1")[:512]
            except Exception:
                return None
    return None


class RequestContextMiddleware:
    """Pure-ASGI middleware that captures client IP/UA into a ContextVar.

    Must be added before any code that calls audit_service.log_action.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        client = scope.get("client")
        client_host = client[0] if client else None

        ctx = RequestContext(
            ip_address=_extract_client_ip(headers, client_host),
            user_agent=_extract_user_agent(headers),
        )
        token = _request_context.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_context.reset(token)
