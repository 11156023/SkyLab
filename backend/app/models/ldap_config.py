"""LDAP/Active Directory 連線設定模型。"""

from datetime import datetime

from sqlmodel import Column, DateTime, Field, SQLModel

from .base import get_datetime_utc


class LdapConfig(SQLModel, table=True):
    """LDAP 連線設定（單列 singleton，id 固定為 1）"""

    __tablename__ = "ldap_config"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    server_uri: str = Field(default="", max_length=255, description="ldap:// 或 ldaps://")
    use_starttls: bool = Field(default=False)
    bind_dn: str = Field(default="", max_length=512)
    encrypted_bind_password: str = Field(default="", max_length=2048)
    user_search_base: str = Field(default="", max_length=512)
    user_filter_template: str = Field(
        default="(uid={username})",
        max_length=512,
        description="AD 常用 (sAMAccountName={username})",
    )
    email_attribute: str = Field(default="mail", max_length=64)
    name_attribute: str = Field(default="displayName", max_length=64)
    teacher_group_dn: str | None = Field(default=None, max_length=512)
    admin_group_dn: str | None = Field(default=None, max_length=512)
    auto_create_users: bool = Field(default=True)
    connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


__all__ = ["LdapConfig"]
