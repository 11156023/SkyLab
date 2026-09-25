from __future__ import annotations

import logging
import uuid

from sqlmodel import Session, select

from app.models import Resource, VMProvisioningStatus, VMRequest, VMRequestStatus
from app.repositories import resource as resource_repo
from app.repositories import vm_request as vm_request_repo
from app.services.proxmox import proxmox_service
from app.services.scheduling import policy as scheduling_policy

logger = logging.getLogger(__name__)


def find_existing_resource_for_request(
    *,
    session: Session,
    request: VMRequest,
) -> dict | None:
    """Find an unclaimed Proxmox guest matching an approved request.

    認領的前提是「這台機器沒有主人」：同名（完全相同）、同型別，而且
    Proxmox 上這個 VMID 在 ``resources`` 裡沒有屬於別人的紀錄。少了最後
    這條，兩位使用者填同一個 hostname 時，後面那張申請單會直接把別人的
    機器認領過去 —— 對方的機器連同資料就這樣換了主人。
    """
    expected_type = scheduling_policy.resource_type_for_request(request)
    expected_hostname = str(request.hostname or "").strip()
    if not expected_hostname:
        # 沒有可比對的名字就不認領，否則會撿到任何一台同型別機器
        return None
    claimed_vmids = {
        int(item.vmid)
        for item in session.exec(
            select(VMRequest).where(
                VMRequest.status == VMRequestStatus.approved,
                VMRequest.vmid.is_not(None),
                VMRequest.id != request.id,
            )
        ).all()
        if item.vmid is not None
    }
    for resource in proxmox_service.list_all_resources():
        if str(resource.get("type") or "") != expected_type:
            continue
        if str(resource.get("name") or "") != expected_hostname:
            continue
        vmid = int(resource.get("vmid"))
        if vmid in claimed_vmids:
            continue
        owner = _tracked_owner(session=session, vmid=vmid)
        if owner is not None and owner != request.user_id:
            logger.warning(
                "Refusing to adopt vmid=%s for request %s: it already belongs "
                "to user %s",
                vmid, request.id, owner,
            )
            continue
        # list_all_resources() 已依各連線自己的 pool 過濾，這裡不需再比對
        return resource
    return None


def _tracked_owner(*, session: Session, vmid: int) -> uuid.UUID | None:
    """這個 VMID 在 ``resources`` 裡的擁有者；沒有紀錄回 None。"""
    tracked: Resource | None = resource_repo.get_resource_by_vmid(
        session=session, vmid=vmid
    )
    return tracked.user_id if tracked is not None else None


def mark_request_runtime_error(
    *,
    session: Session,
    request_id,
    message: str,
) -> None:
    """Persist a provisioning failure while retaining capacity warnings."""
    request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
        for_update=True,
    )
    if not request:
        return
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=request.assigned_node,
        desired_node=request.desired_node,
        actual_node=request.actual_node,
        placement_strategy_used=request.placement_strategy_used,
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error=message[:500],
        commit=False,
    )
    if any(
        keyword in message.lower()
        for keyword in ("no feasible", "capacity", "no node", "cannot fit")
    ):
        request.resource_warning = message[:500]
        session.add(request)
    session.commit()


__all__ = ["find_existing_resource_for_request", "mark_request_runtime_error"]
