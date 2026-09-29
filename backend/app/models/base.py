"""基礎模型設定與工具函數"""

from datetime import datetime, timezone


def get_datetime_utc() -> datetime:
    """取得目前 UTC 時間"""
    return datetime.now(timezone.utc)


__all__ = ["get_datetime_utc"]
