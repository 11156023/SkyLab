"""ARQ-based persistent task runner (optional).

Lazy-imports ``arq`` so the module is safe to import even when ARQ is
not installed. The default in-memory runner in
:mod:`app.infrastructure.worker.background_tasks` remains the active
runner; this module exists to make Phase 8 migration to a Redis-backed
durable queue a one-line swap.

Typical usage (when ``arq`` is installed)::

    from app.infrastructure.worker.arq_runner import enqueue, is_available

    if is_available():
        await enqueue("my_task", arg1, arg2)

When ARQ is unavailable, ``enqueue`` raises :class:`ArqUnavailableError`
so callers can fall back to the in-memory runner explicitly rather than
silently dropping tasks (see CLAUDE.md "no silent fallback" rule).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dep
    from arq.connections import RedisSettings, create_pool

    _AVAILABLE = True
except ImportError:  # pragma: no cover - optional dep
    RedisSettings = None  # type: ignore[assignment,misc]
    create_pool = None  # type: ignore[assignment]
    _AVAILABLE = False


class ArqUnavailableError(RuntimeError):
    """Raised when ARQ enqueue is attempted but ``arq`` package is missing."""


def is_available() -> bool:
    """Return True if the ``arq`` package is importable."""
    return _AVAILABLE


_pool: Any = None


async def get_pool(redis_url: str) -> Any:
    """Return a singleton ARQ Redis pool. Requires ``arq`` installed."""
    if not _AVAILABLE:
        raise ArqUnavailableError(
            "arq is not installed; install with `uv add arq` or use the in-memory runner",
        )
    global _pool
    if _pool is None:
        settings = RedisSettings.from_dsn(redis_url)  # type: ignore[union-attr]
        _pool = await create_pool(settings)  # type: ignore[misc]
        logger.info("ARQ Redis pool initialized")
    return _pool


async def enqueue(task_name: str, *args: Any, redis_url: str, **kwargs: Any) -> Any:
    """Enqueue a job onto the ARQ queue.

    Args:
        task_name: Registered ARQ function name.
        *args: Positional args forwarded to the worker function.
        redis_url: Redis DSN (e.g. ``redis://localhost:6379/0``).
        **kwargs: Keyword args forwarded to the worker function.

    Returns:
        The :class:`arq.jobs.Job` object.

    Raises:
        ArqUnavailableError: If ``arq`` is not installed.
    """
    pool = await get_pool(redis_url)
    return await pool.enqueue_job(task_name, *args, **kwargs)


async def shutdown() -> None:
    """Close the pool if any. Safe to call when ARQ is unavailable."""
    global _pool
    if _pool is not None:
        await _pool.close()  # type: ignore[union-attr]
        _pool = None
        logger.info("ARQ Redis pool closed")
