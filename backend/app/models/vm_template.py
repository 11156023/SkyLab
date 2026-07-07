"""VM 範本模型（範本系統 2.0）"""

import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Enum, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc


class VMTemplateStatus(str, enum.Enum):
    creating = "creating"
    ready = "ready"
    updating = "updating"
    failed = "failed"
    deleted = "deleted"


class VMTemplateVisibility(str, enum.Enum):
    global_ = "global"
    groups = "groups"


class VMTemplate(SQLModel, table=True):
    """PVE 範本的平台側 metadata（與 PVE 端以 pve_vmid 對照）"""

    __tablename__ = "vm_templates"
    __table_args__ = (
        sa.Index("ix_vm_templates_status_visibility", "status", "visibility"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    pve_vmid: int = Field(
        sa_column=Column(sa.Integer, nullable=False, unique=True, index=True),
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
        default=VMTemplateVisibility.groups,
        sa_column=Column(
            # global_ 成員名與值 "global" 不同，必須以值入庫
            Enum(
                VMTemplateVisibility,
                values_callable=lambda enum_cls: [m.value for m in enum_cls],
            ),
            nullable=False,
            default=VMTemplateVisibility.groups,
        ),
    )
    default_cores: int | None = Field(default=None, description="克隆預設 CPU 核數")
    default_memory: int | None = Field(default=None, description="克隆預設記憶體 MB")
    default_disk: int | None = Field(default=None, description="克隆預設磁碟 GB")
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
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class VMTemplateGroupLink(SQLModel, table=True):
    """範本 ↔ 群組可見範圍關聯"""

    __tablename__ = "vm_template_group_links"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "group_id", name="uq_vm_template_group_links"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("vm_templates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    group_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("group.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )


__all__ = [
    "VMTemplate",
    "VMTemplateGroupLink",
    "VMTemplateStatus",
    "VMTemplateVisibility",
]
