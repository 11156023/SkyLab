"""Tests for app.core.metrics — Prometheus middleware + endpoint."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core import metrics as metrics_module
from app.core.metrics import (
    _AVAILABLE,
    UNMATCHED_PATH,
    PrometheusMiddleware,
    _route_template,
    metrics_endpoint,
    register_collect_hook,
    track_websocket,
)

# ─── _route_template helper ──────────────────────────────────────────────────


def test_route_template_collapses_unmatched_paths() -> None:
    # 沒對到路由的原始路徑不能當 label，否則掃描器亂打會讓時間序列爆量
    scope = {"type": "http", "path": "/wp-admin/../../etc/passwd"}
    assert _route_template(scope) == UNMATCHED_PATH


def test_route_template_returns_route_path_when_present() -> None:
    class _Route:
        path = "/resources/{vmid}"

    scope = {"type": "http", "path": "/resources/100", "route": _Route()}
    assert _route_template(scope) == "/resources/{vmid}"


def test_route_template_unmatched_when_nothing_set() -> None:
    assert _route_template({"type": "http"}) == UNMATCHED_PATH


# ─── PrometheusMiddleware ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_passes_through_non_http() -> None:
    called: list[str] = []

    async def downstream(scope, receive, send):
        called.append(scope["type"])

    mw = PrometheusMiddleware(downstream)
    await mw({"type": "websocket"}, None, None)  # type: ignore[arg-type]
    assert called == ["websocket"]


@pytest.mark.asyncio
async def test_middleware_invokes_app_and_records_status() -> None:
    sent: list[dict] = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request"}

    async def capture_send(msg):
        sent.append(msg)

    mw = PrometheusMiddleware(downstream)
    await mw(
        {"type": "http", "method": "GET", "path": "/ping"},
        receive,  # type: ignore[arg-type]
        capture_send,  # type: ignore[arg-type]
    )

    assert any(m.get("status") == 204 for m in sent if m["type"] == "http.response.start")


@pytest.mark.asyncio
async def test_middleware_records_500_when_app_raises() -> None:
    """If the downstream app raises, status defaults to 500 (no http.response.start sent)."""

    async def downstream(scope, receive, send):
        raise RuntimeError("explode")

    async def receive():
        return {"type": "http.request"}

    async def send(_):
        pass

    mw = PrometheusMiddleware(downstream)
    with pytest.raises(RuntimeError, match="explode"):
        await mw({"type": "http", "method": "POST", "path": "/x"}, receive, send)  # type: ignore[arg-type]


# ─── metrics_endpoint ────────────────────────────────────────────────────────


def _fake_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/metrics",
        "headers": headers or [],
    }
    return Request(scope)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_payload_or_503() -> None:
    response = await metrics_endpoint(_fake_request())
    if _AVAILABLE:
        # When prometheus_client is installed we get a 200 with text/plain
        assert response.status_code == 200
        assert "text/plain" in response.media_type or response.media_type.startswith(
            "text/plain"
        )
    else:
        assert response.status_code == 503
        assert b"prometheus_client" in response.body


@pytest.mark.asyncio
@pytest.mark.skipif(not _AVAILABLE, reason="prometheus_client not installed")
async def test_metrics_endpoint_requires_token_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(metrics_module.settings, "METRICS_TOKEN", "s3cret-token")

    denied = await metrics_endpoint(_fake_request())
    assert denied.status_code == 401

    wrong = await metrics_endpoint(_fake_request([(b"authorization", b"Bearer nope")]))
    assert wrong.status_code == 401

    ok = await metrics_endpoint(
        _fake_request([(b"authorization", b"Bearer s3cret-token")])
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
@pytest.mark.skipif(not _AVAILABLE, reason="prometheus_client not installed")
async def test_metrics_endpoint_runs_collect_hooks_and_survives_failures(
    monkeypatch,
) -> None:
    monkeypatch.setattr(metrics_module, "_collect_hooks", [])
    monkeypatch.setattr(metrics_module, "_collect_state", {"last": 0.0})
    calls: list[str] = []

    async def broken() -> None:
        calls.append("broken")
        raise RuntimeError("redis down")

    async def healthy() -> None:
        calls.append("healthy")

    register_collect_hook(broken)
    register_collect_hook(healthy)
    response = await metrics_endpoint(_fake_request())

    assert response.status_code == 200
    assert calls == ["broken", "healthy"]


@pytest.mark.skipif(not _AVAILABLE, reason="prometheus_client not installed")
def test_track_websocket_counts_open_connections() -> None:
    gauge = metrics_module.WEBSOCKET_CONNECTIONS.labels(kind="pytest")
    before = gauge._value.get()
    with track_websocket("pytest"):
        assert gauge._value.get() == before + 1
    assert gauge._value.get() == before
