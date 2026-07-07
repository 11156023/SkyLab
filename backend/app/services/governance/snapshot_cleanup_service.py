"""快照自動清理（E8）：掃描學生 VM，刪除超過保留天數的一般快照。

資格判定在 ``snapshot_cleanup_policy`` 純函式。每 tick 至多掃
``SNAPSHOT_CLEANUP_BATCH_SIZE`` 台，以 module-level vmid 游標輪替，
掃完一輪歸零重來。刪除後寫 audit log 並 email 通知 VM 擁有者
（email 失敗吞掉，絕不使排程 task 崩潰）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from sqlmodel import Session, select

from app.models import Resource, User, UserRole
from app.services.governance.snapshot_cleanup_policy import is_cleanup_eligible
from app.services.proxmox import proxmox_service
from app.services.user import audit_service
from app.utils import send_email

logger = logging.getLogger(__name__)

SNAPSHOT_CLEANUP_BATCH_SIZE = 20

_cursor_vmid: int = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pve_resource_map() -> dict[int, dict[str, Any]]:
    return {
        int(r["vmid"]): r
        for r in proxmox_service.list_all_resources()
        if r.get("vmid") is not None
    }


def _get_config(session: Session) -> Any:
    from app.repositories import governance as governance_repo  # noqa: PLC0415

    return governance_repo.get_governance_config(session=session)


def _list_scan_batch(session: Session, cursor: int, limit: int) -> list[Resource]:
    """學生擁有、vmid 大於游標的資源，一批最多 limit 台。"""
    stmt = (
        select(Resource)
        .join(User, User.id == Resource.user_id)  # type: ignore[arg-type]
        .where(User.role == UserRole.student, Resource.vmid > cursor)
        .order_by(Resource.vmid)  # type: ignore[arg-type]
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def _reset_cursor() -> None:
    global _cursor_vmid
    _cursor_vmid = 0


def _audit_and_notify(
    session: Session, resource: Resource, snapname: str, retention_days: int
) -> None:
    audit_service.log_action(
        session=session,
        user_id=None,
        vmid=resource.vmid,
        action="snapshot_delete",
        details=f"Auto-cleaned snapshot '{snapname}' (>{retention_days}d)",
        commit=False,
    )
    user = resource.user
    if user is None or not user.email:
        return
    try:
        send_email(
            email_to=str(user.email),
            subject=f"[SkyLab] 資源 VMID {resource.vmid} 的過期快照已自動清理",
            html_content=(
                f"<p>您的資源（VMID {resource.vmid}）快照 <b>{snapname}</b> "
                f"已超過保留天數（{retention_days} 天），系統已自動刪除。</p>"
                "<p>skylab-init 初始快照不受影響。</p>"
            ),
        )
    except Exception:
        logger.warning(
            "Failed to send snapshot cleanup email for vmid=%s", resource.vmid
        )


def process_snapshot_cleanup() -> int:
    """Scheduler tick：回傳本 tick 刪除的快照數。"""
    global _cursor_vmid
    try:
        deleted = 0
        now = _utc_now()
        from app.core.db import engine  # noqa: PLC0415 — 測試環境不一定有 DB

        with Session(engine) as session:
            config = _get_config(session)
            if not config.snapshot_cleanup_enabled:
                return 0
            batch = _list_scan_batch(
                session, _cursor_vmid, SNAPSHOT_CLEANUP_BATCH_SIZE
            )
            if not batch:
                _reset_cursor()
                return 0
            _cursor_vmid = int(batch[-1].vmid)
            pve_map = _pve_resource_map()

            for resource in batch:
                pve_info = pve_map.get(resource.vmid)
                if pve_info is None:
                    continue
                node = str(pve_info.get("node") or "")
                rtype: Literal["qemu", "lxc"] = (
                    "lxc" if str(pve_info.get("type") or "") == "lxc" else "qemu"
                )
                try:
                    snapshots = proxmox_service.list_snapshots(
                        node, resource.vmid, rtype
                    )
                    for snap in snapshots:
                        if not is_cleanup_eligible(
                            name=snap.get("name"),
                            snaptime=snap.get("snaptime"),
                            now=now,
                            retention_days=config.snapshot_retention_days,
                        ):
                            continue
                        proxmox_service.delete_snapshot(
                            node, resource.vmid, rtype, str(snap.get("name"))
                        )
                        _audit_and_notify(
                            session,
                            resource,
                            str(snap.get("name")),
                            config.snapshot_retention_days,
                        )
                        deleted += 1
                        session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Snapshot cleanup failed for vmid=%s", resource.vmid
                    )
        return deleted
    except Exception:
        logger.exception("process_snapshot_cleanup failed")
        return 0
