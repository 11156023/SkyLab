"""資源模型"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlmodel import Column, DateTime, Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .user import User


class Resource(SQLModel, table=True):
    """資源額外信息表，儲存VM/Container的環境類型、到期日等資訊."""

    __tablename__ = "resources"

    vmid: int = Field(primary_key=True, description="VM/Container ID")
    user_id: uuid.UUID = Field(foreign_key="user.id", description="擁有者ID")
    environment_type: str = Field(
        description="環境類型，例如：Web開發標準版、LLM微調環境等"
    )
    os_info: str | None = Field(default=None, description="作業系統資訊")
    ip_address: str | None = Field(default=None, max_length=64, description="VM 最後已知 IP 位址（快取）")
    ip_address_cached_at: datetime | None = Field(
        sa_column=Column(DateTime(timezone=True), nullable=True),
        default=None,
        description="IP 位址最後快取時間",
    )
    expiry_date: date | None = Field(default=None, description="到期日，None表示無期限")
    template_id: int | None = Field(
        default=None, description="使用的模板ID（如果是從模板創建）"
    )
    service_template_slug: str | None = Field(
        default=None,
        description="服務模板 slug（對應前端 src/json 內的模板，例如 'jellyfin'）",
    )
    ssh_private_key_encrypted: str | None = Field(
        default=None, description="Fernet 加密後的 SSH 私鑰（PEM 格式）"
    )
    ssh_public_key: str | None = Field(
        default=None, description="SSH 公鑰（OpenSSH 格式）"
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        description="創建時間",
    )

    # Auto-stop fields populated by the scheduler / power-on flow.
    # auto_stop_reason: "window_grace" (course window finished + grace period)
    # or "practice_quota" (student manually started outside a course window).
    auto_stop_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    auto_stop_reason: str | None = Field(default=None, max_length=32)

    # Relationship
    user: Optional["User"] = Relationship(back_populates="resources")


__all__ = [
    "Resource",
]
