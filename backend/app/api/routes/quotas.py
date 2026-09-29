"""資源配額 API：admin 管理個人配額；所有登入者查自己用量。"""

import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import AuditAction, ResourceQuota, User
from app.schemas import (
    EffectiveQuotaPublic,
    GlobalQuotaPublic,
    GlobalQuotaUpdate,
    QuotaUsagePublic,
    ResourceQuotaCreate,
    ResourceQuotaPublic,
    ResourceQuotaUpdate,
)
from app.schemas.common import Message
from app.services.resource import quota_service
from app.services.user import audit_service

router = APIRouter(prefix="/quotas", tags=["quotas"])


def _to_public(session: SessionDep, quota: ResourceQuota) -> ResourceQuotaPublic:
    user = session.get(User, quota.user_id)
    user_email = user.email if user else None
    return ResourceQuotaPublic(
        id=quota.id,
        user_id=quota.user_id,
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


# NOTE: /global 必須註冊在 /{quota_id} 之前，否則會先被當成 quota_id
# 解析成 UUID 而失敗（422）。
@router.get("/global", response_model=GlobalQuotaPublic)
def get_global_quota(session: SessionDep, _: AdminUser) -> GlobalQuotaPublic:
    config = quota_service.get_global_quota(session)
    return GlobalQuotaPublic.model_validate(config, from_attributes=True)


@router.put("/global", response_model=GlobalQuotaPublic)
def update_global_quota(
    body: GlobalQuotaUpdate, session: SessionDep, current_user: AdminUser
) -> GlobalQuotaPublic:
    config = quota_service.update_global_quota(
        session, body.model_dump(exclude_unset=True)
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details="Updated global default quota",
    )
    return GlobalQuotaPublic.model_validate(config, from_attributes=True)


@router.get("", response_model=list[ResourceQuotaPublic])
def list_quotas(session: SessionDep, _: AdminUser) -> list[ResourceQuotaPublic]:
    quotas = quota_service.list_user_quotas(session)
    return [_to_public(session, q) for q in quotas]


@router.post("", response_model=ResourceQuotaPublic, status_code=201)
def create_quota(
    body: ResourceQuotaCreate, session: SessionDep, current_user: AdminUser
) -> ResourceQuotaPublic:
    quota = quota_service.create_user_quota(session, body, actor_id=current_user.id)
    return _to_public(session, quota)


@router.put("/{quota_id}", response_model=ResourceQuotaPublic)
def update_quota(
    quota_id: uuid.UUID,
    body: ResourceQuotaUpdate,
    session: SessionDep,
    current_user: AdminUser,
) -> ResourceQuotaPublic:
    quota = quota_service.update_user_quota(
        session, quota_id, body, actor_id=current_user.id
    )
    return _to_public(session, quota)


@router.delete("/{quota_id}", response_model=Message)
def delete_quota(
    quota_id: uuid.UUID, session: SessionDep, current_user: AdminUser
) -> Message:
    quota_service.delete_user_quota(session, quota_id, actor_id=current_user.id)
    return Message(message="Quota deleted")
