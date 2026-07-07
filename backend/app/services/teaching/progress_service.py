"""學生進度熱圖（E3）：聚合 cluster/resources，判定每台 VM 的活動狀態。

「stale（長期無動靜）」以當下狀態近似：uptime > 1h 且 CPU < 1%。
不查 RRD 歷史（控制成本，30 秒輪詢下已足夠）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.core.authorizers import require_group_access
from app.exceptions import NotFoundError
from app.models import Group, Resource
from app.repositories import group as group_repo
from app.schemas.teaching import HeatmapEntry

logger = logging.getLogger(__name__)

STALE_MIN_UPTIME_SECONDS = 3600
STALE_CPU_PERCENT = 1.0
IDLE_CPU_PERCENT = 10.0


def classify_activity(
    *, status: str, cpu_percent: float, uptime_seconds: int
) -> str:
    if status != "running":
        return "stopped"
    if uptime_seconds > STALE_MIN_UPTIME_SECONDS and cpu_percent < STALE_CPU_PERCENT:
        return "stale"
    if cpu_percent < IDLE_CPU_PERCENT:
        return "idle"
    return "running"


def _get_group_or_404(session: Session, group_id: uuid.UUID) -> Group:
    group = session.get(Group, group_id)
    if group is None:
        raise NotFoundError("Group not found")
    return group


def _resources_for_users(
    session: Session, user_ids: list[uuid.UUID]
) -> list[Resource]:
    if not user_ids:
        return []
    return list(
        session.exec(
            select(Resource).where(col(Resource.user_id).in_(user_ids))
        ).all()
    )


def get_heatmap(
    session: Session,
    *,
    group_id: uuid.UUID,
    user: Any,
    cluster_resources: list[dict[str, Any]],
) -> list[HeatmapEntry]:
    group = _get_group_or_404(session, group_id)
    require_group_access(user, group.owner_id)

    members = group_repo.get_group_members(session=session, group_id=group_id)
    member_by_id = {m.id: m for m in members}
    resources = _resources_for_users(session, list(member_by_id))
    listing: dict[int, dict[str, Any]] = {
        int(item["vmid"]): item
        for item in cluster_resources
        if item.get("vmid") is not None
    }

    entries: list[HeatmapEntry] = []
    for resource in resources:
        info = listing.get(resource.vmid, {})
        status = str(info.get("status") or "unknown")
        maxmem = float(info.get("maxmem") or 0) or 1.0
        cpu_percent = round(float(info.get("cpu") or 0.0) * 100.0, 1)
        mem_percent = round(float(info.get("mem") or 0.0) / maxmem * 100.0, 1)
        uptime = int(info.get("uptime") or 0)
        member = member_by_id.get(resource.user_id)
        entries.append(
            HeatmapEntry(
                vmid=resource.vmid,
                name=info.get("name"),
                owner_id=resource.user_id,
                owner_name=(member.full_name or member.email) if member else None,
                status=status,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                uptime_seconds=uptime,
                activity=classify_activity(
                    status=status,
                    cpu_percent=cpu_percent,
                    uptime_seconds=uptime,
                ),
            )
        )
    return sorted(entries, key=lambda e: e.vmid)
