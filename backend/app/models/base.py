"""基礎模型設定與工具函數"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlmodel import SQLModel

# JSON 文件欄位：PostgreSQL 用 jsonb（可建索引、可查詢路徑、無重複鍵），
# 其他方言（SQLite 測試）用一般 JSON。注意與 sa.JSON 相同：就地修改 dict/list
# 不會被 ORM 偵測到，要重新指派新物件或呼叫 flag_modified。
JSONDocument = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def get_datetime_utc() -> datetime:
    """取得目前 UTC 時間"""
    return datetime.now(timezone.utc)


# 重新匯出 SQLModel 以便其他模組使用
__all__ = [
    "JSONDocument",
    "SQLModel",
    "get_datetime_utc",
    "uuid",
    "datetime",
    "timezone",
]
