from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.monitoring import system_health_service

router = APIRouter(prefix="/utils", tags=["utils"])

# ─── Health / Readiness ──────────────────────────────────────────────────────


@router.get("/health-check/")
async def health_check() -> bool:
    """Backwards-compatible liveness probe — returns True if the process is up."""
    return True


@router.get(
    "/health-check/ready",
    responses={503: {"description": "A core dependency (database or Redis) is down"}},
)
async def readiness_check() -> JSONResponse:
    """Readiness probe：DB 與 Redis 都連得上才回 200，否則 503。

    免登入，給負載平衡器／外部探測用；只回每個依賴的布林值，不帶錯誤
    細節（細節在管理員的 ``/monitoring/system-health``）。Redis 停用時該欄為
    null、不影響結果。
    """
    result = await asyncio.to_thread(system_health_service.readiness)
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(result, status_code=status_code, headers={"Cache-Control": "no-store"})
