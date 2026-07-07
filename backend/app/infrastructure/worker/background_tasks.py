"""Lightweight in-process fire-and-forget background task runner.

Use cases:
- API endpoints that need to kick off slow work (provision/delete a VM)
  and return immediately (HTTP 202) without blocking the request handler.
- Long-running infra calls (Proxmox clone, SSH deploy) that should not
  occupy the request worker.

Features:
- Sync handlers are dispatched via ``asyncio.to_thread`` so they don't
  block the event loop.
- A semaphore caps concurrent in-flight tasks so a flood of requests
  cannot overwhelm Proxmox / the DB pool.
- Tasks can opt into automatic **retry with exponential backoff** on
  failure (``max_retries`` / ``retry_delay`` / ``retry_backoff``).
- Tasks are tracked by ``task_id`` (caller-provided or auto-generated)
  and can be **cancelled** before they start or between retries.
- Lifespan shutdown awaits in-flight tasks so we don't drop work on
  graceful stop.
- Exceptions inside background tasks are logged but never propagate; the
  caller already received an HTTP response.

Cancellation semantics:
- ``cancel(task_id)`` cancels the underlying ``asyncio.Task``.
- This is **effective** when the task is queued behind the semaphore
  or sleeping between retry attempts.
- It is **best-effort** while a sync handler is actively executing in
  the worker thread (Python cannot interrupt sync code mid-Proxmox-API
  call). Domain code should treat cancellation as advisory and prefer
  idempotent operations.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENCY = 8
_MAX_RETRY_DELAY_SECONDS = 300.0


@dataclass
class TaskInfo:
    id: str
    name: str
    submitted_at: datetime
    max_retries: int
    attempt: int = 1
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BackgroundTaskRunner:
    def __init__(self, *, max_concurrency: int = _DEFAULT_MAX_CONCURRENCY) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._info: dict[str, TaskInfo] = {}
        self._shutting_down = False
        # Captured at init() time during app lifespan so submit_sync()
        # can be called from sync routes running in the threadpool.
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Bind the runner to the main event loop.

        Must be called from within the loop (e.g. in lifespan startup).
        Required so that ``submit_sync`` works when invoked from a
        threadpool worker (FastAPI sync routes), where there is no
        running loop in the current thread.
        """
        self._loop = loop or asyncio.get_running_loop()

    # ──────────────────────────────────────────────────────────────────
    # submit
    # ──────────────────────────────────────────────────────────────────

    def submit(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
        task_id: str | None = None,
    ) -> str:
        """Schedule a coroutine to run in the background.

        Retries are not supported here (a coroutine can only be awaited
        once). For retryable work prefer ``submit_sync`` or
        ``submit_factory``.
        """
        if self._shutting_down:
            logger.warning(
                "BackgroundTaskRunner is shutting down; rejecting task %s", name
            )
            _close_quietly(coro)
            return ""

        tid = task_id or str(uuid.uuid4())
        if tid in self._tasks:
            logger.warning(
                "Background task with id=%s already scheduled; skipping duplicate", tid
            )
            _close_quietly(coro)
            return tid

        info = TaskInfo(
            id=tid,
            name=name or "anonymous",
            submitted_at=_utc_now(),
            max_retries=0,
        )

        async def _wrapper() -> None:
            async with self._semaphore:
                try:
                    await coro
                except asyncio.CancelledError:
                    logger.info("Background task '%s' cancelled", info.name)
                    raise
                except Exception as exc:
                    info.last_error = str(exc)[:500]
                    logger.exception("Background task '%s' failed", info.name)

        return self._spawn(tid, info, _wrapper())

    def submit_sync(
        self,
        func: Callable[..., Any],
        *args: Any,
        name: str | None = None,
        task_id: str | None = None,
        max_retries: int = 0,
        retry_delay: float = 5.0,
        retry_backoff: float = 2.0,
        **kwargs: Any,
    ) -> str:
        """Schedule a sync function to run in a worker thread.

        Optional retry on exception with exponential backoff.
        ``retry_delay`` is multiplied by ``retry_backoff`` after each
        failed attempt (capped at 5 minutes).
        """
        return self.submit_factory(
            lambda: asyncio.to_thread(func, *args, **kwargs),
            name=name or getattr(func, "__name__", "anonymous"),
            task_id=task_id,
            max_retries=max_retries,
            retry_delay=retry_delay,
            retry_backoff=retry_backoff,
        )

    def submit_factory(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        name: str,
        task_id: str | None = None,
        max_retries: int = 0,
        retry_delay: float = 5.0,
        retry_backoff: float = 2.0,
        bypass_semaphore: bool = False,
    ) -> str:
        """Schedule a coroutine factory; supports retries.

        ``coro_factory`` must produce a fresh awaitable on each call so
        retries can re-invoke the underlying work.

        ``bypass_semaphore=True`` skips the runner's global concurrency
        semaphore — for task classes that manage their own dedicated
        semaphore (e.g. provisioning clones). Without this, tasks queued
        on a dedicated semaphore would still pin global slots and starve
        lightweight tasks (emails, state sync).
        """
        if self._shutting_down:
            logger.warning("BackgroundTaskRunner is shutting down; rejecting task %s", name)
            return ""

        tid = task_id or str(uuid.uuid4())
        if tid in self._tasks:
            logger.warning(
                "Background task with id=%s already scheduled; skipping duplicate", tid
            )
            return tid

        info = TaskInfo(
            id=tid,
            name=name,
            submitted_at=_utc_now(),
            max_retries=max_retries,
        )

        async def _runner() -> None:
            gate: Any = (
                contextlib.nullcontext() if bypass_semaphore else self._semaphore
            )
            async with gate:
                attempt = 0
                delay = max(0.0, retry_delay)
                while True:
                    info.attempt = attempt + 1
                    try:
                        await coro_factory()
                        return
                    except asyncio.CancelledError:
                        logger.info(
                            "Background task '%s' cancelled (attempt %d)",
                            info.name, info.attempt,
                        )
                        raise
                    except Exception as exc:
                        info.last_error = str(exc)[:500]
                        attempt += 1
                        if attempt > max_retries:
                            logger.exception(
                                "Background task '%s' failed after %d attempt(s)",
                                info.name, info.attempt,
                            )
                            return
                        logger.warning(
                            "Background task '%s' failed (attempt %d/%d): %s; retrying in %.1fs",
                            info.name, info.attempt, max_retries + 1, exc, delay,
                        )
                        try:
                            await asyncio.sleep(delay)
                        except asyncio.CancelledError:
                            logger.info(
                                "Background task '%s' cancelled while waiting for retry",
                                info.name,
                            )
                            raise
                        delay = min(delay * retry_backoff, _MAX_RETRY_DELAY_SECONDS)

        return self._spawn(tid, info, _runner())

    def _spawn(self, task_id: str, info: TaskInfo, coro: Awaitable[Any]) -> str:
        def _create_and_register() -> asyncio.Task[Any]:
            t = asyncio.create_task(coro, name=info.name)
            self._tasks[task_id] = t
            self._info[task_id] = info

            def _cleanup(_: asyncio.Task[Any]) -> None:
                self._tasks.pop(task_id, None)
                self._info.pop(task_id, None)

            t.add_done_callback(_cleanup)
            return t

        try:
            asyncio.get_running_loop()
            _create_and_register()
            return task_id
        except RuntimeError:
            pass

        # No running loop in this thread (e.g. called from a sync FastAPI
        # route running in the anyio threadpool). Schedule the task on
        # the main loop in a thread-safe manner.
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.error(
                "Background runner has no bound event loop; dropping task '%s'."
                " Did init_background_runner() run during lifespan startup?",
                info.name,
            )
            _close_quietly(coro)
            return ""
        loop.call_soon_threadsafe(_create_and_register)
        return task_id

    # ──────────────────────────────────────────────────────────────────
    # cancel / inspect
    # ──────────────────────────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """Cancel a tracked task.

        Returns True if the task existed and a cancel was issued.
        Effective immediately when the task is queued or sleeping;
        best-effort while sync work is actively running on a worker thread.
        """
        task = self._tasks.get(task_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def cancel_by_name(self, name: str) -> int:
        """Cancel all tracked tasks whose name matches; returns count cancelled."""
        cancelled = 0
        for tid, info in list(self._info.items()):
            if info.name == name and self.cancel(tid):
                cancelled += 1
        return cancelled

    def is_active(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return task is not None and not task.done()

    def list_tasks(self) -> list[TaskInfo]:
        return list(self._info.values())

    # ──────────────────────────────────────────────────────────────────
    # shutdown
    # ──────────────────────────────────────────────────────────────────

    async def shutdown(self, *, timeout: float = 30.0) -> None:
        """Wait for in-flight tasks to finish (up to ``timeout`` seconds)."""
        self._shutting_down = True
        if not self._tasks:
            return
        logger.info(
            "Waiting for %d background task(s) to finish before shutdown",
            len(self._tasks),
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*list(self._tasks.values()), return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "Timed out waiting for %d background task(s); cancelling",
                len(self._tasks),
            )
            for t in list(self._tasks.values()):
                t.cancel()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _close_quietly(coro: Awaitable[Any]) -> None:
    try:
        coro.close()  # type: ignore[union-attr]
    except Exception:
        # Ignore errors when closing an already closed or non-closable coroutine
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singleton + helpers
# ──────────────────────────────────────────────────────────────────────────────

_runner: BackgroundTaskRunner | None = None


def init_background_runner(
    *, max_concurrency: int = _DEFAULT_MAX_CONCURRENCY
) -> BackgroundTaskRunner:
    global _runner
    _runner = BackgroundTaskRunner(max_concurrency=max_concurrency)
    try:
        _runner.bind_loop()
    except RuntimeError:
        # Called outside a running loop; submit_sync from threadpool
        # routes will fail until bind_loop() is called from within the
        # event loop.
        logger.warning(
            "init_background_runner() called outside a running event loop;"
            " call runner.bind_loop() from within the loop before serving"
            " sync routes that use submit_sync()."
        )
    return _runner


async def shutdown_background_runner(*, timeout: float = 30.0) -> None:
    global _runner
    if _runner is None:
        return
    await _runner.shutdown(timeout=timeout)
    _runner = None


def get_runner() -> BackgroundTaskRunner | None:
    return _runner


def submit(
    coro: Awaitable[Any],
    *,
    name: str | None = None,
    task_id: str | None = None,
) -> str:
    """Submit an awaitable to the global background runner."""
    if _runner is None:
        logger.error(
            "Background runner not initialized; dropping task %s. "
            "Did you call init_background_runner() in lifespan?",
            name,
        )
        _close_quietly(coro)
        return ""
    return _runner.submit(coro, name=name, task_id=task_id)


def submit_sync(
    func: Callable[..., Any],
    *args: Any,
    name: str | None = None,
    task_id: str | None = None,
    max_retries: int = 0,
    retry_delay: float = 5.0,
    retry_backoff: float = 2.0,
    **kwargs: Any,
) -> str:
    """Submit a sync callable to the global background runner."""
    if _runner is None:
        logger.error(
            "Background runner not initialized; dropping task %s. "
            "Did you call init_background_runner() in lifespan?",
            name or getattr(func, "__name__", "anonymous"),
        )
        return ""
    return _runner.submit_sync(
        func,
        *args,
        name=name,
        task_id=task_id,
        max_retries=max_retries,
        retry_delay=retry_delay,
        retry_backoff=retry_backoff,
        **kwargs,
    )


def submit_factory(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    name: str,
    task_id: str | None = None,
    max_retries: int = 0,
    retry_delay: float = 5.0,
    retry_backoff: float = 2.0,
    bypass_semaphore: bool = False,
) -> str:
    """Submit a coroutine factory to the global background runner."""
    if _runner is None:
        logger.error(
            "Background runner not initialized; dropping task %s. "
            "Did you call init_background_runner() in lifespan?",
            name,
        )
        return ""
    return _runner.submit_factory(
        coro_factory,
        name=name,
        task_id=task_id,
        max_retries=max_retries,
        retry_delay=retry_delay,
        retry_backoff=retry_backoff,
        bypass_semaphore=bypass_semaphore,
    )


def cancel(task_id: str) -> bool:
    """Cancel a tracked background task by id."""
    if _runner is None:
        return False
    return _runner.cancel(task_id)


def is_active(task_id: str) -> bool:
    if _runner is None:
        return False
    return _runner.is_active(task_id)


def list_tasks() -> list[TaskInfo]:
    if _runner is None:
        return []
    return _runner.list_tasks()
