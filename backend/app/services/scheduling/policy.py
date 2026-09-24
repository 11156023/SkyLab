from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import VMRequest
from app.utils.timeutil import normalize_datetime

SCHEDULER_POLL_SECONDS = 60

# provisioning_status=running 超過這麼久還沒寫回 vmid，就當成孤兒讓排程器接手。
# clone 一台機器通常幾分鐘內完成；留寬一點避免大範本正常 clone 被誤判。
PROVISIONING_STALE_MINUTES = 30


def utc_now() -> datetime:
    return datetime.now(UTC)


def is_provisioning_stale(started_at: datetime | None, *, now: datetime) -> bool:
    """running 是否已超時（純函式）。沒有起始時間的舊資料一律視為超時。"""
    started = normalize_datetime(started_at)
    if started is None:
        return True
    return now - started > timedelta(minutes=PROVISIONING_STALE_MINUTES)


def resource_type_for_request(request: VMRequest) -> str:
    return "lxc" if request.resource_type == "lxc" else "qemu"
