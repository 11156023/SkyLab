from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/utils", tags=["utils"])

_HEALTH_CHECK_TIMEOUT_SECONDS = 3.0
# health／readiness 是未驗證端點：例外字串可能夾帶主機名、連線字串、
# 帳號等內部資訊，對外只回固定代碼，細節一律進 log。
_UNAVAILABLE_DETAIL = "unavailable"
# ─── Health / Readiness ──────────────────────────────────────────────────────


@router.get("/health-check/")
async def health_check() -> bool:
    """Backwards-compatible liveness probe — returns True if the process is up."""
    return True

