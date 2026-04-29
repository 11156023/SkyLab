"""Recurrence schedule utilities.

This module owns RRULE parsing and "next window" computation used by the
scheduler. Stored fields on ``VMRequest`` / ``BatchProvisionJob``:

- ``recurrence_rule`` — RFC 5545 RRULE string (e.g. ``FREQ=WEEKLY;BYDAY=FR``).
- ``recurrence_duration_minutes`` — length of one occurrence window.
- ``schedule_timezone`` — IANA tz name; the RRULE is interpreted in this tz.

Window computation is timezone-aware: the RRULE start is anchored at
``00:00`` of *today* in the requested timezone, then advanced via
``dateutil.rrule``. Returned datetimes are UTC-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from sqlmodel import Session

from app.repositories import proxmox_config as proxmox_config_repo

DEFAULT_TIMEZONE = "Asia/Taipei"


@dataclass(frozen=True)
class SchedulePolicy:
    """Admin-tunable scheduled boot / auto-stop parameters."""

    boot_batch_size: int
    boot_batch_interval_seconds: int
    boot_lead_time_minutes: int
    window_grace_minutes: int
    practice_session_hours: int
    practice_warning_minutes: int


# Defaults match the model's server defaults — used only when no
# ProxmoxConfig row exists yet (fresh installs).
_DEFAULT_POLICY = SchedulePolicy(
    boot_batch_size=5,
    boot_batch_interval_seconds=10,
    boot_lead_time_minutes=5,
    window_grace_minutes=30,
    practice_session_hours=3,
    practice_warning_minutes=30,
)


def get_schedule_policy(*, session: Session) -> SchedulePolicy:
    config = proxmox_config_repo.get_proxmox_config(session)
    if config is None:
        return _DEFAULT_POLICY
    return SchedulePolicy(
        boot_batch_size=max(int(config.scheduled_boot_batch_size or 5), 1),
        boot_batch_interval_seconds=max(
            int(config.scheduled_boot_batch_interval_seconds or 10), 0
        ),
        boot_lead_time_minutes=max(
            int(config.scheduled_boot_lead_time_minutes or 5), 0
        ),
        window_grace_minutes=max(int(config.window_grace_period_minutes or 30), 0),
        practice_session_hours=max(int(config.practice_session_hours or 3), 1),
        practice_warning_minutes=max(int(config.practice_warning_minutes or 30), 1),
    )


def compute_next_window(
    rule: str,
    duration_minutes: int,
    timezone: str | None,
    after: datetime,
) -> tuple[datetime, datetime] | None:
    """Return (window_start_utc, window_end_utc) of the first occurrence
    starting at-or-after ``after`` (UTC). ``None`` if the RRULE has been
    exhausted (e.g. an UNTIL clause has passed).

    The RRULE is anchored at midnight (in the requested tz) of the day
    represented by ``after``, then advanced. ``BYHOUR``/``BYMINUTE`` clauses
    in the rule pin the actual start-of-day time.
    """
    if not rule or duration_minutes <= 0:
        return None

    tz = ZoneInfo(timezone or DEFAULT_TIMEZONE)
    after_utc = _ensure_utc(after)
    after_local = after_utc.astimezone(tz)
    # Anchor at midnight of the local day so BYHOUR/BYMINUTE picks the
    # intended time-of-day.
    dtstart = after_local.replace(hour=0, minute=0, second=0, microsecond=0)

    rule_set = rrulestr(rule, dtstart=dtstart)
    occurrence = rule_set.after(after_local, inc=True)
    if occurrence is None:
        return None

    # rrulestr returns a tz-aware datetime when dtstart is tz-aware.
    start_utc = occurrence.astimezone(UTC)
    end_utc = start_utc + timedelta(minutes=duration_minutes)
    return start_utc, end_utc


def is_in_window(
    window_start: datetime | None,
    window_end: datetime | None,
    now: datetime,
) -> bool:
    if window_start is None or window_end is None:
        return False
    now_utc = _ensure_utc(now)
    return _ensure_utc(window_start) <= now_utc < _ensure_utc(window_end)


def build_weekly_rule(
    days: list[str],
    hour: int,
    minute: int,
) -> str:
    """Helper for the "preset" UI mode — turns a list of weekday codes
    (``MO``, ``TU``, ...) and a start time into an RRULE string.
    """
    if not days:
        raise ValueError("days must be non-empty")
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("hour/minute out of range")
    by_day = ",".join(d.upper() for d in days)
    return f"FREQ=WEEKLY;BYDAY={by_day};BYHOUR={hour};BYMINUTE={minute}"


def build_daily_rule(hour: int, minute: int) -> str:
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("hour/minute out of range")
    return f"FREQ=DAILY;BYHOUR={hour};BYMINUTE={minute}"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_TIMEZONE",
    "SchedulePolicy",
    "build_daily_rule",
    "build_weekly_rule",
    "compute_next_window",
    "get_schedule_policy",
    "is_in_window",
]
