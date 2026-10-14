"""Bounded, best-effort AI accounting, isolated from authentication workers."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from app.core import metrics

logger = logging.getLogger(__name__)
MAX_ACTIVE = 4
MAX_INFLIGHT = 40
WAIT_SECONDS = 2.0


class UsageWriter:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=MAX_ACTIVE, thread_name_prefix="ai-usage"
        )
        self.pending: dict[Future[Any], float] = {}
        self.lock = threading.RLock()
        self.active = 0
        self.closed = False

    def _update(self) -> None:
        metrics.AI_USAGE_ACTIVE.set(self.active)
        metrics.AI_USAGE_PENDING.set(len(self.pending))
        metrics.AI_USAGE_OLDEST.set(
            max(0.0, time.monotonic() - min(self.pending.values()))
            if self.pending
            else 0
        )

    def submit(
        self, operation: Callable[[], Any], *, source: str
    ) -> Future[Any] | None:
        with self.lock:
            if self.closed or len(self.pending) >= MAX_INFLIGHT:
                metrics.AI_USAGE_WRITES.labels(source=source, result="dropped").inc()
                logger.warning(
                    "AI usage rejected: source=%s reason=%s",
                    source,
                    "shutdown" if self.closed else "backlog_full",
                )
                return None
            queued_at = time.monotonic()
            context = contextvars.copy_context()

            def write() -> None:
                with self.lock:
                    self.active += 1
                    self._update()
                metrics.AI_USAGE_SECONDS.labels(stage="queue").observe(
                    time.monotonic() - queued_at
                )
                started = time.monotonic()
                try:
                    result = context.run(operation)
                except Exception:
                    metrics.AI_USAGE_WRITES.labels(source=source, result="failed").inc()
                    logger.exception("AI usage write failed: source=%s", source)
                else:
                    metrics.AI_USAGE_WRITES.labels(
                        source=source,
                        result="failed" if result is False else "persisted",
                    ).inc()
                finally:
                    metrics.AI_USAGE_SECONDS.labels(stage="write").observe(
                        time.monotonic() - started
                    )
                    with self.lock:
                        self.active -= 1

            future = self.executor.submit(write)
            self.pending[future] = queued_at
            self._update()

            def completed(done: Future[Any]) -> None:
                # concurrent Future completes only after the actual DB thread exits.
                with self.lock:
                    self.pending.pop(done, None)
                    if done.cancelled():
                        metrics.AI_USAGE_WRITES.labels(
                            source=source, result="dropped"
                        ).inc()
                    self._update()

            future.add_done_callback(completed)
            return future

    async def close(self) -> None:
        with self.lock:
            self.closed = True
            futures = list(self.pending)
        try:
            if futures:
                await asyncio.wait(
                    [asyncio.wrap_future(f) for f in futures], timeout=WAIT_SECONDS
                )
        finally:
            # Also discard queued work when lifespan drain itself is cancelled.
            # Running threads retain their Session and capacity until completion.
            self.executor.shutdown(wait=False, cancel_futures=True)
            with self.lock:
                if self.pending:
                    logger.warning(
                        "AI usage shutdown has %d running writes", len(self.pending)
                    )


_writer: UsageWriter | None = None
_writer_lock = threading.Lock()


def get_usage_writer() -> UsageWriter:
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = UsageWriter()
        return _writer


def start_usage_writer() -> None:
    global _writer
    with _writer_lock:
        if _writer is not None and _writer.closed:
            with _writer.lock:
                if _writer.pending:
                    raise RuntimeError("Previous AI usage workers have not drained")
            _writer = None
    get_usage_writer()


async def close_usage_writer() -> None:
    if _writer is not None:
        await _writer.close()


async def write_usage(operation: Callable[[], Any], *, source: str) -> bool:
    future = get_usage_writer().submit(operation, source=source)
    if future is None:
        return False
    # Cancelling a request must not cancel or uncount the actual worker.
    await asyncio.shield(asyncio.wrap_future(future))
    return True
