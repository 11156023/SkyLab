"""一鍵環境重置（E1）：rollback 到受保護的 skylab-init 初始快照。

- ``ensure_init_snapshot``：provision 完成點呼叫，best-effort；失敗只記
  warning（該 VM 之後「重置不可用」，可由老師/admin 補建）。
- ``start_reset``：API 進入點，驗證前置條件後丟背景任務（202）。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from sqlmodel import Session

from app.exceptions import BadRequestError, ConflictError
from app.infrastructure.worker import background_tasks
from app.services.proxmox import proxmox_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

INIT_SNAPSHOT_NAME = "skylab-init"
INIT_SNAPSHOT_DESCRIPTION = "SkyLab 初始快照（受保護）"
INIT_SNAPSHOT_WAIT_SECONDS = 120.0


def _rtype(resource_info: dict[str, Any]) -> Literal["qemu", "lxc"]:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _has_init_snapshot(node: str, vmid: int, rtype: Literal["qemu", "lxc"]) -> bool:
    snapshots = proxmox_service.list_snapshots(node, vmid, rtype)
    return any(s.get("name") == INIT_SNAPSHOT_NAME for s in snapshots)


def ensure_init_snapshot(vmid: int) -> bool:
    """Provision 完成點 hook；失敗不阻斷 provision。"""
    try:
        info = proxmox_service.find_resource(vmid)
        node = str(info["node"])
        rtype = _rtype(info)
        if _has_init_snapshot(node, vmid, rtype):
            return True
        proxmox_service.create_snapshot(
            node,
            vmid,
            rtype,
            wait_timeout_seconds=INIT_SNAPSHOT_WAIT_SECONDS,
            snapname=INIT_SNAPSHOT_NAME,
            description=INIT_SNAPSHOT_DESCRIPTION,
        )
        logger.info("Init snapshot created for vmid=%s", vmid)
        return True
    except Exception:
        logger.warning(
            "Init snapshot failed for vmid=%s (reset unavailable until"
            " an instructor re-creates it)",
            vmid,
            exc_info=True,
        )
        return False


def create_init_snapshot(
    session: Session, *, vmid: int, resource_info: dict[str, Any], user: Any
) -> dict[str, str]:
    """老師/admin 為舊 VM 補建初始快照；已存在回 409。"""
    node = str(resource_info["node"])
    rtype = _rtype(resource_info)
    if _has_init_snapshot(node, vmid, rtype):
        raise ConflictError("初始快照 skylab-init 已存在")
    proxmox_service.create_snapshot(
        node,
        vmid,
        rtype,
        wait_timeout_seconds=INIT_SNAPSHOT_WAIT_SECONDS,
        snapname=INIT_SNAPSHOT_NAME,
        description=INIT_SNAPSHOT_DESCRIPTION,
    )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_create",
        details="Created protected init snapshot skylab-init",
    )
    return {"message": "初始快照已建立", "snapname": INIT_SNAPSHOT_NAME}


def _audit_reset(vmid: int, user_id: uuid.UUID, *, ok: bool, detail: str) -> None:
    """背景任務內寫 audit（獨立 session；失敗吞掉）。"""
    from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB

    logger.log(
        logging.INFO if ok else logging.WARNING,
        "Reset audit for vmid=%s ok=%s: %s",
        vmid,
        ok,
        detail,
    )
    try:
        with Session(engine) as session:
            audit_service.log_action(
                session=session,
                user_id=user_id,
                vmid=vmid,
                action="snapshot_rollback",
                details=detail,
            )
    except Exception:
        logger.warning("Failed to audit reset for vmid=%s", vmid, exc_info=True)


def _run_reset(
    vmid: int, node: str, rtype: Literal["qemu", "lxc"], user_id: uuid.UUID
) -> None:
    """背景任務本體：記電源狀態 → 強制停機 → rollback → 原狀態恢復。"""
    try:
        status = proxmox_service.get_status(node, vmid, rtype)
        was_running = str(status.get("status") or "").lower() == "running"
        if was_running:
            proxmox_service.control(node, vmid, rtype, "stop")
        proxmox_service.rollback_snapshot(node, vmid, rtype, INIT_SNAPSHOT_NAME)
        if was_running:
            proxmox_service.control(node, vmid, rtype, "start")
        _audit_reset(
            vmid, user_id, ok=True,
            detail=f"Reset to {INIT_SNAPSHOT_NAME} (was_running={was_running})",
        )
        logger.info("Reset vmid=%s to init snapshot", vmid)
    except Exception as exc:
        _audit_reset(vmid, user_id, ok=False, detail=f"Reset failed: {exc}")
        logger.exception("Reset failed for vmid=%s", vmid)
        raise


def start_reset(
    session: Session, *, vmid: int, resource_info: dict[str, Any], user: Any
) -> str:
    node = str(resource_info["node"])
    rtype = _rtype(resource_info)
    if not _has_init_snapshot(node, vmid, rtype):
        raise BadRequestError(
            "此資源沒有 skylab-init 初始快照，無法重置；請老師或管理員先補建"
        )
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_rollback",
        details="Requested reset to init snapshot",
    )
    task_id = background_tasks.submit_sync(
        _run_reset,
        vmid,
        node,
        rtype,
        user.id,
        name=f"reset-vm:{vmid}",
        task_id=f"reset-{vmid}",
    )
    return task_id
