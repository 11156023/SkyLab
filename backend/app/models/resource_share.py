"""Resource sharing: let another user control (start/stop/console) a VM."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, DateTime, Field, SQLModel, UniqueConstraint

from .base import get_datetime_utc

SHARE_PERMISSION_CONTROL = "control"


class ResourceShare(SQLModel, table=True):
    """擁有者授權另一位使用者操作自己的機器（開關機、重開、主控台、監控）。

    共享只給「使用」層級：憑證、快照、規格、對外服務、刪除仍只有擁有者
    （與管理員）能動；轉移擁有者則是直接改 ``resources.user_id``，不走這張表。
    """

    __tablename__ = "resource_shares"
    __table_args__ = (
        UniqueConstraint("resource_vmid", "user_id", name="uq_resource_share_user"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    resource_vmid: int = Field(
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="被共享的資源 VMID",
    )
    user_id: uuid.UUID = Field(
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="被授權的使用者",
    )
    granted_by: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        description="授權者（通常是擁有者）",
    )
    permission: str = Field(
        default=SHARE_PERMISSION_CONTROL,
        max_length=24,
        description="目前只有 control（開關機／主控台）",
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["ResourceShare", "SHARE_PERMISSION_CONTROL"]
