"""VM 範本模型（範本系統 2.0）"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel

from .base import get_datetime_utc


class VMTemplateStatus(str, enum.Enum):
    creating = "creating"
    ready = "ready"
    updating = "updating"
    failed = "failed"
    deleted = "deleted"


class VMTemplateVisibility(str, enum.Enum):
    global_ = "global"
    private = "private"


class VMTemplate(SQLModel, table=True):
    """PVE 範本的平台側 metadata（與 PVE 端以 pve_vmid 對照）"""

    __tablename__ = "vm_templates"
    __table_args__ = (
        sa.Index("ix_vm_templates_status_visibility", "status", "visibility"),
        # PVE 會回收 VMID：只有未刪除的範本需要唯一；刪除的舊列保留原 id，
        # 避免課程版本等歷史引用被新範本「接手」
        sa.Index(
            "uq_vm_templates_pve_vmid_active",
            "pve_vmid",
            unique=True,
            postgresql_where=sa.text("status <> 'deleted'"),
            sqlite_where=sa.text("status <> 'deleted'"),
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pve_vmid: int = Field(
        sa_column=Column(sa.Integer, nullable=False),
        description="PVE 端範本 VMID",
    )
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    owner_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    node: str = Field(max_length=63, description="範本所在 PVE 節點")
    storage: str | None = Field(
        default=None,
        max_length=128,
        description="範本磁碟所在 storage（linked clone 需同 storage）",
    )
    resource_type: str = Field(
        default="qemu",
        max_length=10,
        description="qemu 或 lxc",
    )
    status: VMTemplateStatus = Field(
        default=VMTemplateStatus.creating,
        sa_column=Column(
            Enum(VMTemplateStatus),
            nullable=False,
            default=VMTemplateStatus.creating,
        ),
    )
    visibility: VMTemplateVisibility = Field(
        default=VMTemplateVisibility.private,
        sa_column=Column(
            # global_ 成員名與值 "global" 不同，必須以值入庫
            Enum(
                VMTemplateVisibility,
                values_callable=lambda enum_cls: [m.value for m in enum_cls],
            ),
            nullable=False,
            default=VMTemplateVisibility.private,
        ),
    )
    default_cores: int | None = Field(default=None, description="克隆預設 CPU 核數")
    default_memory: int | None = Field(default=None, description="克隆預設記憶體 MB")
    default_disk: int | None = Field(
        default=None,
        description="範本磁碟 GB（轉換完成時自動偵測，唯讀；克隆固定沿用）",
    )
    allow_password_change: bool = Field(
        default=True,
        sa_column=Column(
            sa.Boolean, nullable=False, server_default=sa.true()
        ),
        description="克隆時是否允許使用者自訂/重設登入密碼；否則沿用範本內建帳密",
    )
    requires_gpu: bool = Field(
        default=False,
        sa_column=Column(
            sa.Boolean, nullable=False, server_default=sa.false()
        ),
        description="使用此範本是否需要 GPU（僅 qemu；克隆時強制選擇 GPU）",
    )
    source_vmid: int | None = Field(
        default=None,
        description="建立範本時的來源母機 VMID",
    )
    version: int = Field(default=1, description="更新循環遞增版本號")
    error_message: str | None = Field(default=None, max_length=1000)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False, onupdate=get_datetime_utc),
    )


class TemplateAttachment(SQLModel, table=True):
    """範本附件（使用手冊等）。實體檔存 data/template_files/{template_id}/。"""

    __tablename__ = "template_attachments"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_templates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    filename: str = Field(max_length=255, description="原始檔名（下載時還原）")
    content_type: str | None = Field(default=None, max_length=100)
    size_bytes: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = [
    "TemplateAttachment",
    "VMTemplate",
    "VMTemplateStatus",
    "VMTemplateVisibility",
]
