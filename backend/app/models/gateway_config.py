"""Gateway VM 連線設定模型"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class GatewayConfig(SQLModel, table=True):
    """Gateway VM 連線設定（singleton，id 固定為 1）"""

    __tablename__ = "gateway_config"

    id: int = Field(default=1, primary_key=True)
    host: str = Field(default="", max_length=255, description="Gateway VM IP")
    ssh_port: int = Field(default=22, description="SSH port")
    ssh_user: str = Field(default="root", max_length=64, description="SSH 使用者名稱")
    # 私鑰加密儲存；公鑰明文儲存（給 admin 貼到 Gateway VM）
    encrypted_private_key: str = Field(default="", sa_type=sa.Text())
    public_key: str = Field(default="", sa_type=sa.Text())
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_datetime_utc},
    )


__all__ = ["GatewayConfig"]
