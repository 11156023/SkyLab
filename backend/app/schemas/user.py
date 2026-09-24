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
    totp_required: bool = False  # 強制此帳號啟用兩步驟驗證


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
    totp_required: bool | None = None  # 強制此帳號啟用兩步驟驗證


class UserUpdateMe(BaseModel):
    """使用者更新自己資料"""

    full_name: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


class UpdatePassword(BaseModel):
    """更新密碼"""

    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TotpCodeRequest(BaseModel):
    """兩步驟驗證碼（確認綁定／停用時提交）；允許 App 顯示的 ``123 456`` 格式"""

    code: str = Field(min_length=6, max_length=16)


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
    totp_enabled: bool = False  # 已綁定兩步驟驗證（登入需輸入驗證碼）
    totp_required: bool = False  # 管理員要求此帳號啟用兩步驟驗證（已綁定者不可自行停用）
    # 只有 GET /users/me 會算：要求中且本人尚未綁定，前端只能顯示綁定畫面
    totp_setup_required: bool = False
    created_at: datetime | None = None


class UsersPublic(BaseModel):
    """使用者列表回應"""

    data: list[UserPublic]
    count: int


class TotpSetupPublic(BaseModel):
    """開始綁定兩步驟驗證：回傳金鑰與 otpauth URI（前端轉成 QR code）"""

    secret: str
    otpauth_uri: str
    issuer: str
    account: str


class TotpStatusPublic(BaseModel):
    """兩步驟驗證狀態"""

    totp_enabled: bool
