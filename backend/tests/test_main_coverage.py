"""Coverage for app.main: security headers middleware, lifespan loops and WebSocket endpoints."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest
from fastapi import FastAPI

from app import main
from app.core.config import settings
from app.services.network import wireguard_service
from app.services.notification import web_push_service
from app.services.scheduling import vm_request_schedule_service


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only uvicorn loop patch")
def test_win_selector_factory_returns_selector_loop() -> None:
    assert main._win_selector_factory() is asyncio.SelectorEventLoop
    assert main._win_selector_factory(use_subprocess=True) is asyncio.SelectorEventLoop


async def _run_middleware(path: str, scope_type: str = "http") -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def inner_app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    middleware = main.SecurityHeadersMiddleware(inner_app)
    await middleware({"type": scope_type, "path": path}, receive, send)
    return sent


async def test_security_headers_docs_skip_csp_and_production_adds_hsts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")

    sent = await _run_middleware("/docs/oauth2-redirect")

    headers = dict(sent[0]["headers"])
    assert b"content-security-policy" not in headers
    assert b"x-frame-options" not in headers
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"strict-transport-security"].startswith(b"max-age=")
    assert sent[1] == {"type": "http.response.body", "body": b"ok"}


async def test_lifespan_starts_and_stops_scheduler_and_reconciler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    started: dict[str, asyncio.Event] = {}

    def _sync(name: str):
        def _fn(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return _fn

    def _async(name: str):
        async def _fn(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return _fn

    def _loop(name: str):
        started[name] = asyncio.Event()

        async def _fn(stop_event: asyncio.Event) -> None:
            started[name].set()
            await stop_event.wait()
            calls.append(f"{name}:stopped")

        return _fn

    async def _observe() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "start_ai_capacity", _sync("start_ai_capacity"))
    monkeypatch.setattr(main, "start_relay_runtime", _sync("start_relay_runtime"))
    monkeypatch.setattr(main, "configure_logging", _sync("configure_logging"))
    monkeypatch.setattr(main, "init_redis", _async("init_redis"))
    monkeypatch.setattr(main, "init_arq_pool", _async("init_arq_pool"))
    monkeypatch.setattr(main, "init_background_runner", _sync("init_background_runner"))
    monkeypatch.setattr(main, "_observe_runtime", _observe)
    monkeypatch.setattr(main, "shutdown_background_runner", _async("shutdown_runner"))
    monkeypatch.setattr(main, "close_ai_capacity", _async("close_ai_capacity"))
    monkeypatch.setattr(main, "close_relay_runtime", _async("close_relay_runtime"))
    monkeypatch.setattr(main, "close_ai_clients", _async("close_ai_clients"))
    monkeypatch.setattr(main, "close_arq_pool", _async("close_arq_pool"))
    monkeypatch.setattr(main, "close_redis", _async("close_redis"))
    monkeypatch.setattr(
        vm_request_schedule_service, "run_scheduler", _loop("scheduler")
    )
    monkeypatch.setattr(web_push_service, "run_push_notifier", _loop("push"))
    monkeypatch.setattr(wireguard_service, "run_reconciler", _loop("wireguard"))
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(settings, "WIREGUARD_RECONCILE_ENABLED", True)

    async with main.lifespan(FastAPI()):
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())), 5
        )
        assert set(started) == {"scheduler", "push", "wireguard"}

    for name in ("scheduler:stopped", "push:stopped", "wireguard:stopped"):
        assert name in calls
    assert calls[-1] == "close_redis"
    assert calls.index("shutdown_runner") < calls.index("close_ai_capacity")


@pytest.mark.parametrize(
    ("endpoint", "target", "args", "label"),
    [
        (main.websocket_vnc_proxy, "vnc_proxy", (5,), "vnc"),
        (main.websocket_terminal_proxy, "terminal_proxy", (6,), "terminal"),
        (main.websocket_jobs_proxy, "jobs_ws_proxy", (), "jobs"),
        (
            main.websocket_classroom_presence,
            "classroom_presence_proxy",
            (),
            "classroom",
        ),
        (
            main.websocket_classroom_watch,
            "classroom_watch_proxy",
            ("sess",),
            "classroom_watch",
        ),
        (
            main.websocket_course_progress,
            "course_progress_proxy",
            ("path-1",),
            "course_progress",
        ),
    ],
)
async def test_websocket_endpoints_delegate_with_tracking(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: Any,
    target: str,
    args: tuple[Any, ...],
    label: str,
) -> None:
    received: list[tuple[Any, ...]] = []
    tracked: list[str] = []

    async def _proxy(websocket: Any, *proxy_args: Any, **kwargs: Any) -> None:
        received.append((websocket, proxy_args, kwargs))

    class _Track:
        def __init__(self, name: str) -> None:
            tracked.append(name)

        def __enter__(self) -> None:
            return None

        def __exit__(self, *_exc: Any) -> None:
            tracked.append(f"{label}:exit")

    monkeypatch.setattr(main, target, _proxy)
    monkeypatch.setattr(main, "track_websocket", _Track)
    websocket = object()

    await endpoint(websocket, *args, token="tk")

    assert tracked == [label, f"{label}:exit"]
    assert received[0][0] is websocket
    assert received[0][1] == args
    assert received[0][2]["token"] == "tk"
