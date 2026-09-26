"""基礎模型設定與工具函數"""

import uuid
from datetime import datetime, timezone

from sqlmodel import SQLModel


def get_datetime_utc() -> datetime:
    """取得目前 UTC 時間"""
    return datetime.now(timezone.utc)


# 重新匯出 SQLModel 以便其他模組使用
__all__ = ["SQLModel", "get_datetime_utc", "uuid", "datetime", "timezone"]
