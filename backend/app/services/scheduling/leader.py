"""lifespan 迴圈的 leader 鎖：多 worker 部署時只讓一個行程跑治理任務。

``fastapi run --workers N`` 會起 N 個行程，每個都執行 lifespan 裡的排程器、
Web Push 推播與 WireGuard reconciler；TTL 通知、挖礦處置、告警、推播、
peer replay 這些任務沒有 DB 層的去重，會被重複執行 N 次。
這裡用 PostgreSQL transaction-level advisory lock：每輪 tick 開一條連線、在其
交易內用 ``pg_try_advisory_xact_lock`` 搶一次，搶到的跑完本輪任務後隨交易
結束釋放；行程死掉連線斷開時鎖也自動回收。

用 xact 版而不是 session 版，是因為連線經 PgBouncer transaction pooling：
session-level 鎖若沒被明確釋放，會殘留在回到池裡的 server 連線上被其他
客戶端繼承；xact 版在 rollback／commit 時必然釋放，沒有這個問題。
非 PostgreSQL（測試用 SQLite）一律視為 leader。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.core.db import engine

logger = logging.getLogger(__name__)

# 與 operations._VMID_ALLOCATION_LOCK_KEY 等其他 advisory lock 錯開。
# 每個 lifespan 迴圈各用一把鎖：主排程一輪可能跑十幾秒，不能讓 5 秒一輪的
# 推播或 WireGuard reconciler 排在它後面等。
SCHEDULER_LEADER_LOCK_KEY = 0x534B_5943_4C31  # "SKYCL1"
PUSH_NOTIFIER_LEADER_LOCK_KEY = 0x534B_5950_5348  # "SKYPSH"
WIREGUARD_RECONCILER_LEADER_LOCK_KEY = 0x534B_5957_4752  # "SKYWGR"


@contextmanager
def scheduler_leader_lock(
    key: int = SCHEDULER_LEADER_LOCK_KEY,
) -> Iterator[bool]:
    """本輪 tick 是否取得 ``key`` 對應的 leader 鎖；離開 context 即釋放。"""
    if engine.dialect.name != "postgresql":
        yield True
        return

    # SQLAlchemy 2 在第一次 execute 時自動開交易，離開 context 才 rollback；
    # 鎖就跟著這個交易活到 context 結束。
    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": key},
            ).scalar()
        )
        try:
            yield acquired
        finally:
            try:
                connection.rollback()
            except Exception:
                # 連線關閉時交易結束，xact-level advisory lock 一樣會釋放
                logger.warning(
                    "Failed to release scheduler leader lock", exc_info=True
                )
