"""Tests for app.core.request_context — context var + ASGI middleware.

Covers IP / user-agent extraction precedence:
- X-Real-IP > X-Forwarded-For (last/trusted hop) > socket peer
  (never the first XFF hop — that value is client-supplied and spoofable)
- User-Agent header truncated to 512 chars
"""

from __future__ import annotations

import pytest

from app.core.request_context import (
    RequestContext,
    RequestContextMiddleware,
    _extract_client_ip,
    _extract_user_agent,
    get_request_context,
    resolve_request_id,
    set_request_context,
)

# ─── Pure helpers ────────────────────────────────────────────────────────────


def test_extract_client_ip_prefers_real_ip_over_xff() -> None:
    # X-Real-IP (set by nginx to the unforgeable $remote_addr) must win over
    # any client-supplied X-Forwarded-For, so a spoofed leading XFF hop
    # (203.0.113.42) is ignored.
    headers = [
        (b"x-forwarded-for", b"203.0.113.42, 10.0.0.1, 10.0.0.2"),
        (b"x-real-ip", b"10.0.0.99"),
    ]
    assert _extract_client_ip(headers, "127.0.0.1") == "10.0.0.99"


def test_extract_client_ip_uses_xff_last_hop_when_no_real_ip() -> None:
    # Without X-Real-IP, only the LAST XFF hop is trustworthy (appended by our
    # own nginx); the spoofable leading hops must never be returned.
    headers = [
        (b"x-forwarded-for", b"203.0.113.42, 10.0.0.1, 10.0.0.2"),
    ]
    assert _extract_client_ip(headers, "127.0.0.1") == "10.0.0.2"


def test_extract_client_ip_falls_back_to_real_ip() -> None:
    headers = [(b"x-real-ip", b"198.51.100.7")]
    assert _extract_client_ip(headers, "127.0.0.1") == "198.51.100.7"


def test_extract_client_ip_falls_back_to_socket_peer() -> None:
    assert _extract_client_ip([], "127.0.0.1") == "127.0.0.1"


def test_extract_client_ip_no_headers_no_peer_returns_none() -> None:
    assert _extract_client_ip([], None) is None


def test_extract_client_ip_empty_xff_uses_real_ip() -> None:
    headers = [
        (b"x-forwarded-for", b"   "),
        (b"x-real-ip", b"203.0.113.5"),
    ]
    assert _extract_client_ip(headers, "127.0.0.1") == "203.0.113.5"


def test_extract_user_agent_returns_value() -> None:
    headers = [(b"user-agent", b"Mozilla/5.0 (Windows)")]
    assert _extract_user_agent(headers) == "Mozilla/5.0 (Windows)"


def test_extract_user_agent_truncates_to_512_chars() -> None:
    long_ua = "X" * 1000
    headers = [(b"user-agent", long_ua.encode())]
    result = _extract_user_agent(headers)
    assert result is not None
    assert len(result) == 512


def test_extract_user_agent_missing_returns_none() -> None:
    assert _extract_user_agent([]) is None


# ─── Context var ─────────────────────────────────────────────────────────────


def test_set_and_get_request_context_round_trip() -> None:
    ctx = RequestContext(ip_address="10.0.0.1", user_agent="ua")
    set_request_context(ctx)
    fetched = get_request_context()
    assert fetched.ip_address == "10.0.0.1"
    assert fetched.user_agent == "ua"


def test_default_request_context_has_none_fields() -> None:
    set_request_context(RequestContext())
    fetched = get_request_context()
    assert fetched.ip_address is None
    assert fetched.user_agent is None


