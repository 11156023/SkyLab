from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/utils", tags=["utils"])

# ─── Health / Readiness ──────────────────────────────────────────────────────


@router.get("/health-check/")
async def health_check() -> bool:
    """Backwards-compatible liveness probe — returns True if the process is up."""
    return True

