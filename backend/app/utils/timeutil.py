"""時間相關的純函式。"""

from datetime import UTC, datetime


def normalize_datetime(value: datetime | None) -> datetime | None:
    """naive datetime 一律視為 UTC；aware 的原樣回傳。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
