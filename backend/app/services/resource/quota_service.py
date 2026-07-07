"""配額計算與執法 I/O 層（純函式在 quota_policy）。

用量來源：DB resources 表決定擁有的 vmid 與台數；specs 取自 PVE
cluster/resources（maxcpu / maxmem / maxdisk，單次呼叫）。
PVE 不可用時 fail-open（記 warning、放行），不阻斷 provisioning。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session, col, select

from app.exceptions import AppError, ConflictError
from app.models import GroupMember, QuotaScope, Resource, ResourceQuota
from app.services.proxmox import proxmox_service
from app.services.resource.quota_policy import (
    EffectiveQuota,
    QuotaUsage,
    check_quota_delta,
    resolve_effective_quota,
)

logger = logging.getLogger(__name__)

_MIB = 1024**2
_GIB = 1024**3


def _quota_rows_for_user(
    session: Session, user_id: uuid.UUID
) -> tuple[ResourceQuota | None, list[ResourceQuota]]:
    user_quota = session.exec(
        select(ResourceQuota).where(
            ResourceQuota.scope == QuotaScope.user,
            ResourceQuota.user_id == user_id,
        )
    ).first()
    group_ids = session.exec(
        select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    ).all()
    group_quotas: list[ResourceQuota] = []
    if group_ids:
        group_quotas = list(
            session.exec(
                select(ResourceQuota).where(
                    ResourceQuota.scope == QuotaScope.group,
                    col(ResourceQuota.group_id).in_(list(group_ids)),
                )
            ).all()
        )
    return user_quota, group_quotas


def _owned_vmids(session: Session, user_id: uuid.UUID) -> list[int]:
    return [
        int(v)
        for v in session.exec(
            select(Resource.vmid).where(Resource.user_id == user_id)
        ).all()
    ]


def get_effective_quota(session: Session, user_id: uuid.UUID) -> EffectiveQuota:
    user_quota, group_quotas = _quota_rows_for_user(session, user_id)
    return resolve_effective_quota(user_quota, group_quotas)


def get_usage(
    session: Session,
    user_id: uuid.UUID,
    *,
    cluster_resources: list[dict[str, Any]] | None = None,
) -> QuotaUsage:
    vmids = set(_owned_vmids(session, user_id))
    listing = (
        cluster_resources
        if cluster_resources is not None
        else proxmox_service.list_all_resources()
    )
    cores = memory_mb = disk_gb = 0
    for item in listing:
        if int(item.get("vmid") or 0) not in vmids:
            continue
        cores += int(item.get("maxcpu") or 0)
        memory_mb += int(item.get("maxmem") or 0) // _MIB
        disk_gb += int(item.get("maxdisk") or 0) // _GIB
    return QuotaUsage(
        cpu_cores=cores, memory_mb=memory_mb, disk_gb=disk_gb, instances=len(vmids)
    )


def check_quota(
    session: Session,
    user_id: uuid.UUID,
    *,
    delta_cores: int = 0,
    delta_memory_mb: int = 0,
    delta_disk_gb: int = 0,
    delta_instances: int = 0,
) -> None:
    """執法點呼叫；超限 raise ConflictError(409)。PVE 失敗 fail-open。"""
    quota = get_effective_quota(session, user_id)
    try:
        usage = get_usage(session, user_id)
    except Exception:
        logger.warning(
            "Quota usage lookup failed for user %s; skipping enforcement",
            user_id,
            exc_info=True,
        )
        return
    violations = check_quota_delta(
        usage,
        quota,
        delta_cores=delta_cores,
        delta_memory_mb=delta_memory_mb,
        delta_disk_gb=delta_disk_gb,
        delta_instances=delta_instances,
    )
    if violations:
        raise ConflictError("配額不足：" + "；".join(violations))


__all__ = [
    "AppError",
    "check_quota",
    "get_effective_quota",
    "get_usage",
]
