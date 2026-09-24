"""登入安全政策（全站 singleton）。"""

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class AuthPolicy(SQLModel, table=True):
    """登入安全政策（單列 singleton，id 固定為 1）

    ``totp_required``：強制所有使用者啟用兩步驟驗證。開啟後，尚未綁定的使用者
    登入雖然成功，但除了帳號本身（``/users/me*``）與登入／登出端點外的 API
    一律 403，前端據 ``UserPublic.totp_setup_required`` 顯示強制綁定畫面。
    """

    __tablename__ = "auth_policy"

    id: int = Field(default=1, primary_key=True)
    totp_required: bool = Field(default=False)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["AuthPolicy"]
