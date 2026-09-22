"""排程器 leader 鎖：多 worker 部署時只讓一個行程跑治理任務。

``fastapi run --workers N`` 會起 N 個行程，每個都執行 lifespan 裡的排程器；
TTL 通知、挖礦處置、告警這些任務沒有 DB 層的去重，會被重複執行 N 次。
這裡用 PostgreSQL session-level advisory lock：每輪 tick 用 ``pg_try_advisory_lock``
搶一次，搶到的跑完本輪任務就釋放；行程死掉連線斷開時鎖自動回收。
非 PostgreSQL（測試用 SQLite）一律視為 leader。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.core.db import engine

logger = logging.getLogger(__name__)

# 與 operations._VMID_ALLOCATION_LOCK_KEY 等其他 advisory lock 錯開
SCHEDULER_LEADER_LOCK_KEY = 0x534B_5943_4C31  # "SKYCL1"


@contextmanager
def scheduler_leader_lock() -> Iterator[bool]:
    """本輪 tick 是否取得 leader 鎖；離開 context 即釋放。"""
    if engine.dialect.name != "postgresql":
        yield True
        return

    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": SCHEDULER_LEADER_LOCK_KEY},
            ).scalar()
        )
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": SCHEDULER_LEADER_LOCK_KEY},
                    )
                except Exception:
                    # 連線關閉時 session-level advisory lock 也會釋放
                    logger.warning(
                        "Failed to release scheduler leader lock", exc_info=True
                    )
