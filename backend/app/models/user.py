"""使用者相關模型"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import EmailStr
from sqlalchemy import DateTime
from sqlmodel import Column, Enum, Field, Relationship, SQLModel

from .base import get_datetime_utc

if TYPE_CHECKING:
    from .ai_api_credential import AIAPICredential
    from .ai_api_request import AIAPIRequest
    from .audit_log import AuditLog
    from .resource import Resource
    from .spec_change_request import SpecChangeRequest
    from .vm_request import VMRequest


# Shared properties
class UserRole(str, enum.Enum):
    student = "student"
    teacher = "teacher"
    admin = "admin"


class UserBase(SQLModel):
    """使用者基礎屬性"""

    email: EmailStr = Field(unique=True, index=True, max_length=255)
    is_active: bool = True
    role: UserRole = Field(
        default=UserRole.student,
        sa_column=Column(Enum(UserRole), nullable=False, default=UserRole.student),
    )
    is_superuser: bool = False
    is_instructor: bool = False
    full_name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)


# Database model, database table inferred from class name
class User(UserBase, table=True):
    """使用者資料庫模型"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    hashed_password: str
    # 帳號來源："local"（本地密碼）| "ldap"（由 LDAP 目錄管理，本地密碼不可用）。
    # 舊帳號一律 local，LDAP 登入成功時自癒標記為 ldap（見 ldap_auth_service）。
    auth_source: str = Field(default="local", max_length=20)
    token_version: int = Field(default=0, description="令牌版本，修改密碼時遞增以失效舊令牌")
    created_at: datetime | None = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),
    )

    # Relationships
    resources: list["Resource"] = Relationship(back_populates="user")
    vm_requests: list["VMRequest"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"foreign_keys": "[VMRequest.user_id]"},
    )
    spec_change_requests: list["SpecChangeRequest"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"foreign_keys": "[SpecChangeRequest.user_id]"},
    )
    ai_api_requests: list["AIAPIRequest"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"foreign_keys": "[AIAPIRequest.user_id]"},
    )
    ai_api_credentials: list["AIAPICredential"] = Relationship(
        back_populates="user"
    )
    audit_logs: list["AuditLog"] = Relationship(back_populates="user")


__all__ = [
    "UserBase",
    "User",
    "UserRole",
]
