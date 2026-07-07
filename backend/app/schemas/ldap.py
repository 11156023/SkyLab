"""LDAP 登入與設定 schemas。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LdapLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class LoginMethodsPublic(BaseModel):
    password: bool
    google: bool
    ldap: bool


class LdapConfigPublic(BaseModel):
    """LDAP 設定（不含 bind 密碼本體，只回報是否已設定）。"""

    enabled: bool
    server_uri: str
    use_starttls: bool
    bind_dn: str
    bind_password_set: bool
    user_search_base: str
    user_filter_template: str
    email_attribute: str
    name_attribute: str
    teacher_group_dn: str | None = None
    admin_group_dn: str | None = None
    auto_create_users: bool
    connect_timeout_seconds: int
    updated_at: datetime


class LdapConfigUpdate(BaseModel):
    """LDAP 設定更新（partial；bind_password 有值才覆寫）。"""

    enabled: bool | None = None
    server_uri: str | None = Field(default=None, max_length=255)
    use_starttls: bool | None = None
    bind_dn: str | None = Field(default=None, max_length=512)
    bind_password: str | None = Field(default=None, max_length=1024)
    user_search_base: str | None = Field(default=None, max_length=512)
    user_filter_template: str | None = Field(default=None, max_length=512)
    email_attribute: str | None = Field(default=None, max_length=64)
    name_attribute: str | None = Field(default=None, max_length=64)
    teacher_group_dn: str | None = Field(default=None, max_length=512)
    admin_group_dn: str | None = Field(default=None, max_length=512)
    auto_create_users: bool | None = None
    connect_timeout_seconds: int | None = Field(default=None, ge=1, le=60)


class LdapTestResult(BaseModel):
    ok: bool
    message: str
