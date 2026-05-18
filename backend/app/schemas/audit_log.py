"""審計日誌 schemas"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.audit_log import AuditAction


class AuditLogPublic(BaseModel):
    """公開的審計日誌"""

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_email: str | None = None
    user_full_name: str | None = None
    vmid: int | None
    resource_vmid: int | None = None
    action: AuditAction
    details: str
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AuditLogsPublic(BaseModel):
    """審計日誌列表"""

    data: list[AuditLogPublic]
    count: int


class AuditLogStats(BaseModel):
    """審計日誌儀表板統計卡片資料"""

    total: int
    danger: int
    login_failed: int
    active_users: int


class AuditActionMeta(BaseModel):
    """單一 action 元資訊（給前端 select 使用）"""

    value: str
    category: str


class AuditUserOption(BaseModel):
    """操作者下拉選單選項"""

    id: uuid.UUID
    email: str
    full_name: str | None = None
