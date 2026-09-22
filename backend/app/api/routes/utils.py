from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db import engine
from app.infrastructure.redis.client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/utils", tags=["utils"])

_HEALTH_CHECK_TIMEOUT_SECONDS = 3.0
# health／readiness 是未驗證端點：例外字串可能夾帶主機名、連線字串、
# 帳號等內部資訊，對外只回固定代碼，細節一律進 log。
_UNAVAILABLE_DETAIL = "unavailable"
# ─── Health / Readiness ──────────────────────────────────────────────────────


class DependencyStatus(BaseModel):
    status: Literal["ok", "error", "skipped"]
    detail: str | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    overall: Literal["ok", "degraded", "error"]
    db: DependencyStatus
    redis: DependencyStatus
    proxmox: DependencyStatus


async def _check_db() -> DependencyStatus:
    loop = asyncio.get_running_loop()
    start = loop.time()

    def _ping() -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_ping), timeout=_HEALTH_CHECK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return DependencyStatus(status="error", detail="timeout")
    except Exception:  # noqa: BLE001
        logger.warning("Health check: DB ping failed", exc_info=True)
        return DependencyStatus(status="error", detail=_UNAVAILABLE_DETAIL)

    return DependencyStatus(
        status="ok", latency_ms=round((loop.time() - start) * 1000, 2)
    )


async def _check_redis() -> DependencyStatus:
    loop = asyncio.get_running_loop()
    start = loop.time()
    try:
        redis = await get_redis()
        if redis is None:
            return DependencyStatus(status="skipped", detail="redis disabled")
        await asyncio.wait_for(redis.ping(), timeout=_HEALTH_CHECK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return DependencyStatus(status="error", detail="timeout")
    except Exception:  # noqa: BLE001
        logger.warning("Health check: Redis ping failed", exc_info=True)
        return DependencyStatus(status="error", detail=_UNAVAILABLE_DETAIL)

    return DependencyStatus(
        status="ok", latency_ms=round((loop.time() - start) * 1000, 2)
    )


async def _check_proxmox() -> DependencyStatus:
    loop = asyncio.get_running_loop()
    start = loop.time()

    def _ping_nodes() -> int:
        # Lazy import to avoid circular dependencies at module load time.
        from app.infrastructure.proxmox.operations import list_nodes

        return len(list_nodes())

    try:
        node_count = await asyncio.wait_for(
            asyncio.to_thread(_ping_nodes), timeout=_HEALTH_CHECK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return DependencyStatus(status="error", detail="timeout")
    except Exception:  # noqa: BLE001
        logger.warning("Health check: Proxmox ping failed", exc_info=True)
        return DependencyStatus(status="error", detail=_UNAVAILABLE_DETAIL)

    return DependencyStatus(
        status="ok",
        detail=f"{node_count} node(s)",
        latency_ms=round((loop.time() - start) * 1000, 2),
    )


def _aggregate_overall(
    *statuses: DependencyStatus,
) -> Literal["ok", "degraded", "error"]:
    if any(s.status == "error" for s in statuses):
        return "error"
    if any(s.status == "skipped" for s in statuses):
        return "degraded"
    return "ok"


@router.get("/health-check/")
async def health_check() -> bool:
    """Backwards-compatible liveness probe — returns True if the process is up."""
    return True


@router.get("/health/", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    """Detailed health: checks DB, Redis, and Proxmox connectivity in parallel."""
    db_status, redis_status, proxmox_status = await asyncio.gather(
        _check_db(), _check_redis(), _check_proxmox()
    )

    overall = _aggregate_overall(db_status, redis_status, proxmox_status)

    if db_status.status == "error":
        response.status_code = 503

    return HealthResponse(
        overall=overall,
        db=db_status,
        redis=redis_status,
        proxmox=proxmox_status,
    )


@router.get("/readiness/", response_model=HealthResponse)
async def readiness(response: Response) -> HealthResponse:
    """Kubernetes-style readiness probe — fails (503) if DB or Proxmox unreachable."""
    db_status, redis_status, proxmox_status = await asyncio.gather(
        _check_db(), _check_redis(), _check_proxmox()
    )

    overall = _aggregate_overall(db_status, redis_status, proxmox_status)

    if db_status.status == "error" or proxmox_status.status == "error":
        response.status_code = 503

    return HealthResponse(
        overall=overall,
        db=db_status,
        redis=redis_status,
        proxmox=proxmox_status,
    )
