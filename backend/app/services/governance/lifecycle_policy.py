"""TTL 與閒置回收的決策純函式。

不碰 DB / PVE / SMTP — 輸入資源狀態與 now，輸出單一動作，
由 ``lifecycle_service`` 負責 I/O。
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timedelta, timezone
from typing import Any


class TtlAction(str, enum.Enum):
    warn = "warn"      # 到期前通知擁有者
    stop = "stop"      # 已到期：排程自動關機
    delete = "delete"  # 寬限期滿：進刪除佇列
    none = "none"


class IdleAction(str, enum.Enum):
    mark = "mark"    # 首次偵測到閒置：標記 + 通知
    stop = "stop"    # 閒置寬限期滿：排程自動關機
    clear = "clear"  # 恢復活躍：清除閒置標記
    none = "none"


def _expiry_datetime(expiry_date: date) -> datetime:
    """到期日以當日 00:00 UTC 起算。"""
    return datetime(
        expiry_date.year, expiry_date.month, expiry_date.day, tzinfo=timezone.utc
    )


def decide_ttl_action(
    *,
    expiry_date: date | None,
    expiry_notified_at: datetime | None,
    scheduled_deletion_at: datetime | None,
    is_running: bool,
    now: datetime,
    warn_days: int,
    grace_delete_days: int,
) -> TtlAction:
    if expiry_date is None:
        return TtlAction.none

    expiry_at = _expiry_datetime(expiry_date)

    # 寬限期滿：進刪除佇列（優先於 stop — 即使還在跑，刪除流程會處理）
    if now >= expiry_at + timedelta(days=grace_delete_days):
        if scheduled_deletion_at is None:
            return TtlAction.delete
        return TtlAction.none

    # 已到期：自動關機（冪等 — 已停止就不再動作）
    if now >= expiry_at:
        return TtlAction.stop if is_running else TtlAction.none

    # 到期前 warn_days 內：通知一次
    if now >= expiry_at - timedelta(days=warn_days) and expiry_notified_at is None:
        return TtlAction.warn

    return TtlAction.none


def average_cpu_percent(
    rrd: list[dict[str, Any]], *, window_hours: int, now: datetime
) -> float | None:
    """RRD（PVE rrddata 格式）在時間視窗內的平均 CPU（percent）。

    無有效資料點回傳 None（不可據此判斷閒置）。
    """
    window_start = (now - timedelta(hours=window_hours)).timestamp()
    values: list[float] = []
    for point in rrd:
        ts = point.get("time")
        cpu = point.get("cpu")
        if ts is None or cpu is None:
            continue
        if float(ts) >= window_start:
            values.append(float(cpu) * 100.0)
    if not values:
        return None
    return sum(values) / len(values)


def decide_idle_action(
    *,
    avg_cpu: float | None,
    idle_since: datetime | None,
    now: datetime,
    threshold_percent: float,
    grace_hours: int,
) -> IdleAction:
    if avg_cpu is None:
        # 無數據不做任何判斷（也不清標記 — 避免 PVE 抖動反覆清除）
        return IdleAction.none

    if avg_cpu >= threshold_percent:
        return IdleAction.clear if idle_since is not None else IdleAction.none

    if idle_since is None:
        return IdleAction.mark
    if now - idle_since >= timedelta(hours=grace_hours):
        return IdleAction.stop
    return IdleAction.none
