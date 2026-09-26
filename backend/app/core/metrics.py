"""Prometheus metrics：HTTP 請求、排程器心跳、佇列與依賴元件。

Lazily imports ``prometheus_client``. If the dep is missing the middleware
no-ops and ``/metrics`` returns 503, so this module is safe to wire up
unconditionally.

只放「應用程式本身」的指標。Proxmox 節點／VM 的資源用量由 PVE 內建的
Metric Server 直接推到 InfluxDB，不經過後端（見 docs/monitoring.md）。

``/metrics`` 抓取前會先跑 ``register_collect_hook`` 註冊的非同步 hook，讓
service 層把「抓取當下才有意義」的 gauge（佇列長度、DB/Redis 是否可用…）
更新好；core 不直接 import service。
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dep
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep
    _AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain"

    class _Stub:  # type: ignore[no-redef]
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def labels(self, *_: Any, **__: Any) -> _Stub:
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            pass

        def dec(self, *_: Any, **__: Any) -> None:
            pass

        def set(self, *_: Any, **__: Any) -> None:
            pass

        def observe(self, *_: Any, **__: Any) -> None:
            pass

        def clear(self) -> None:
            pass

    Counter = Gauge = Histogram = _Stub  # type: ignore[misc, assignment]
    CollectorRegistry = _Stub  # type: ignore[misc, assignment]

    def generate_latest(*_: Any, **__: Any) -> bytes:  # type: ignore[misc]
        return b""


REGISTRY = CollectorRegistry() if _AVAILABLE else None

# 沒對到任何路由的請求（掃描器亂打的路徑）一律歸成同一個 label，
# 否則每個隨機路徑都會變成一條新的時間序列，Prometheus 記憶體會被吃光。
UNMATCHED_PATH = "<unmatched>"

# ─── HTTP ─────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("method", "path", "status"),
    registry=REGISTRY,
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    labelnames=("method", "path"),
    registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being processed",
    registry=REGISTRY,
)

# ─── WebSocket ────────────────────────────────────────────────────────────

WEBSOCKET_CONNECTIONS = Gauge(
    "skylab_websocket_connections",
    "Open WebSocket connections by kind (vnc, terminal, jobs, classroom...)",
    labelnames=("kind",),
    registry=REGISTRY,
)

# ─── 排程器／背景迴圈心跳 ─────────────────────────────────────────────────

SCHEDULER_TASK_RUNS = Counter(
    "skylab_scheduler_task_runs_total",
    "Scheduled task executions by result",
    labelnames=("loop", "task", "result"),
    registry=REGISTRY,
)
SCHEDULER_TASK_DURATION = Histogram(
    "skylab_scheduler_task_duration_seconds",
    "Scheduled task execution time",
    labelnames=("loop", "task"),
    registry=REGISTRY,
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)
SCHEDULER_TASK_LAST_SUCCESS = Gauge(
    "skylab_scheduler_task_last_success_timestamp_seconds",
    "Unix time of the last successful run of a scheduled task",
    labelnames=("loop", "task"),
    registry=REGISTRY,
)
SCHEDULER_TASK_CONSECUTIVE_FAILURES = Gauge(
    "skylab_scheduler_task_consecutive_failures",
    "Consecutive failed runs of a scheduled task (0 = last run succeeded)",
    labelnames=("loop", "task"),
    registry=REGISTRY,
)
SCHEDULER_LOOP_LAST_TICK = Gauge(
    "skylab_scheduler_loop_last_tick_timestamp_seconds",
    "Unix time of the last tick of a background loop in this process",
    labelnames=("loop",),
    registry=REGISTRY,
)
SCHEDULER_LOOP_IS_LEADER = Gauge(
    "skylab_scheduler_loop_is_leader",
    "1 when this process currently holds the loop's leader lock",
    labelnames=("loop",),
    registry=REGISTRY,
)

# ─── 依賴元件與佇列（抓取當下由 collect hook 更新） ──────────────────────

DEPENDENCY_UP = Gauge(
    "skylab_dependency_up",
    "1 when the dependency answered the last health probe",
    labelnames=("component",),
    registry=REGISTRY,
)
DEPENDENCY_LATENCY = Gauge(
    "skylab_dependency_latency_seconds",
    "Latency of the last health probe",
    labelnames=("component",),
    registry=REGISTRY,
)
QUEUE_JOBS = Gauge(
    "skylab_queue_jobs",
    "Jobs waiting in the arq queue",
    labelnames=("queue",),
    registry=REGISTRY,
)
TASK_RECORDS = Gauge(
    "skylab_task_records",
    "Background task records by status (queued/running = current; failed_24h/succeeded_24h = last 24 hours)",
    labelnames=("status",),
    registry=REGISTRY,
)


@contextmanager
def track_websocket(kind: str) -> Iterator[None]:
    """包住 WebSocket handler，連線期間計入 ``skylab_websocket_connections``。"""
    gauge = WEBSOCKET_CONNECTIONS.labels(kind=kind)
    gauge.inc()
    try:
        yield
    finally:
        gauge.dec()


def _route_template(scope: Scope) -> str:
    """Return the parameterised route template (e.g. /resources/{vmid})."""
    route = scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return UNMATCHED_PATH


class PrometheusMiddleware:
    """ASGI middleware that records request count + latency per route."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        start = time.perf_counter()
        status_holder: dict[str, int] = {"code": 500}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = int(message.get("status", 500))
            await send(message)

        REQUESTS_IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            REQUESTS_IN_PROGRESS.dec()
            duration = time.perf_counter() - start
            path = _route_template(scope)
            REQUEST_COUNT.labels(method=method, path=path, status=str(status_holder["code"])).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(duration)


# ─── /metrics ─────────────────────────────────────────────────────────────

CollectHook = Callable[[], Awaitable[None]]
_collect_hooks: list[CollectHook] = []
# hook 本身可能打 DB／Redis；多個 Prometheus 同時抓時每次都跑一遍沒有意義
_COLLECT_MIN_INTERVAL_SECONDS = 5.0
_COLLECT_TIMEOUT_SECONDS = 8.0
_collect_state: dict[str, float] = {"last": 0.0}


def register_collect_hook(hook: CollectHook) -> None:
    if hook not in _collect_hooks:
        _collect_hooks.append(hook)


async def _run_collect_hooks() -> None:
    now = time.monotonic()
    if now - _collect_state["last"] < _COLLECT_MIN_INTERVAL_SECONDS:
        return
    _collect_state["last"] = now
    for hook in list(_collect_hooks):
        try:
            await asyncio.wait_for(hook(), timeout=_COLLECT_TIMEOUT_SECONDS)
        except Exception:
            logger.warning("metrics collect hook %s failed", getattr(hook, "__name__", hook), exc_info=True)


def is_authorized(request: Request) -> bool:
    """``/metrics`` 系列端點共用的 Bearer token 驗證（METRICS_TOKEN 留空時不驗證）。"""
    expected = settings.METRICS_TOKEN
    if not expected:
        return True
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip().encode(), expected.encode())


async def metrics_endpoint(request: Request) -> Response:
    if not _AVAILABLE:
        return PlainTextResponse(
            "prometheus_client not installed", status_code=503
        )
    if not is_authorized(request):
        return PlainTextResponse(
            "unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    await _run_collect_hooks()
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
