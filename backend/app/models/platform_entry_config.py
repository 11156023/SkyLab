"""平台入口設定模型：SkyLab 主系統自己經 Gateway nginx 對外的網域"""

from datetime import datetime

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from .base import get_datetime_utc


class PlatformEntryConfig(SQLModel, table=True):
    """平台入口設定（singleton，id 固定為 1）"""

    __tablename__ = "platform_entry_config"

    id: int = Field(default=1, primary_key=True)
    enabled: bool = Field(default=False)
    domain: str = Field(default="", max_length=255, description="主系統對外網域")
    # Gateway 連得到的主系統入口（部署機的內層 nginx，預設 :8082）
    upstream_host: str = Field(default="", max_length=255)
    upstream_port: int = Field(default=8082)
    enable_https: bool = Field(default=True)
    updated_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=sa.DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_datetime_utc},
    )


__all__ = ["PlatformEntryConfig"]
