"""共享與轉移：授權別人操作我的機器、把機器交給別人。

共享只給「使用」層級（開關機、重開、主控台、監控）；擁有者層級的設定
（憑證、快照、規格、對外服務、刪除）仍只有擁有者與管理員能動。
轉移是直接改 ``resources.user_id``，原申請單保留給歷史紀錄。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import User
from app.models.resource_share import SHARE_PERMISSION_CONTROL
from app.repositories import resource as resource_repo
from app.repositories import resource_share as share_repo
from app.repositories import user as user_repo
from app.schemas.resource_settings import (
    ResourceSharePublic,
    ResourceTransferResponse,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)


def _get_personal_resource(session: Session, vmid: int):
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise NotFoundError(t("resource_settings.resourceNotRegistered", vmid=vmid))
    if resource.allocation_scope == "teaching_class" or resource.teaching_class_id:
        raise BadRequestError(t("resource_settings.classMachineNoSharing"))
    return resource


def _find_target_user(session: Session, email: str) -> User:
    user = user_repo.get_user_by_email(session=session, email=email)
    if user is None or not user.is_active:
        raise NotFoundError(t("resource_settings.userNotFound", email=email))
    return user


def _to_public(share, user: User | None) -> ResourceSharePublic:
    return ResourceSharePublic(
        id=share.id,
        vmid=share.resource_vmid,
        user_id=share.user_id,
        user_email=user.email if user else None,
        user_full_name=user.full_name if user else None,
        permission=share.permission,
        created_at=share.created_at,
    )


def user_has_share(*, session: Session, vmid: int, user_id: uuid.UUID) -> bool:
    return share_repo.get_share(session=session, vmid=vmid, user_id=user_id) is not None


def list_shared_vmids(*, session: Session, user_id: uuid.UUID) -> dict[int, Any]:
    """被分享給這位使用者的 vmid → ResourceShare。"""
    return {
        share.resource_vmid: share
        for share in share_repo.list_shares_for_user(session=session, user_id=user_id)
    }


def list_shares(*, session: Session, vmid: int) -> list[ResourceSharePublic]:
    shares = share_repo.list_shares_for_resource(session=session, vmid=vmid)
    return [_to_public(share, session.get(User, share.user_id)) for share in shares]


def add_share(
    *, session: Session, vmid: int, actor: Any, email: str
) -> ResourceSharePublic:
    resource = _get_personal_resource(session, vmid)
    target = _find_target_user(session, email)
    if target.id == resource.user_id:
        raise BadRequestError(t("resource_settings.cannotShareWithOwner"))
    if share_repo.get_share(session=session, vmid=vmid, user_id=target.id):
        raise BadRequestError(t("resource_settings.alreadyShared", email=target.email))

    share = share_repo.create_share(
        session=session,
        vmid=vmid,
        user_id=target.id,
        granted_by=actor.id,
        permission=SHARE_PERMISSION_CONTROL,
        commit=False,
    )
    audit_service.log_action(
        session=session,
        user_id=actor.id,
        vmid=vmid,
        action="resource_share_update",
        details=f"Shared resource {vmid} with {target.email} (permission=control)",
        commit=False,
    )
    session.commit()
    session.refresh(share)
    return _to_public(share, target)


def remove_share(
    *, session: Session, vmid: int, share_id: uuid.UUID, actor: Any
) -> None:
    share = share_repo.get_share_by_id(session=session, share_id=share_id)
    if share is None or share.resource_vmid != vmid:
        raise NotFoundError(t("resource_settings.shareNotFound"))
    target = session.get(User, share.user_id)
    share_repo.delete_share(session=session, share=share, commit=False)
    audit_service.log_action(
        session=session,
        user_id=actor.id,
        vmid=vmid,
        action="resource_share_update",
        details=(
            f"Revoked share of resource {vmid} from "
            f"{target.email if target else share.user_id}"
        ),
        commit=False,
    )
    session.commit()


def transfer_ownership(
    *,
    session: Session,
    vmid: int,
    actor: Any,
    email: str,
    keep_access: bool,
) -> ResourceTransferResponse:
    resource = _get_personal_resource(session, vmid)
    target = _find_target_user(session, email)
    if target.id == resource.user_id:
        raise BadRequestError(t("resource_settings.alreadyOwner", email=target.email))

    previous_owner_id = resource.user_id
    previous_owner = session.get(User, previous_owner_id)
    resource.user_id = target.id
    session.add(resource)

    # 新擁有者原本若在共享名單，改成擁有者後就不需要那筆共享了
    existing = share_repo.get_share(session=session, vmid=vmid, user_id=target.id)
    if existing is not None:
        share_repo.delete_share(session=session, share=existing, commit=False)

    if keep_access and previous_owner_id != target.id:
        share_repo.create_share(
            session=session,
            vmid=vmid,
            user_id=previous_owner_id,
            granted_by=actor.id,
            permission=SHARE_PERMISSION_CONTROL,
            commit=False,
        )

    audit_service.log_action(
        session=session,
        user_id=actor.id,
        vmid=vmid,
        action="resource_transfer",
        details=(
            f"Transferred resource {vmid} from "
            f"{previous_owner.email if previous_owner else previous_owner_id} "
            f"to {target.email} (keep_access={keep_access})"
        ),
        commit=False,
    )
    session.commit()
    logger.info("Resource %s transferred to %s by %s", vmid, target.email, actor.email)
    return ResourceTransferResponse(
        vmid=vmid,
        new_owner_id=target.id,
        new_owner_email=target.email,
        message=t("resource_settings.transferred", email=target.email),
    )


__all__ = [
    "add_share",
    "list_shared_vmids",
    "list_shares",
    "remove_share",
    "transfer_ownership",
    "user_has_share",
]