# ─── ASGI middleware ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_passes_through_non_http_scope_unchanged() -> None:
    called: list[str] = []

    async def downstream(scope, receive, send):
        called.append(scope["type"])

    mw = RequestContextMiddleware(downstream)
    await mw({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called == ["lifespan"]


@pytest.mark.asyncio
async def test_middleware_sets_context_during_http_request() -> None:
    captured: dict[str, str | None] = {}

    async def downstream(scope, receive, send):
        ctx = get_request_context()
        captured["ip"] = ctx.ip_address
        captured["ua"] = ctx.user_agent

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    mw = RequestContextMiddleware(downstream)
    scope = {
        "type": "http",
        "headers": [
            (b"x-forwarded-for", b"203.0.113.10"),
            (b"user-agent", b"pytest-client/2.0"),
        ],
        "client": ("127.0.0.1", 12345),
    }
    await mw(scope, receive, send)  # type: ignore[arg-type]

    assert captured["ip"] == "203.0.113.10"
    assert captured["ua"] == "pytest-client/2.0"


@pytest.mark.asyncio
async def test_middleware_resets_context_after_request() -> None:
    """ContextVar token must be reset so requests don't leak across each other."""

    async def downstream(scope, receive, send):
        pass

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    set_request_context(RequestContext())

    mw = RequestContextMiddleware(downstream)
    scope = {
        "type": "http",
        "headers": [(b"x-real-ip", b"1.2.3.4")],
        "client": ("127.0.0.1", 1),
    }
    await mw(scope, receive, send)  # type: ignore[arg-type]

    # After the request finishes the ctx should be back to defaults
    assert get_request_context().ip_address is None


# ─── Request ID ──────────────────────────────────────────────────────────────


def test_resolve_request_id_keeps_valid_upstream_id() -> None:
    nginx_id = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
    assert resolve_request_id(nginx_id) == nginx_id


@pytest.mark.parametrize(
    "incoming",
    [None, "", "short", "has space in it", "line\nbreak-0123456789", "x" * 65],
)
def test_resolve_request_id_replaces_invalid_values(incoming: str | None) -> None:
    generated = resolve_request_id(incoming)
    assert generated != incoming
    assert len(generated) == 32


async def _run_http(
    headers: list[tuple[bytes, bytes]],
) -> tuple[str | None, list[tuple[bytes, bytes]]]:
    seen: dict[str, str | None] = {}
    sent: list[dict] = []

    async def downstream(scope, receive, send):
        seen["request_id"] = get_request_context().request_id
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-request-id", b"downstream-should-be-replaced")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    mw = RequestContextMiddleware(downstream)
    scope = {"type": "http", "headers": headers, "client": ("127.0.0.1", 1)}
    await mw(scope, receive, send)  # type: ignore[arg-type]
    start = next(m for m in sent if m["type"] == "http.response.start")
    return seen["request_id"], start["headers"]


async def test_middleware_propagates_request_id_to_context_and_response() -> None:
    request_id, headers = await _run_http([(b"x-request-id", b"abcdef0123456789")])

    assert request_id == "abcdef0123456789"
    assert [v for k, v in headers if k == b"x-request-id"] == [b"abcdef0123456789"]


async def test_middleware_generates_request_id_when_missing() -> None:
    request_id, headers = await _run_http([])

    assert request_id is not None and len(request_id) == 32
    assert [v for k, v in headers if k == b"x-request-id"] == [request_id.encode()]


async def test_middleware_sets_context_for_websocket_without_touching_send() -> None:
    seen: dict[str, str | None] = {}

    async def downstream(scope, receive, send):
        ctx = get_request_context()
        seen["ip"] = ctx.ip_address
        seen["request_id"] = ctx.request_id
        seen["send_is_original"] = send is original_send

    async def original_send(message):
        pass

    mw = RequestContextMiddleware(downstream)
    scope = {
        "type": "websocket",
        "headers": [(b"x-real-ip", b"198.51.100.7")],
        "client": ("127.0.0.1", 1),
    }
    await mw(scope, None, original_send)  # type: ignore[arg-type]

    assert seen["ip"] == "198.51.100.7"
    assert seen["request_id"]
    assert seen["send_is_original"] is True
