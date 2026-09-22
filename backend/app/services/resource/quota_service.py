"""配額計算與執法 I/O 層（純函式在 quota_policy）。

用量來源：DB resources 表中的個人資源決定 vmid 與台數；班級持有的
教學資源不計入學生個人配額。specs 取自 PVE cluster/resources
（maxcpu / maxmem / maxdisk，單次呼叫）。
PVE 不可用時 fail-open（記 warning、放行），不阻斷 provisioning。
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlmodel import Session, col, select

from app.core.i18n import t
from app.exceptions import AppError, ConflictError
from app.models import (
    QuotaConfig,
    Resource,
    ResourceQuota,
    VMRequest,
    VMRequestStatus,
)
from app.models.base import get_datetime_utc
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
_QUOTA_CONFIG_ID = 1


def _global_quota_row(session: Session) -> QuotaConfig | None:
    """純讀取全域預設配額 singleton。

    刻意不 lazy-create：這條路徑會被 check_quota 在 provisioning 途中呼叫，
    一旦在此 commit 就會把呼叫端未完成的交易一起提交。列不存在時由
    quota_policy 退回內建預設。
    """
    return session.get(QuotaConfig, _QUOTA_CONFIG_ID)


def get_global_quota(session: Session) -> QuotaConfig:
    """管理 API 用：取得 singleton，不存在則以內建預設建立。"""
    config = _global_quota_row(session)
    if config is None:
        config = QuotaConfig(id=_QUOTA_CONFIG_ID)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def update_global_quota(session: Session, data: dict[str, Any]) -> QuotaConfig:
    """partial 更新全域預設配額；None 與未知欄位一律忽略。"""
    config = get_global_quota(session)
    for key, value in data.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)
    now = get_datetime_utc()
    # Windows 的系統時鐘可能讓連續兩次 datetime.now() 取得相同值；API 的
    # updated_at 必須保持單調遞增，否則快取與前端變更偵測會漏掉這次更新。
    if config.updated_at is not None and now <= config.updated_at:
        now = config.updated_at + timedelta(microseconds=1)
    config.updated_at = now
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


def _quota_for_user(session: Session, user_id: uuid.UUID) -> ResourceQuota | None:
    return session.exec(
        select(ResourceQuota).where(ResourceQuota.user_id == user_id)
    ).first()


def _owned_vmids(session: Session, user_id: uuid.UUID) -> list[int]:
    return [
        int(v)
        for v in session.exec(
            select(Resource.vmid).where(
                Resource.user_id == user_id,
                Resource.allocation_scope == "personal",
            )
        ).all()
    ]


def get_effective_quota(session: Session, user_id: uuid.UUID) -> EffectiveQuota:
    return resolve_effective_quota(
        _quota_for_user(session, user_id), _global_quota_row(session)
    )


# provisioning 預設值（與 provisioning_service 的 `or 8` / `or 20` 一致）
_DEFAULT_LXC_DISK_GB = 8
_DEFAULT_VM_DISK_GB = 20


def request_specs(request: Any) -> tuple[int, int, int]:
    """申請單會佔用的 (cores, memory_mb, disk_gb)。"""
    if str(getattr(request, "resource_type", "") or "") == "lxc":
        disk_gb = int(getattr(request, "rootfs_size", None) or _DEFAULT_LXC_DISK_GB)
    else:
        disk_gb = int(getattr(request, "disk_size", None) or _DEFAULT_VM_DISK_GB)
    return (
        int(getattr(request, "cores", 0) or 0),
        int(getattr(request, "memory", 0) or 0),
        disk_gb,
    )


def _reserved_by_requests(
    session: Session,
    user_id: uuid.UUID,
    *,
    exclude_request_id: uuid.UUID | None = None,
) -> tuple[int, int, int]:
    """尚未佈建的申請單已經預約掉的資源。

    待審核／已核准但還沒拿到 vmid 的申請單，在 PVE 上還看不到，但核准之後
    一定會變成機器。不計入的話，使用者可以一次送十張單把配額整個繞過去。
    """
    statement = select(VMRequest).where(
        VMRequest.user_id == user_id,
        col(VMRequest.status).in_(
            [VMRequestStatus.pending, VMRequestStatus.approved]
        ),
        col(VMRequest.vmid).is_(None),
    )
    if exclude_request_id is not None:
        statement = statement.where(VMRequest.id != exclude_request_id)
    cores = memory_mb = disk_gb = 0
    for request in session.exec(statement).all():
        req_cores, req_memory, req_disk = request_specs(request)
        cores += req_cores
        memory_mb += req_memory
        disk_gb += req_disk
    return cores, memory_mb, disk_gb


def get_usage(
    session: Session,
    user_id: uuid.UUID,
    *,
    cluster_resources: list[dict[str, Any]] | None = None,
    exclude_request_id: uuid.UUID | None = None,
) -> QuotaUsage:
    """已佈建機器（PVE 實況）＋ 尚未佈建的申請單（DB 預約）。

    ``exclude_request_id`` 給「正要佈建這張單」的呼叫端把自己扣掉，
    否則同一張單會被算兩次（一次預約、一次增量）。
    """
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
    reserved_cores, reserved_memory, reserved_disk = _reserved_by_requests(
        session, user_id, exclude_request_id=exclude_request_id
    )
    return QuotaUsage(
        cpu_cores=cores + reserved_cores,
        memory_mb=memory_mb + reserved_memory,
        disk_gb=disk_gb + reserved_disk,
        instances=len(vmids),
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
        raise ConflictError(t("quota.exceeded", violations="；".join(violations)))


def check_quota_for_provision(session: Session, request: Any) -> None:
    """真正要開機器前的配額檢查（排程器 provisioning 路徑用）。

    這張申請單自己已經算在 ``get_usage`` 的「預約」裡，所以先把它排除，
    再用它的實際規格當增量；否則同一張單會被重複計算而永遠過不了。
    與 ``check_quota`` 一樣，PVE 查詢失敗時 fail-open（不阻斷佈建）。
    """
    try:
        quota = get_effective_quota(session, request.user_id)
        usage = get_usage(session, request.user_id, exclude_request_id=request.id)
    except Exception:
        logger.warning(
            "Quota usage lookup failed for user %s; skipping enforcement",
            request.user_id,
            exc_info=True,
        )
        return
    delta_cores, delta_memory_mb, delta_disk_gb = request_specs(request)
    violations = check_quota_delta(
        usage,
        quota,
        delta_cores=delta_cores,
        delta_memory_mb=delta_memory_mb,
        delta_disk_gb=delta_disk_gb,
        delta_instances=1,
    )
    if violations:
        raise ConflictError(t("quota.exceeded", violations="；".join(violations)))


__all__ = [
    "AppError",
    "check_quota",
    "check_quota_for_provision",
    "get_effective_quota",
    "get_global_quota",
    "get_usage",
    "request_specs",
    "update_global_quota",
]
