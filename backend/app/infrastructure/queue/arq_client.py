"""arq Redis 任務隊列連線管理。

App 端（FastAPI lifespan）呼叫 init/close 管理 enqueue 用的連線池；
worker 端由 arq CLI 依 WorkerSettings 自行建池。
"""

from __future__ import annotations

import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.features.ai.config import settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "skylab:tasks"

_pool: ArqRedis | None = None


def get_redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def init_arq_pool() -> None:
    global _pool
    if _pool is not None:
        return
    try:
        _pool = await create_pool(get_redis_settings())
        logger.info("arq pool connected: %s", settings.redis_url)
    except Exception as exc:
        logger.error("Failed to connect arq pool: %s", exc)
        _pool = None


async def get_arq_pool() -> ArqRedis:
    if _pool is None:
        await init_arq_pool()
    if _pool is None:
        raise RuntimeError(
            "arq pool is not available; check REDIS_URL / Redis connectivity"
        )
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        logger.info("arq pool closed")
