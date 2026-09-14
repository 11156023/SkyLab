"""資源進階設定 schemas：規格摘要、開機選項、登入憑證、標籤備註、共享與轉移。"""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

ResourceTypeLiteral = Literal["qemu", "lxc"]


# ===== 規格摘要（給規格分頁用，取代直接讀 Proxmox 原始 config） =====


class ResourceSpecsPublic(BaseModel):
    vmid: int
    resource_type: ResourceTypeLiteral
    cpu_cores: int | None = None
    memory_mb: int | None = None
    disk_gb: int | None = None


# ===== 開機選項 =====


class BootDevicePublic(BaseModel):
    key: str = Field(description="Proxmox 裝置鍵，如 scsi0 / ide2 / net0")
    kind: Literal["disk", "cdrom", "network", "other"]
    description: str | None = None


class IsoImagePublic(BaseModel):
    volid: str
    name: str
    size: int | None = None


class BootOptionsPublic(BaseModel):
    """開機順序與 ISO 掛載。

    ``onboot``（主機開機時自動啟動）不在這裡：它由系統跟著電源動作自動對齊
    （開機→1、關機→0，見 ``infrastructure.proxmox.operations.sync_onboot``），
    不開放使用者設定。
    """

    vmid: int
    resource_type: ResourceTypeLiteral
    supports_boot_order: bool = False
    boot_order: list[str] = Field(default_factory=list)
    boot_devices: list[BootDevicePublic] = Field(default_factory=list)
    supports_cdrom: bool = False
    cdrom_slot: str | None = Field(default=None, description="目前掛載 ISO 的裝置鍵")
    cdrom_iso: str | None = Field(default=None, description="目前掛載的 ISO volid")
    iso_storage: str | None = None
    running: bool = False


_DEVICE_KEY_RE = re.compile(r"^(scsi|virtio|sata|ide|nvme|net)\d{1,2}$")


class BootOptionsUpdate(BaseModel):
    boot_order: list[str] | None = Field(
        default=None, description="開機順序（裝置鍵列表，空列表代表交給 Proxmox 預設）"
    )
    cdrom_iso: str | None = Field(
        default=None, max_length=255, description="要掛載的 ISO volid"
    )
    eject_cdrom: bool = Field(default=False, description="退出目前掛載的 ISO")

    @field_validator("boot_order")
    @classmethod
    def _validate_boot_order(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            key = str(item).strip()
            if not _DEVICE_KEY_RE.match(key):
                raise ValueError(f"invalid boot device key: {key}")
            if key not in cleaned:
                cleaned.append(key)
        return cleaned

    @model_validator(mode="after")
    def _at_least_one(self) -> "BootOptionsUpdate":
        if (
            self.boot_order is None
            and self.cdrom_iso is None
            and not self.eject_cdrom
        ):
            raise ValueError("nothing to update")
        if self.cdrom_iso and self.eject_cdrom:
            raise ValueError("cdrom_iso and eject_cdrom are mutually exclusive")
        return self


# ===== 登入憑證 =====


class CredentialsPublic(BaseModel):
    vmid: int
    resource_type: ResourceTypeLiteral
    running: bool = False
    username: str | None = Field(
        default=None, description="cloud-init 設定的使用者；None 代表沿用映像預設"
    )
    has_login_password: bool = False
    supports_password_reset: bool = False
    supports_ssh_keys: bool = False
    requires_running: bool = Field(
        default=False, description="LXC 要在執行中才能改密碼／金鑰（pct exec）"
    )
    platform_public_key: str | None = Field(
        default=None, description="平台替這台機器產生的金鑰（公鑰）"
    )
    authorized_keys: list[str] = Field(
        default_factory=list, description="目前授權登入的公鑰（含平台金鑰）"
    )


class PasswordResetRequest(BaseModel):
    password: str | None = Field(
        default=None,
        min_length=8,
        max_length=64,
        description="留空由系統產生",
    )

    @field_validator("password")
    @classmethod
    def _no_whitespace(cls, value: str | None) -> str | None:
        if value is not None and any(ch.isspace() for ch in value):
            raise ValueError("password must not contain whitespace")
        return value


class PasswordResetResponse(BaseModel):
    vmid: int
    password: str
    applied_immediately: bool = Field(
        description="True：已在機器內直接改好；False：重新開機後生效"
    )
    message: str


class SshKeyRegenerateResponse(BaseModel):
    vmid: int
    ssh_public_key: str
    ssh_private_key: str
    applied_immediately: bool
    message: str


_SSH_KEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|"
    r"ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)"
    r"\s+[A-Za-z0-9+/=]+(\s+\S.*)?$"
)


class AuthorizedKeyRequest(BaseModel):
    public_key: str = Field(min_length=20, max_length=4096)

    @field_validator("public_key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if "\n" in value.strip():
            raise ValueError("only one public key at a time")
        if not _SSH_KEY_RE.match(cleaned):
            raise ValueError("not a valid OpenSSH public key")
        return cleaned


class AuthorizedKeysResponse(BaseModel):
    vmid: int
    authorized_keys: list[str]
    applied_immediately: bool
    message: str


# ===== 標籤 =====

_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-\+\.]{0,31}$")


class ResourceMetadataPublic(BaseModel):
    vmid: int
    tags: list[str] = Field(default_factory=list)


class ResourceMetadataUpdate(BaseModel):
    tags: list[str] = Field(max_length=16)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            tag = str(raw).strip().lower()
            if not tag:
                continue
            if not _TAG_RE.match(tag):
                raise ValueError(f"invalid tag: {raw}")
            if tag not in cleaned:
                cleaned.append(tag)
        return cleaned


# ===== 共享與轉移 =====


class ResourceSharePublic(BaseModel):
    id: uuid.UUID
    vmid: int
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    permission: str
    created_at: datetime


class ResourceShareCreate(BaseModel):
    email: EmailStr


class ResourceTransferRequest(BaseModel):
    email: EmailStr
    keep_access: bool = Field(
        default=False, description="轉移後把自己留在共享名單（仍可開關機）"
    )


class ResourceTransferResponse(BaseModel):
    vmid: int
    new_owner_id: uuid.UUID
    new_owner_email: str
    message: str


__all__ = [
    "AuthorizedKeyRequest",
    "AuthorizedKeysResponse",
    "BootDevicePublic",
    "BootOptionsPublic",
    "BootOptionsUpdate",
    "CredentialsPublic",
    "IsoImagePublic",
    "PasswordResetRequest",
    "PasswordResetResponse",
    "ResourceMetadataPublic",
    "ResourceMetadataUpdate",
    "ResourceShareCreate",
    "ResourceSharePublic",
    "ResourceSpecsPublic",
    "ResourceTransferRequest",
    "ResourceTransferResponse",
    "SshKeyRegenerateResponse",
]
