import logging
import uuid
from typing import Any

from sqlmodel import Session

from app.core.i18n import t
from app.core.permissions import is_admin as _is_admin
from app.exceptions import BadRequestError, ConflictError, PermissionDeniedError
from app.repositories import governance as governance_repo
from app.services.proxmox import proxmox_service

# 受保護的初始快照名稱由 reset_service（負責建立／還原它）統一定義
from app.services.resource.reset_service import INIT_SNAPSHOT_NAME
from app.services.resource.snapshot_capability import (
    SnapshotCapabilityDict,
    get_snapshot_capability,
    require_snapshot_available,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _snapshot_max_count(session: Session) -> int:
    return int(
        governance_repo.get_governance_config(session=session).student_snapshot_max_count
    )


def get_capability(
    *, vmid: int, resource_info: dict[str, Any]
) -> SnapshotCapabilityDict:
    """這台機器當下能否使用快照；前端據此顯示或隱藏整組快照功能。"""
    return get_snapshot_capability(
        resource_info["node"], vmid, resource_info["type"]
    )


def list_snapshots(*, vmid: int, resource_info: dict) -> list[dict]:
    node = resource_info["node"]
    resource_type = resource_info["type"]

    snapshots = proxmox_service.list_snapshots(node, vmid, resource_type)

    return [
        {
            "name": snap.get("name", ""),
            "description": snap.get("description"),
            "snaptime": snap.get("snaptime"),
            "vmstate": snap.get("vmstate"),
        }
        for snap in snapshots
        if snap.get("name") != "current"
    ]


def create_snapshot(
    *,
    session: Session,
    vmid: int,
    snapname: str,
    description: str | None,
    vmstate: bool,
    resource_info: dict,
    user: Any,
) -> dict:
    node = resource_info["node"]
    resource_type = resource_info["type"]

    require_snapshot_available(node, vmid, resource_type)
    if snapname == INIT_SNAPSHOT_NAME and not _is_admin(user):
        raise BadRequestError(t("snapshot.reservedName"))
    if not _is_admin(user):
        existing = proxmox_service.list_snapshots(node, vmid, resource_type)
        countable = [
            s
            for s in existing
            if s.get("name") not in ("current", INIT_SNAPSHOT_NAME)
        ]
        limit = _snapshot_max_count(session)
        if len(countable) >= limit:
            raise ConflictError(
                t("snapshot.maxCountReached", limit=limit)
            )

    params: dict[str, Any] = {"snapname": snapname}
    if description:
        params["description"] = description
    if resource_type == "qemu" and vmstate:
        params["vmstate"] = 1

    task = proxmox_service.create_snapshot(node, vmid, resource_type, **params)

    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_create",
        details=f"Created snapshot '{snapname}': {description or 'No description'}",
    )

    logger.info(f"Snapshot '{snapname}' created for {vmid}")
    return {
        "message": f"Snapshot '{snapname}' created successfully",
        "task_id": task,
    }


def delete_snapshot(
    *,
    session: Session,
    vmid: int,
    snapname: str,
    resource_info: dict,
    user: Any,
) -> dict:
    node = resource_info["node"]
    resource_type = resource_info["type"]

    require_snapshot_available(node, vmid, resource_type)
    if snapname == INIT_SNAPSHOT_NAME and not _is_admin(user):
        raise PermissionDeniedError(t("snapshot.initProtected"))

    task = proxmox_service.delete_snapshot(node, vmid, resource_type, snapname)

    audit_service.log_action(
        session=session,
        user_id=user.id,
        vmid=vmid,
        action="snapshot_delete",
        details=f"Deleted snapshot '{snapname}'",
    )

    logger.info(f"Snapshot '{snapname}' deleted for {vmid}")
    return {
        "message": f"Snapshot '{snapname}' deleted successfully",
        "task_id": task,
    }


def rollback_snapshot(
    *,
    session: Session,
    vmid: int,
    snapname: str,
    resource_info: dict,
    user_id: uuid.UUID,
) -> dict:
    node = resource_info["node"]
    resource_type = resource_info["type"]

    require_snapshot_available(node, vmid, resource_type)
    task = proxmox_service.rollback_snapshot(node, vmid, resource_type, snapname)

    audit_service.log_action(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action="snapshot_rollback",
        details=f"Rolled back to snapshot '{snapname}'",
    )

    logger.info(f"Rolled back to snapshot '{snapname}' for {vmid}")
    return {
        "message": f"Rolled back to snapshot '{snapname}' successfully",
        "task_id": task,
    }
