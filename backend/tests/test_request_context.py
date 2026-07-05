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

    async def downstream(scope, receive, send):  # noqa: ANN001
        called.append(scope["type"])

    mw = RequestContextMiddleware(downstream)
    await mw({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert called == ["lifespan"]


@pytest.mark.asyncio
async def test_middleware_sets_context_during_http_request() -> None:
    captured: dict[str, str | None] = {}

    async def downstream(scope, receive, send):  # noqa: ANN001
        ctx = get_request_context()
        captured["ip"] = ctx.ip_address
        captured["ua"] = ctx.user_agent

    async def receive():
        return {"type": "http.request"}

    async def send(message):  # noqa: ANN001
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

    async def downstream(scope, receive, send):  # noqa: ANN001
        pass

    async def receive():
        return {"type": "http.request"}

    async def send(message):  # noqa: ANN001
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
