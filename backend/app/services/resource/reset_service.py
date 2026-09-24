"""一鍵環境重置（E1）：rollback 到受保護的 skylab-init 初始快照。

- ``ensure_init_snapshot``：provision 完成點呼叫，best-effort；失敗只記
  warning（該 VM 之後「重置不可用」，可由老師/admin 補建）。
- ``start_reset``：API 進入點，驗證前置條件後入列 arq 任務（202）。
- ``run_reset_task``：worker 端 handler 本體（見 ``resource/tasks.py``）。
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal

from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import BadRequestError, ConflictError
from app.infrastructure.queue import enqueue_task_sync
from app.services.proxmox import proxmox_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

TASK_RESET = "resource.reset"

INIT_SNAPSHOT_NAME = "skylab-init"
INIT_SNAPSHOT_DESCRIPTION = "SkyLab 初始快照（受保護）"
INIT_SNAPSHOT_WAIT_SECONDS = 120.0
INIT_SNAPSHOT_ATTEMPTS = 3
INIT_SNAPSHOT_RETRY_SECONDS = 10.0


def _rtype(resource_info: dict[str, Any]) -> Literal["qemu", "lxc"]:
    return "lxc" if str(resource_info.get("type") or "") == "lxc" else "qemu"


def _has_init_snapshot(node: str, vmid: int, rtype: Literal["qemu", "lxc"]) -> bool:
    snapshots = proxmox_service.list_snapshots(node, vmid, rtype)
    return any(s.get("name") == INIT_SNAPSHOT_NAME for s in snapshots)


def ensure_init_snapshot(vmid: int) -> bool:
    """Provision 完成點 hook；失敗不阻斷 provision。

    provision 尾聲常伴隨剛下發的 start（qmstart 持有 config lock），
    快照會被 PVE 以 lock timeout 拒絕，因此帶固定間隔重試。
    """
    for attempt in range(1, INIT_SNAPSHOT_ATTEMPTS + 1):
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
            if attempt < INIT_SNAPSHOT_ATTEMPTS:
                logger.info(
                    "Init snapshot attempt %d/%d failed for vmid=%s;"
                    " retrying in %.0fs",
                    attempt,
                    INIT_SNAPSHOT_ATTEMPTS,
                    vmid,
                    INIT_SNAPSHOT_RETRY_SECONDS,
                )
                time.sleep(INIT_SNAPSHOT_RETRY_SECONDS)
                continue
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
        raise ConflictError(t("reset.init_snapshot_exists"))
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


def _sync_lxc_platform_key_after_start(
    node: str, vmid: int, rtype: Literal["qemu", "lxc"]
) -> None:
    """Best-effort key repair after a snapshot rollback restarts an LXC."""
    if rtype != "lxc":
        return

    try:
        from app.core.db import engine  # noqa: PLC0415 — background task session
        from app.services.resource import resource_service  # noqa: PLC0415

        with Session(engine) as session:
            resource_service.ensure_lxc_platform_key(
                session=session,
                node=node,
                vmid=vmid,
            )
            resource_service.ensure_lxc_login_password(
                session=session,
                node=node,
                vmid=vmid,
                reapply_recorded=True,
            )
    except Exception:
        # Reset success must not be turned into a failure because a repair
        # attempt could not reach the guest or the database.
        logger.warning(
            "Failed to sync platform SSH key after LXC reset for vmid=%s",
            vmid,
            exc_info=True,
        )


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
            _sync_lxc_platform_key_after_start(node, vmid, rtype)
        _audit_reset(
            vmid, user_id, ok=True,
            detail=f"Reset to {INIT_SNAPSHOT_NAME} (was_running={was_running})",
        )
        logger.info("Reset vmid=%s to init snapshot", vmid)
    except Exception as exc:
        _audit_reset(vmid, user_id, ok=False, detail=f"Reset failed: {exc}")
        logger.exception("Reset failed for vmid=%s", vmid)
        raise


def run_reset_task(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
    """worker 端 handler：解包 payload 後執行重置，回傳結果寫入 TaskRecord。"""
    vmid = int(payload["vmid"])
    node = str(payload["node"])
    rtype: Literal["qemu", "lxc"] = "lxc" if payload.get("rtype") == "lxc" else "qemu"
    user_id = uuid.UUID(str(payload["user_id"]))
    logger.info("Reset task %s started for vmid=%s", task_id, vmid)
    _run_reset(vmid, node, rtype, user_id)
    return {"vmid": vmid}


def start_reset(
    session: Session, *, vmid: int, resource_info: dict[str, Any], user: Any
) -> str:
    """驗證前置條件後把重置入列；回傳 TaskRecord id（同時是 arq job id）。

    走 arq 而不是行程內背景任務：重置中途 API 重啟不會讓機器停在關機狀態，
    同一台機器的重複請求也由 TaskRecord／job id 在跨行程層級去重。
    """
    node = str(resource_info["node"])
    rtype = _rtype(resource_info)
    if not _has_init_snapshot(node, vmid, rtype):
        raise BadRequestError(t("reset.no_init_snapshot"))
    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_rollback",
        details="Requested reset to init snapshot",
    )
    record = enqueue_task_sync(
        session=session,
        task_type=TASK_RESET,
        user_id=user.id,
        payload={
            "vmid": vmid,
            "node": node,
            "rtype": rtype,
            "user_id": str(user.id),
        },
    )
    return str(record.id)
