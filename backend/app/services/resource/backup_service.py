"""機器備份／還原：快照不能用的機器以 vzdump 備份當還原點。

為什麼需要：磁碟在厚配置 LVM 之類不支援快照的 storage 上時（LXC 連 PVE 9 的
volume-chain 快照都用不了），使用者沒有任何「動手前先存檔」的手段。備份比
快照慢得多、佔空間也大，所以是獨立功能而不是偷偷頂替快照：

- 每個叢集（PVE 連線）由管理員指定 ``backup_storage``；沒設定就不開放。
- 備份 storage 往往和機構自己的排程備份共用，同一個 VMID 底下會混著別人的
  備份。SkyLab 建的備份在 notes 帶標記 ``skylab-backup:<機器建立時間>``，清單、
  還原、刪除都**只認標記相符的**——不會動到機構的備份，VMID 被重用時也看
  不到前一台機器的備份。
- 備份與還原都要好幾分鐘，走 arq 背景任務（202），進度在任務中心看。
- 機器刪除時由 ``purge_backups_for_removed_machine`` 一併清掉（PVE 的 purge
  不會刪備份檔）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict

from sqlmodel import Session, col, select

from app.core.i18n import t
from app.core.permissions import is_admin
from app.exceptions import ConflictError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import get_proxmox_settings_for_node
from app.infrastructure.queue import enqueue_task_sync
from app.models.task_record import TaskRecord, TaskRecordStatus
from app.repositories import governance as governance_repo
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.resource import reset_service
from app.services.resource._guest_helpers import GuestType, resource_type
from app.services.user import audit_service

logger = logging.getLogger(__name__)

TASK_BACKUP = "resource.backup"
TASK_RESTORE = "resource.restore"

# 同一台機器同時間只允許一個會改動機器狀態的任務（重置／備份／還原）
_EXCLUSIVE_TASK_TYPES = (reset_service.TASK_RESET, TASK_BACKUP, TASK_RESTORE)

# arq 任務上限 3600 秒（見 tasks.py）；PVE 端的等待要留時間給收尾與稽核
BACKUP_WAIT_SECONDS = 3300.0
RESTORE_WAIT_SECONDS = 3300.0
STOP_WAIT_SECONDS = 180.0

NOTE_MARKER = "skylab-backup"
DESCRIPTION_MAX_LENGTH = 120
_MARKER_RE = re.compile(rf"^{NOTE_MARKER}:(\d+)(?: \| (.*))?$")
# notes-template 會展開反斜線跳脫與 {{變數}}；控制字元會把單行備註拆成多行
_DESCRIPTION_STRIP_RE = re.compile(r"[\x00-\x1f\x7f\\{}]")

UnavailableReason = Literal["not_configured", "storage_unavailable", "unknown"]


class BackupCapabilityDict(TypedDict):
    available: bool
    reason: UnavailableReason | None
    requires_shutdown: bool
    max_count: int | None


class BackupInfoDict(TypedDict):
    volid: str
    created_at: int | None
    size: int | None
    description: str | None


# ─── 標記與清單 ───────────────────────────────────────────────────────────────


def _owner_token(created_at: datetime) -> int:
    """機器建立時間（秒）：同一個 VMID 被重用時，新舊兩台的值不同。"""
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return int(created_at.timestamp())


def clean_description(description: str | None) -> str | None:
    if not description:
        return None
    cleaned = " ".join(_DESCRIPTION_STRIP_RE.sub(" ", description).split())
    return cleaned[:DESCRIPTION_MAX_LENGTH].strip() or None


def build_notes(owner_token: int, description: str | None) -> str:
    cleaned = clean_description(description)
    marker = f"{NOTE_MARKER}:{owner_token}"
    return f"{marker} | {cleaned}" if cleaned else marker


def parse_notes(notes: object) -> tuple[int, str | None] | None:
    """解析備份備註；不是 SkyLab 建的（沒有標記）回 None。"""
    if not isinstance(notes, str):
        return None
    lines = notes.strip().splitlines()
    if not lines:
        return None
    match = _MARKER_RE.match(lines[0].strip())
    if match is None:
        return None
    return int(match.group(1)), (match.group(2) or "").strip() or None


def _backup_storage(node: str) -> str | None:
    storage = get_proxmox_settings_for_node(node).backup_storage
    return storage.strip() if storage and storage.strip() else None


def _require_storage(node: str) -> str:
    storage = _backup_storage(node)
    if storage is None:
        raise ConflictError(t("backup.not_configured"))
    try:
        usable = proxmox_service.storage_accepts_backups(node, storage)
    except Exception as exc:
        logger.warning(
            "Backup storage check failed for node=%s storage=%s", node, storage,
            exc_info=True,
        )
        raise ProxmoxError(t("backup.storage_unavailable")) from exc
    if not usable:
        raise ConflictError(t("backup.storage_unavailable"))
    return storage


def _require_owner_token(session: Session, vmid: int) -> int:
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise NotFoundError(t("backup.resource_not_tracked"))
    return _owner_token(resource.created_at)


def _owned_backups(
    node: str, storage: str, vmid: int, owner_token: int
) -> list[BackupInfoDict]:
    """這台機器（同一個建立時間）由 SkyLab 建立的備份，新的在前。"""
    owned: list[BackupInfoDict] = []
    for item in proxmox_service.list_backups(node, storage, vmid):
        parsed = parse_notes(item.get("notes"))
        if parsed is None or parsed[0] != owner_token:
            continue
        volid = item.get("volid")
        if not isinstance(volid, str) or not volid:
            continue
        ctime, size = item.get("ctime"), item.get("size")
        owned.append(
            {
                "volid": volid,
                "created_at": int(ctime) if isinstance(ctime, (int, float)) else None,
                "size": int(size) if isinstance(size, (int, float)) else None,
                "description": parsed[1],
            }
        )
    owned.sort(key=lambda b: b["created_at"] or 0, reverse=True)
    return owned


def _require_owned_backup(
    node: str, storage: str, vmid: int, owner_token: int, volid: str
) -> BackupInfoDict:
    """使用者送來的 volid 只拿來比對；之後一律用清單裡的那個值。"""
    for backup in _owned_backups(node, storage, vmid, owner_token):
        if backup["volid"] == volid:
            return backup
    raise NotFoundError(t("backup.not_found"))


# ─── 可用性 ───────────────────────────────────────────────────────────────────


def _backup_mode(node: str, vmid: int, rtype: GuestType) -> Literal["snapshot", "stop"]:
    """VM 一律線上備份；LXC 只有磁碟支援快照時才能線上備份，否則要先停機。"""
    if rtype == "qemu":
        return "snapshot"
    try:
        return "snapshot" if proxmox_service.has_snapshot_feature(node, vmid, rtype) else "stop"
    except Exception:
        return "stop"


def _max_count(session: Session, user: Any) -> int | None:
    if is_admin(user):
        return None
    return int(
        governance_repo.get_governance_config(session=session).student_backup_max_count
    )


def get_capability(
    *, session: Session, vmid: int, resource_info: dict[str, Any], user: Any
) -> BackupCapabilityDict:
    """這台機器能不能用備份；前端據此決定要不要顯示備份分頁。"""
    node = str(resource_info["node"])
    rtype = resource_type(resource_info)

    def _unavailable(reason: UnavailableReason) -> BackupCapabilityDict:
        return {
            "available": False,
            "reason": reason,
            "requires_shutdown": False,
            "max_count": None,
        }

    try:
        storage = _backup_storage(node)
        if storage is None:
            return _unavailable("not_configured")
        if not proxmox_service.storage_accepts_backups(node, storage):
            return _unavailable("storage_unavailable")
    except Exception:
        logger.warning(
            "Backup capability check failed for vmid=%s on node=%s", vmid, node,
            exc_info=True,
        )
        return _unavailable("unknown")
    return {
        "available": True,
        "reason": None,
        "requires_shutdown": _backup_mode(node, vmid, rtype) == "stop",
        "max_count": _max_count(session, user),
    }


def list_backups(
    *, session: Session, vmid: int, resource_info: dict[str, Any]
) -> list[BackupInfoDict]:
    node = str(resource_info["node"])
    storage = _require_storage(node)
    return _owned_backups(node, storage, vmid, _require_owner_token(session, vmid))


# ─── 入列 ─────────────────────────────────────────────────────────────────────


def _has_active_machine_task(session: Session, vmid: int) -> bool:
    """這台機器是否已有排隊中／執行中的重置、備份或還原任務。"""
    rows = session.exec(
        select(TaskRecord).where(
            col(TaskRecord.task_type).in_(_EXCLUSIVE_TASK_TYPES),
            col(TaskRecord.status).in_(
                [TaskRecordStatus.queued, TaskRecordStatus.running]
            ),
        )
    ).all()
    for row in rows:
        raw = row.payload
        try:
            payload = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            task_vmid = payload.get("vmid") if isinstance(payload, dict) else None
            if task_vmid is not None and int(task_vmid) == vmid:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _enqueue_exclusive(
    session: Session,
    *,
    vmid: int,
    user: Any,
    task_type: str,
    payload: dict[str, Any],
    action: str,
    details: str,
) -> str:
    """交易鎖內檢查「沒有進行中的任務」再入列；回傳 TaskRecord id。

    與重置共用同一把 advisory xact lock（見 reset_service._lock_reset_enqueue），
    稽核紀錄跟著寫入 TaskRecord 的那次 commit 一起落地。
    """
    reset_service._lock_reset_enqueue(session, vmid)
    if _has_active_machine_task(session, vmid):
        session.rollback()
        raise ConflictError(t("backup.task_running"))
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action=action,
        details=details,
        commit=False,
    )
    record = enqueue_task_sync(
        session=session, task_type=task_type, user_id=user.id, payload=payload
    )
    if record is None:
        raise ConflictError(t("backup.task_running"))
    return str(record.id)


def start_backup(
    session: Session,
    *,
    vmid: int,
    resource_info: dict[str, Any],
    user: Any,
    description: str | None,
) -> str:
    """驗證後把備份入列；回傳任務 id。"""
    node = str(resource_info["node"])
    rtype = resource_type(resource_info)
    storage = _require_storage(node)
    owner_token = _require_owner_token(session, vmid)

    limit = _max_count(session, user)
    if limit is not None:
        existing = _owned_backups(node, storage, vmid, owner_token)
        if len(existing) >= limit:
            raise ConflictError(t("backup.max_count_reached", limit=limit))

    return _enqueue_exclusive(
        session,
        vmid=vmid,
        user=user,
        task_type=TASK_BACKUP,
        payload={
            "vmid": vmid,
            "node": node,
            "rtype": rtype,
            "storage": storage,
            "notes": build_notes(owner_token, description),
            "user_id": str(user.id),
        },
        action="backup_create",
        details=f"Requested backup to storage '{storage}'",
    )


def start_restore(
    session: Session,
    *,
    vmid: int,
    resource_info: dict[str, Any],
    user: Any,
    volid: str,
) -> str:
    """驗證這份備份確實屬於這台機器後把還原入列；回傳任務 id。"""
    node = str(resource_info["node"])
    rtype = resource_type(resource_info)
    storage = _require_storage(node)
    backup = _require_owned_backup(
        node, storage, vmid, _require_owner_token(session, vmid), volid
    )
    return _enqueue_exclusive(
        session,
        vmid=vmid,
        user=user,
        task_type=TASK_RESTORE,
        payload={
            "vmid": vmid,
            "node": node,
            "rtype": rtype,
            "volid": backup["volid"],
            "user_id": str(user.id),
        },
        action="backup_restore",
        details=f"Requested restore from backup '{backup['volid']}'",
    )


def delete_backup(
    *,
    session: Session,
    vmid: int,
    resource_info: dict[str, Any],
    user: Any,
    volid: str,
) -> dict[str, str]:
    """刪除一份備份（同步）；備份／還原進行中時拒絕，避免刪到正在讀的檔案。"""
    node = str(resource_info["node"])
    storage = _require_storage(node)
    backup = _require_owned_backup(
        node, storage, vmid, _require_owner_token(session, vmid), volid
    )
    if _has_active_machine_task(session, vmid):
        raise ConflictError(t("backup.task_running"))
    proxmox_service.delete_backup(node, storage, backup["volid"])
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="backup_delete",
        details=f"Deleted backup '{backup['volid']}'",
    )
    logger.info("Backup '%s' deleted for vmid=%s", backup["volid"], vmid)
    return {"message": "備份已刪除"}


# ─── 背景任務本體（worker 端）─────────────────────────────────────────────────


def _audit_task(vmid: int, user_id: uuid.UUID, *, action: str, ok: bool, detail: str) -> None:
    """背景任務內寫 audit（獨立 session；失敗吞掉）。"""
    from app.core.db import engine

    logger.log(
        logging.INFO if ok else logging.WARNING,
        "Backup task audit for vmid=%s action=%s ok=%s: %s",
        vmid, action, ok, detail,
    )
    try:
        with Session(engine) as session:
            audit_service.log_action(
                session=session,
                user_id=user_id,
                vmid=vmid,
                action=action,
                details=detail,
            )
    except Exception:
        logger.warning("Failed to audit backup task for vmid=%s", vmid, exc_info=True)


def _payload_rtype(payload: dict[str, Any]) -> GuestType:
    return "lxc" if payload.get("rtype") == "lxc" else "qemu"


def run_backup_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """worker 端 handler：對這台機器做一次備份。"""
    vmid = int(payload["vmid"])
    node = str(payload["node"])
    rtype = _payload_rtype(payload)
    storage = str(payload["storage"])
    user_id = uuid.UUID(str(payload["user_id"]))
    mode = _backup_mode(node, vmid, rtype)
    logger.info(
        "Backup task %s started for vmid=%s (storage=%s, mode=%s)",
        task_id, vmid, storage, mode,
    )
    try:
        proxmox_service.create_backup(
            node,
            vmid,
            storage,
            mode=mode,
            notes=str(payload["notes"]),
            wait_timeout_seconds=BACKUP_WAIT_SECONDS,
        )
    except Exception as exc:
        _audit_task(
            vmid, user_id, action="backup_create", ok=False,
            detail=f"Backup failed: {exc}",
        )
        logger.exception("Backup failed for vmid=%s", vmid)
        raise
    _audit_task(
        vmid, user_id, action="backup_create", ok=True,
        detail=f"Backup created on storage '{storage}' (mode={mode})",
    )
    return {"vmid": vmid, "mode": mode}


def _lxc_restore_target(node: str, vmid: int) -> tuple[str | None, bool]:
    """LXC 還原要明確指定的兩件事：rootfs 所在的 storage、是否為非特權容器。

    都取自機器「目前」的設定——還原是把同一台機器的內容換回去，不該順便
    改變它放在哪裡或權限模式。
    """
    config = proxmox_service.get_config(node, vmid, "lxc")
    unprivileged = str(config.get("unprivileged") or "0") in ("1", "True", "true")
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, str) or ":" not in rootfs.split(",")[0]:
        return None, unprivileged
    return rootfs.split(":", 1)[0].strip() or None, unprivileged


def run_restore_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """worker 端 handler：記電源狀態 → 停機 → 以備份覆蓋還原 → 原狀態恢復。"""
    vmid = int(payload["vmid"])
    node = str(payload["node"])
    rtype = _payload_rtype(payload)
    volid = str(payload["volid"])
    user_id = uuid.UUID(str(payload["user_id"]))
    logger.info("Restore task %s started for vmid=%s from %s", task_id, vmid, volid)

    was_running = False
    try:
        status = proxmox_service.get_status(node, vmid, rtype)
        was_running = str(status.get("status") or "").lower() == "running"
        restore_storage: str | None = None
        unprivileged: bool | None = None
        if rtype == "lxc":
            restore_storage, unprivileged = _lxc_restore_target(node, vmid)
        if was_running:
            proxmox_service.control(
                node, vmid, rtype, "stop", wait_timeout_seconds=STOP_WAIT_SECONDS
            )
        proxmox_service.restore_backup(
            node,
            vmid,
            rtype,
            volid,
            storage=restore_storage,
            unprivileged=unprivileged,
            wait_timeout_seconds=RESTORE_WAIT_SECONDS,
        )
    except Exception as exc:
        _audit_task(
            vmid, user_id, action="backup_restore", ok=False,
            detail=f"Restore failed: {exc}",
        )
        logger.exception("Restore failed for vmid=%s", vmid)
        if was_running:
            # 還原沒做成：盡量把機器開回原本的狀態（已被還原弄壞的話這步也會失敗）
            try:
                proxmox_service.control(node, vmid, rtype, "start")
            except Exception:
                logger.warning(
                    "Could not restart vmid=%s after failed restore", vmid,
                    exc_info=True,
                )
        raise

    if was_running:
        proxmox_service.control(node, vmid, rtype, "start")
        reset_service._sync_lxc_platform_key_after_start(node, vmid, rtype)
    _audit_task(
        vmid, user_id, action="backup_restore", ok=True,
        detail=f"Restored from backup '{volid}' (was_running={was_running})",
    )
    return {"vmid": vmid}


# ─── 機器刪除時的清理 ─────────────────────────────────────────────────────────


def purge_backups_for_removed_machine(*, node: str, vmid: int) -> int:
    """機器已從 PVE 刪除後，清掉它留下的 SkyLab 備份；best-effort，絕不拋出。

    PVE 刪機器（含 purge）不會刪備份檔，而 VMID 會被重用。這裡把該 VMID 底下
    所有帶 SkyLab 標記的備份都刪掉（機器已不存在，任何一份都不再有主人）；
    沒標記的是機構自己的備份，不碰。回傳刪掉的份數。
    """
    try:
        storage = _backup_storage(node)
        if storage is None:
            return 0
        items = proxmox_service.list_backups(node, storage, vmid)
    except Exception:
        logger.warning(
            "Could not list backups to purge for removed vmid=%s", vmid, exc_info=True
        )
        return 0

    removed = 0
    for item in items:
        volid = item.get("volid")
        if parse_notes(item.get("notes")) is None or not isinstance(volid, str):
            continue
        try:
            proxmox_service.delete_backup(node, storage, volid)
            removed += 1
        except Exception:
            logger.warning(
                "Failed to purge backup '%s' of removed vmid=%s", volid, vmid,
                exc_info=True,
            )
    if removed:
        logger.info("Purged %d backup(s) of removed vmid=%s", removed, vmid)
    return removed


__all__ = [
    "TASK_BACKUP",
    "TASK_RESTORE",
    "build_notes",
    "clean_description",
    "delete_backup",
    "get_capability",
    "list_backups",
    "parse_notes",
    "purge_backups_for_removed_machine",
    "run_backup_task",
    "run_restore_task",
    "start_backup",
    "start_restore",
]
