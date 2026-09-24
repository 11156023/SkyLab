"""系統初始化（首次安裝精靈）狀態模型。"""

import uuid
from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class SystemSetup(SQLModel, table=True):
    """初始化精靈進度（單列 singleton，id 固定為 1）。

    `completed` 為 True 之後，`/setup/*` 的所有未授權端點一律拒絕，
    後續設定改由管理員登入後在各系統管理頁面調整。
    """

    __tablename__ = "system_setup"

    id: int = Field(default=1, primary_key=True)
    completed: bool = Field(default=False)
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # 精靈建立（或指定）的管理員；帳號被刪除時設為 NULL，不影響 completed 狀態
    admin_user_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="user.id",
        ondelete="SET NULL",
        nullable=True,
    )
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["SystemSetup"]
