"""Public AI ingress and DB capacity, separate from normal authentication."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, TypeVar

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import metrics
from app.core.config import settings

MAX_INGRESS = 64
MAX_DB_ACTIVE = 8
T = TypeVar("T")
logger = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None
_pending: set[Future[Any]] = set()
_lock = threading.RLock()
_closed = False


class AIIngressBusy(Exception):
    pass


def start_ai_capacity() -> None:
    global _executor, _closed
    with _lock:
        if _closed:
            if _pending:
                raise RuntimeError("Previous public AI DB workers have not drained")
            _executor = None
            _closed = False


async def close_ai_capacity() -> None:
    global _closed
    with _lock:
        _closed = True
        pending = list(_pending)
        executor = _executor
    try:
        if pending:
            await asyncio.wait([asyncio.wrap_future(f) for f in pending], timeout=2.0)
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        with _lock:
            if _pending:
                logger.warning(
                    "Public AI shutdown has %d running DB operations", len(_pending)
                )


async def run_ai_db(operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    global _executor
    started = time.monotonic()
    context = contextvars.copy_context()
    cancelled = threading.Event()

    def run() -> T:
        # Keep cancelled queue entries counted until the executor removes them.
        # Future.cancel() alone leaves their WorkItems in its unbounded queue.
        if cancelled.is_set():
            raise asyncio.CancelledError
        metrics.AUTH_SECONDS.labels(transport="ai", stage="queue").observe(
            time.monotonic() - started
        )
        before = time.monotonic()
        try:
            return context.run(operation, *args, **kwargs)
        finally:
            metrics.AUTH_SECONDS.labels(transport="ai", stage="db").observe(
                time.monotonic() - before
            )

    with _lock:
        if _closed or len(_pending) >= MAX_INGRESS:
            raise AIIngressBusy
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=MAX_DB_ACTIVE, thread_name_prefix="ai-auth"
            )
        future = _executor.submit(run)
        _pending.add(future)

        def done(completed: Future[Any]) -> None:
            with _lock:
                _pending.discard(completed)

        future.add_done_callback(done)
    wrapped = asyncio.wrap_future(future)

    def consume_exception(completed: asyncio.Future[Any]) -> None:
        if not completed.cancelled():
            completed.exception()

    wrapped.add_done_callback(consume_exception)
    try:
        return await asyncio.shield(wrapped)
    except asyncio.CancelledError:
        cancelled.set()
        raise


class AIIngressMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.active = 0
        self.prefix = f"{settings.API_V1_STR}/ai-proxy"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or not (path == self.prefix or path.startswith(self.prefix + "/"))
        ):
            await self.app(scope, receive, send)
            return
        if self.active >= MAX_INGRESS:
            metrics.AI_INGRESS_REJECTED.inc()
            response = JSONResponse(
                {
                    "error": {
                        "message": "Public AI ingress is busy",
                        "type": "server_error",
                        "code": "server_busy",
                    }
                },
                status_code=503,
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        self.active += 1
        metrics.AI_INGRESS_ACTIVE.set(self.active)
        try:
            await self.app(scope, receive, send)
        except AIIngressBusy:
            metrics.AI_INGRESS_REJECTED.inc()
            await JSONResponse(
                {
                    "error": {
                        "message": "Public AI authentication is busy",
                        "type": "server_error",
                        "code": "server_busy",
                    }
                },
                status_code=503,
                headers={"Retry-After": "1"},
            )(scope, receive, send)
        finally:
            self.active -= 1
            metrics.AI_INGRESS_ACTIVE.set(self.active)
