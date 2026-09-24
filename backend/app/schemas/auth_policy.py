"""登入安全政策 schemas"""

from datetime import datetime

from pydantic import BaseModel


class AuthPolicyPublic(BaseModel):
    """登入安全政策（所有登入者可讀）"""

    totp_required: bool
    updated_at: datetime | None = None


class AuthPolicyUpdate(BaseModel):
    """管理員更新登入安全政策"""

    totp_required: bool
