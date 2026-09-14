"""資源進階設定 API：規格摘要、開機選項、登入憑證、標籤備註、共享與轉移。

路徑都掛在 ``/resources/{vmid}/...`` 下；讀取用 ``ResourceInfoDep``（擁有者／
管理員），寫入一律再過 ``require_resource_management``。
"""

import logging
import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, ResourceInfoDep, SessionDep
from app.schemas import Message
from app.schemas.resource_settings import (
    AuthorizedKeyRequest,
    AuthorizedKeysResponse,
    BootOptionsPublic,
    BootOptionsUpdate,
    CredentialsPublic,
    IsoImagePublic,
    PasswordResetRequest,
    PasswordResetResponse,
    ResourceMetadataPublic,
    ResourceMetadataUpdate,
    ResourceShareCreate,
    ResourceSharePublic,
    ResourceSpecsPublic,
    ResourceTransferRequest,
    ResourceTransferResponse,
    SshKeyRegenerateResponse,
)
from app.services.resource import (
    credentials_service,
    settings_service,
    sharing_service,
)
from app.services.resource.access import require_resource_management

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resource-settings"])


# ─── 規格摘要 ─────────────────────────────────────────────────────────────────


@router.get("/{vmid}/specs", response_model=ResourceSpecsPublic)
def get_specs(vmid: int, resource_info: ResourceInfoDep):
    return settings_service.get_specs(vmid=vmid, resource_info=resource_info)


# ─── 開機選項 ─────────────────────────────────────────────────────────────────
# 「主機開機時自動啟動」(onboot) 不開放設定：由 operations.control 在每次
# 開關機後自動對齊（開機→1、關機→0）。


@router.get("/{vmid}/boot-options", response_model=BootOptionsPublic)
def get_boot_options(vmid: int, resource_info: ResourceInfoDep):
    return settings_service.get_boot_options(vmid=vmid, resource_info=resource_info)


@router.put("/{vmid}/boot-options", response_model=BootOptionsPublic)
def update_boot_options(
    vmid: int,
    body: BootOptionsUpdate,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return settings_service.update_boot_options(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        data=body,
    )


@router.get("/{vmid}/iso-images", response_model=list[IsoImagePublic])
def list_iso_images(vmid: int, resource_info: ResourceInfoDep):
    return settings_service.list_iso_images(resource_info=resource_info)


# ─── 登入憑證 ─────────────────────────────────────────────────────────────────


@router.get("/{vmid}/credentials", response_model=CredentialsPublic)
def get_credentials(
    vmid: int,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return credentials_service.get_credentials(
        session=session, vmid=vmid, resource_info=resource_info
    )


@router.post("/{vmid}/credentials/reset-password", response_model=PasswordResetResponse)
def reset_password(
    vmid: int,
    body: PasswordResetRequest,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return credentials_service.reset_password(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        password=body.password,
    )


@router.post(
    "/{vmid}/credentials/regenerate-ssh-key", response_model=SshKeyRegenerateResponse
)
def regenerate_ssh_key(
    vmid: int,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return credentials_service.regenerate_ssh_key(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post(
    "/{vmid}/credentials/authorized-keys", response_model=AuthorizedKeysResponse
)
def add_authorized_key(
    vmid: int,
    body: AuthorizedKeyRequest,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return credentials_service.add_authorized_key(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        public_key=body.public_key,
    )


@router.delete(
    "/{vmid}/credentials/authorized-keys", response_model=AuthorizedKeysResponse
)
def remove_authorized_key(
    vmid: int,
    body: AuthorizedKeyRequest,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return credentials_service.remove_authorized_key(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        public_key=body.public_key,
    )


# ─── 標籤與備註 ───────────────────────────────────────────────────────────────


@router.get("/{vmid}/metadata", response_model=ResourceMetadataPublic)
def get_metadata(vmid: int, resource_info: ResourceInfoDep):
    return settings_service.get_metadata(vmid=vmid, resource_info=resource_info)


@router.put("/{vmid}/metadata", response_model=ResourceMetadataPublic)
def update_metadata(
    vmid: int,
    body: ResourceMetadataUpdate,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return settings_service.update_metadata(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        data=body,
    )


# ─── 共享與轉移 ───────────────────────────────────────────────────────────────


@router.get("/{vmid}/shares", response_model=list[ResourceSharePublic])
def list_shares(
    vmid: int,
    _resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return sharing_service.list_shares(session=session, vmid=vmid)


@router.post("/{vmid}/shares", response_model=ResourceSharePublic, status_code=201)
def add_share(
    vmid: int,
    body: ResourceShareCreate,
    _resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return sharing_service.add_share(
        session=session, vmid=vmid, actor=current_user, email=body.email
    )


@router.delete("/{vmid}/shares/{share_id}", response_model=Message)
def remove_share(
    vmid: int,
    share_id: uuid.UUID,
    _resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    sharing_service.remove_share(
        session=session, vmid=vmid, share_id=share_id, actor=current_user
    )
    return Message(message="Share removed")


@router.post("/{vmid}/transfer", response_model=ResourceTransferResponse)
def transfer_ownership(
    vmid: int,
    body: ResourceTransferRequest,
    _resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return sharing_service.transfer_ownership(
        session=session,
        vmid=vmid,
        actor=current_user,
        email=body.email,
        keep_access=body.keep_access,
    )
