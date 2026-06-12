"""服務模板腳本部署日誌模型

持久化每一次 community-scripts 部署的狀態與完整輸出，讓管理員在
部署失敗或事後追查時可以查看完整的腳本執行記錄。
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Column, Field, SQLModel

from .base import get_datetime_utc


class ScriptDeployLog(SQLModel, table=True):
    """服務模板部署日誌

    - task_id：背景任務 ID（與 in-memory ExpiringStore 對應）
    - status：running | completed | failed
    - output：完整腳本 stdout/stderr（TEXT，不限長度）
    """

    __tablename__ = "script_deploy_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: str = Field(max_length=64, unique=True, index=True)
    user_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            sa.Uuid,
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    vmid: int | None = Field(default=None, index=True)
    resource_vmid: int | None = Field(
        default=None,
        sa_column=Column(
            sa.Integer,
            sa.ForeignKey("resources.vmid", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        description="Linked resource VMID; vmid remains as deployment snapshot",
    )
    template_slug: str = Field(max_length=120, index=True)
    template_name: str | None = Field(default=None, max_length=255)
    script_path: str | None = Field(default=None, max_length=500)
    hostname: str | None = Field(default=None, max_length=255)
    status: str = Field(default="running", max_length=20, index=True)
    progress: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=2000)
    error: str | None = Field(default=None, sa_column=Column(sa.Text, nullable=True))
    output: str | None = Field(default=None, sa_column=Column(sa.Text, nullable=True))
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        index=True,
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_type=sa.DateTime(timezone=True),
    )


__all__ = ["ScriptDeployLog"]
