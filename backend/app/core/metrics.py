"""Prometheus-style metrics for HTTP requests.

Lazily imports ``prometheus_client``. If the dep is missing the middleware
no-ops and ``/metrics`` returns 503, so this module is safe to wire up
unconditionally.

To enable, add ``prometheus-client`` to backend dependencies.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

try:  # pragma: no cover - optional dep
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
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

        def observe(self, *_: Any, **__: Any) -> None:
            pass

    Counter = Histogram = _Stub  # type: ignore[misc, assignment]
    CollectorRegistry = _Stub  # type: ignore[misc, assignment]

    def generate_latest(*_: Any, **__: Any) -> bytes:  # type: ignore[misc]
        return b""


REGISTRY = CollectorRegistry() if _AVAILABLE else None

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


def _route_template(scope: Scope) -> str:
    """Return the parameterised route template (e.g. /resources/{vmid})."""
    route = scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return scope.get("path", "unknown")


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

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            path = _route_template(scope)
            REQUEST_COUNT.labels(method=method, path=path, status=str(status_holder["code"])).inc()
            REQUEST_LATENCY.labels(method=method, path=path).observe(duration)


async def metrics_endpoint(_: Request) -> Response:
    if not _AVAILABLE:
        return PlainTextResponse(
            "prometheus_client not installed", status_code=503
        )
    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
