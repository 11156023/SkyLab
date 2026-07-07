"""資源配額 API：admin 管理群組/個人配額；所有登入者查自己用量。"""

import logging
import uuid

from fastapi import APIRouter
from sqlmodel import select

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.exceptions import ConflictError, NotFoundError
from app.models import AuditAction, Group, QuotaScope, ResourceQuota, User
from app.schemas import (
    EffectiveQuotaPublic,
    QuotaUsagePublic,
    ResourceQuotaCreate,
    ResourceQuotaPublic,
    ResourceQuotaUpdate,
)
from app.schemas.common import Message
from app.services.resource import quota_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/quotas", tags=["quotas"])


def _to_public(session: SessionDep, quota: ResourceQuota) -> ResourceQuotaPublic:
    group_name = None
    user_email = None
    if quota.group_id is not None:
        group = session.get(Group, quota.group_id)
        group_name = group.name if group else None
    if quota.user_id is not None:
        user = session.get(User, quota.user_id)
        user_email = user.email if user else None
    return ResourceQuotaPublic(
        id=quota.id,
        scope=quota.scope,
        group_id=quota.group_id,
        user_id=quota.user_id,
        group_name=group_name,
        user_email=user_email,
        max_cpu_cores=quota.max_cpu_cores,
        max_memory_mb=quota.max_memory_mb,
        max_disk_gb=quota.max_disk_gb,
        max_instances=quota.max_instances,
        created_at=quota.created_at,
    )


@router.get("/my-usage", response_model=QuotaUsagePublic)
def get_my_usage(session: SessionDep, current_user: CurrentUser) -> QuotaUsagePublic:
    quota = quota_service.get_effective_quota(session, current_user.id)
    usage = quota_service.get_usage(session, current_user.id)
    return QuotaUsagePublic(
        used_cpu_cores=usage.cpu_cores,
        used_memory_mb=usage.memory_mb,
        used_disk_gb=usage.disk_gb,
        used_instances=usage.instances,
        quota=EffectiveQuotaPublic(
            max_cpu_cores=quota.max_cpu_cores,
            max_memory_mb=quota.max_memory_mb,
            max_disk_gb=quota.max_disk_gb,
            max_instances=quota.max_instances,
        ),
    )


@router.get("", response_model=list[ResourceQuotaPublic])
def list_quotas(session: SessionDep, _: AdminUser) -> list[ResourceQuotaPublic]:
    quotas = session.exec(select(ResourceQuota)).all()
    return [_to_public(session, q) for q in quotas]


@router.post("", response_model=ResourceQuotaPublic, status_code=201)
def create_quota(
    body: ResourceQuotaCreate, session: SessionDep, current_user: AdminUser
) -> ResourceQuotaPublic:
    if body.scope == QuotaScope.group:
        if session.get(Group, body.group_id) is None:
            raise NotFoundError("Group not found")
        existing = session.exec(
            select(ResourceQuota).where(ResourceQuota.group_id == body.group_id)
        ).first()
    else:
        if session.get(User, body.user_id) is None:
            raise NotFoundError("User not found")
        existing = session.exec(
            select(ResourceQuota).where(ResourceQuota.user_id == body.user_id)
        ).first()
    if existing is not None:
        raise ConflictError("此對象已有配額設定，請改用更新")

    quota = ResourceQuota(
        scope=body.scope,
        group_id=body.group_id if body.scope == QuotaScope.group else None,
        user_id=body.user_id if body.scope == QuotaScope.user else None,
        max_cpu_cores=body.max_cpu_cores,
        max_memory_mb=body.max_memory_mb,
        max_disk_gb=body.max_disk_gb,
        max_instances=body.max_instances,
    )
    session.add(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details=f"Created {body.scope.value} quota for "
        f"{body.group_id or body.user_id}",
        commit=False,
    )
    session.commit()
    session.refresh(quota)
    return _to_public(session, quota)


@router.put("/{quota_id}", response_model=ResourceQuotaPublic)
def update_quota(
    quota_id: uuid.UUID,
    body: ResourceQuotaUpdate,
    session: SessionDep,
    current_user: AdminUser,
) -> ResourceQuotaPublic:
    quota = session.get(ResourceQuota, quota_id)
    if quota is None:
        raise NotFoundError("Quota not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(quota, field, value)
    session.add(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details=f"Updated quota {quota_id}",
        commit=False,
    )
    session.commit()
    session.refresh(quota)
    return _to_public(session, quota)


@router.delete("/{quota_id}", response_model=Message)
def delete_quota(
    quota_id: uuid.UUID, session: SessionDep, current_user: AdminUser
) -> Message:
    quota = session.get(ResourceQuota, quota_id)
    if quota is None:
        raise NotFoundError("Quota not found")
    session.delete(quota)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details=f"Deleted quota {quota_id}",
        commit=False,
    )
    session.commit()
    return Message(message="Quota deleted")
