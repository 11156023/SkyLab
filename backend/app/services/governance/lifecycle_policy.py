"""TTL 與閒置回收的決策純函式。

不碰 DB / PVE / SMTP — 輸入資源狀態與 now，輸出單一動作，
由 ``lifecycle_service`` 負責 I/O。
"""

from __future__ import annotations

import enum
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.infrastructure.proxmox.rrd import window_cpu_percentages


class TtlAction(str, enum.Enum):
    warn = "warn"      # 到期前通知擁有者
    stop = "stop"      # 已到期：排程自動關機
    delete = "delete"  # 寬限期滿：進刪除佇列
    none = "none"


class IdleAction(str, enum.Enum):
    mark = "mark"      # 首次偵測到閒置：只記 idle_since，不通知
    notify = "notify"  # 持續閒置達通知時數：通知擁有者
    stop = "stop"      # 閒置寬限期滿：排程自動關機
    clear = "clear"    # 恢復活躍或重開機：清除閒置標記
    none = "none"


def expiry_datetime(expiry_date: date) -> datetime:
    """回傳既有生命週期政策使用的到期日 00:00 UTC 界線。"""
    return datetime(
        expiry_date.year, expiry_date.month, expiry_date.day, tzinfo=timezone.utc
    )


# 排過刪除卻沒成功時，隔多久才重排一次
DELETION_RETRY_AFTER_HOURS = 24


def decide_ttl_action(
    *,
    expiry_date: date | None,
    expiry_notified_at: datetime | None,
    scheduled_deletion_at: datetime | None,
    is_running: bool,
    now: datetime,
    warn_days: int,
    grace_delete_days: int,
    deletion_pending: bool = False,
    deletion_retry_after_hours: int = DELETION_RETRY_AFTER_HOURS,
) -> TtlAction:
    """``deletion_pending``：這台機器目前有 pending/running 的刪除單。"""
    if expiry_date is None:
        return TtlAction.none

    expiry_at = expiry_datetime(expiry_date)

    # 寬限期滿：進刪除佇列（優先於 stop — 即使還在跑，刪除流程會處理）
    if now >= expiry_at + timedelta(days=grace_delete_days):
        if scheduled_deletion_at is None:
            return TtlAction.delete
        # 排程過但刪除單已 failed/cancelled（或當時 PVE 查不到而只記了時間），
        # 機器就這樣永遠留著 —— scheduled_deletion_at 有值會讓後續每個 tick
        # 都判成「已處理」。隔一段時間重排一次，直到真的有刪除單在跑。
        scheduled_at = (
            scheduled_deletion_at
            if scheduled_deletion_at.tzinfo is not None
            else scheduled_deletion_at.replace(tzinfo=timezone.utc)
        )
        if (
            not deletion_pending
            and now - scheduled_at >= timedelta(hours=deletion_retry_after_hours)
        ):
            return TtlAction.delete
        return TtlAction.none

    # 已到期：自動關機（冪等 — 已停止就不再動作）
    if now >= expiry_at:
        return TtlAction.stop if is_running else TtlAction.none

    # 到期前 warn_days 內：通知一次
    if now >= expiry_at - timedelta(days=warn_days) and expiry_notified_at is None:
        return TtlAction.warn

    return TtlAction.none


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def ttl_stop_email_due(
    *, expiry_date: date | None, expiry_notified_at: datetime | None
) -> bool:
    """這次到期的「已到期，將自動關機」信是否還沒寄過。

    寄出時把 ``expiry_notified_at`` 蓋成寄信時間（必然晚於到期時刻），之後
    VM 沒關成（guest 不理 ACPI）而每 tick 重排關機時，就不會每分鐘再寄一封。
    延期會把 ``expiry_notified_at`` 清成 None，新的到期日照常通知。
    """
    if expiry_date is None or expiry_notified_at is None:
        return True
    return _as_utc(expiry_notified_at) < expiry_datetime(expiry_date)


def idle_stop_email_due(
    *,
    idle_since: datetime | None,
    idle_notified_at: datetime | None,
    grace_hours: int,
) -> bool:
    """這段閒置（同一個 ``idle_since``）的「將自動關機」信是否還沒寄過。

    排程閒置關機時把 ``idle_notified_at`` 蓋成當下（必然不早於
    ``idle_since + grace_hours``）；通知階段的時間戳一定早於寬限期滿，
    兩者因此分得開。VM 沒關成而每次重掃又判 stop 時，只重排關機、不再寄信。
    """
    if idle_since is None or idle_notified_at is None:
        return True
    return _as_utc(idle_notified_at) < _as_utc(idle_since) + timedelta(
        hours=grace_hours
    )


def average_cpu_percent(
    rrd: list[dict[str, Any]], *, window_hours: int, now: datetime
) -> float | None:
    """RRD（PVE rrddata 格式）在時間視窗內的平均 CPU（percent）。

    無有效資料點回傳 None（不可據此判斷閒置）。
    """
    values = window_cpu_percentages(rrd, window_hours=window_hours, now=now)
    if not values:
        return None
    return sum(values) / len(values)


def decide_idle_action(
    *,
    avg_cpu: float | None,
    idle_since: datetime | None,
    idle_notified_at: datetime | None,
    now: datetime,
    threshold_percent: float,
    notify_after_hours: int,
    grace_hours: int,
    window_hours: int,
    uptime_seconds: int | None,
) -> IdleAction:
    """閒置狀態機：mark（靜默標記）→ notify（持續 notify_after_hours）
    → stop（持續 grace_hours）；三者都從 ``idle_since`` 起算。

    ``uptime_seconds`` 為 PVE 回報的本次開機秒數（未知則傳 None）。
    """
    if uptime_seconds is not None:
        # 標記閒置後曾重開機：舊標記失效，重新起算（否則重開後會因寬限期
        # 早已過而立刻再被排關機）。
        if idle_since is not None and now - idle_since > timedelta(
            seconds=uptime_seconds
        ):
            return IdleAction.clear
        # 開機時間還沒蓋滿觀察視窗：關機期間 RRD 沒有 cpu 值，平均只代表
        # 這段短暫開機時間，不構成「長期」閒置，不判斷。
        if uptime_seconds < window_hours * 3600:
            return IdleAction.none

    if avg_cpu is None:
        # 無數據不做任何判斷（也不清標記 — 避免 PVE 抖動反覆清除）
        return IdleAction.none

    if avg_cpu >= threshold_percent:
        return IdleAction.clear if idle_since is not None else IdleAction.none

    if idle_since is None:
        return IdleAction.mark
    idle_for = now - idle_since
    if idle_for >= timedelta(hours=grace_hours):
        return IdleAction.stop
    if idle_notified_at is None and idle_for >= timedelta(hours=notify_after_hours):
        return IdleAction.notify
    return IdleAction.none
