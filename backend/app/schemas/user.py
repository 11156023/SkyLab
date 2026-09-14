"""使用者相關 schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import UserRole

# ===== Request Schemas =====


class UserCreate(BaseModel):
    """建立使用者"""

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    is_active: bool = True
    role: UserRole = UserRole.student
    is_superuser: bool = False
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UserRegister(BaseModel):
    """使用者自行註冊"""

    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UserUpdate(BaseModel):
    """管理員更新使用者"""

    email: EmailStr | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None
    role: UserRole | None = None
    is_superuser: bool | None = None
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UserUpdateMe(BaseModel):
    """使用者更新自己資料"""

    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UpdatePassword(BaseModel):
    """更新密碼"""

    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ===== Response Schemas =====


class UserPublic(BaseModel):
    """API 回傳的使用者資料"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    role: UserRole
    is_superuser: bool
    full_name: str | None = None
    avatar_url: str | None = None
    auth_source: str = "local"  # "local" | "ldap"（LDAP 帳號的本地密碼欄位應鎖住）
    created_at: datetime | None = None


class UsersPublic(BaseModel):
    """使用者列表回應"""

    data: list[UserPublic]
    count: int
