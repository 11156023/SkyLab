"""Request / response schemas for administrator-managed user groups."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.i18n import t
from app.models import USER_GROUP_NAME_MAX_LENGTH, UserRole

# 一次加入／移出的上限：使用者管理頁最多載入 5000 位
MAX_MEMBERS_PER_REQUEST = 5000


class UserGroupIn(BaseModel):
    name: str = Field(max_length=USER_GROUP_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(t("userGroup.nameRequired"))
        return value


class UserGroupPublic(BaseModel):
    id: uuid.UUID
    name: str
    member_count: int
    # 老師從群組匯入班級時只會帶入學生，選單上顯示這個數字
    student_count: int
    created_at: datetime


class UserGroupsPublic(BaseModel):
    data: list[UserGroupPublic]
    count: int


class UserGroupMemberPublic(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: UserRole
    is_active: bool


class UserGroupMembersPublic(BaseModel):
    data: list[UserGroupMemberPublic]
    count: int


class UserGroupMembersAdd(BaseModel):
    """使用者管理頁勾選的帳號給 user_ids；手動輸入或 CSV 名單給 emails。"""

    user_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=MAX_MEMBERS_PER_REQUEST
    )
    emails: list[str] = Field(default_factory=list, max_length=MAX_MEMBERS_PER_REQUEST)


class UserGroupMembersRemove(BaseModel):
    user_ids: list[uuid.UUID] = Field(max_length=MAX_MEMBERS_PER_REQUEST)


class UserGroupMembersResult(BaseModel):
    added: int = 0
    removed: int = 0
    # 找不到（或已刪除）的 email／user id，原樣列回給畫面提示
    not_found: list[str] = Field(default_factory=list)
    group: UserGroupPublic
