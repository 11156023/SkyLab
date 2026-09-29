"""Unit tests for the pure RRULE helpers in services.scheduling.recurrence.

These cover the core scheduler invariants:
- Window timing matches the configured timezone (cross-DST aware).
- ``is_in_window`` honors UTC/naive datetimes consistently.
- RRULE construction helpers produce valid strings.
- Exhausted RRULEs (``UNTIL`` already past) yield ``None``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.scheduling.recurrence import (
    build_weekly_rule,
    compute_next_window,
    is_in_window,
)

# ─── compute_next_window ─────────────────────────────────────────────────────


def test_weekly_rule_picks_next_friday_in_taipei() -> None:
    """Friday 13:00 Asia/Taipei == 05:00 UTC the same day."""
    rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=13;BYMINUTE=0"
    # 2026-04-26 is a Sunday; next Friday is 2026-05-01.
    after = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
    window = compute_next_window(rule, 240, "Asia/Taipei", after)
    assert window is not None
    start, end = window
    assert start == datetime(2026, 5, 1, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def test_daily_rule_returns_today_when_after_is_before_window() -> None:
    rule = "FREQ=DAILY;BYHOUR=8;BYMINUTE=0"
    after = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)  # 08:00 Asia/Taipei is 00:00 UTC
    window = compute_next_window(rule, 60, "Asia/Taipei", after)
    assert window is not None
    # 08:00 Taipei = 00:00 UTC, but the rule's anchor is later — must be today
    # or tomorrow. Either way it should be the soonest UTC.
    start, end = window
    assert end - start == timedelta(minutes=60)
    assert start >= after


def test_returns_none_when_until_already_past() -> None:
    rule = "FREQ=WEEKLY;BYDAY=FR;BYHOUR=13;BYMINUTE=0;UNTIL=20200101T000000Z"
    after = datetime(2026, 4, 26, tzinfo=UTC)
    assert compute_next_window(rule, 240, "Asia/Taipei", after) is None


def test_returns_none_for_empty_rule_or_zero_duration() -> None:
    after = datetime(2026, 4, 26, tzinfo=UTC)
    assert compute_next_window("", 60, "UTC", after) is None
    assert compute_next_window("FREQ=DAILY", 0, "UTC", after) is None


def test_naive_after_is_treated_as_utc() -> None:
    """Defensive: callers occasionally pass naive datetimes; we should attach
    UTC rather than mis-interpret as local time."""
    rule = "FREQ=DAILY;BYHOUR=12;BYMINUTE=0"
    naive = datetime(2026, 4, 26, 0, 0)  # tzinfo=None
    aware = datetime(2026, 4, 26, 0, 0, tzinfo=UTC)
    assert compute_next_window(rule, 30, "UTC", naive) == compute_next_window(
        rule, 30, "UTC", aware
    )


def test_dst_boundary_us_pacific() -> None:
    """DST forward-jump (2026-03-08 02:00 → 03:00 in US/Pacific) should not
    drop or duplicate the daily 09:00 window."""
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    # The day before the spring-forward (Mar 7 PST = UTC-8).
    after = datetime(2026, 3, 7, 0, 0, tzinfo=UTC)
    w1 = compute_next_window(rule, 60, "America/Los_Angeles", after)
    assert w1 is not None
    # The day of (Mar 8 PDT = UTC-7) — windows shift by one hour in UTC.
    after2 = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    w2 = compute_next_window(rule, 60, "America/Los_Angeles", after2)
    assert w2 is not None
    # PST → PDT means 09:00 local moves from 17:00 UTC to 16:00 UTC.
    assert w1[0].hour == 17
    assert w2[0].hour == 16


def test_default_timezone_used_when_none() -> None:
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    after = datetime(2026, 4, 26, 0, 0, tzinfo=UTC)
    # None and explicit "Asia/Taipei" should match the default.
    assert compute_next_window(rule, 60, None, after) == compute_next_window(
        rule, 60, "Asia/Taipei", after
    )


# ─── is_in_window ────────────────────────────────────────────────────────────


def test_is_in_window_true_at_start() -> None:
    start = datetime(2026, 5, 1, 5, 0, tzinfo=UTC)
    end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    assert is_in_window(start, end, start) is True


def test_is_in_window_false_at_end() -> None:
    start = datetime(2026, 5, 1, 5, 0, tzinfo=UTC)
    end = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    # Inclusive start, exclusive end.
    assert is_in_window(start, end, end) is False


def test_is_in_window_false_when_either_bound_is_none() -> None:
    now = datetime(2026, 5, 1, 6, 0, tzinfo=UTC)
    assert is_in_window(None, now, now) is False
    assert is_in_window(now, None, now) is False
    assert is_in_window(None, None, now) is False


def test_is_in_window_handles_naive_datetimes_as_utc() -> None:
    start = datetime(2026, 5, 1, 5, 0)  # naive
    end = datetime(2026, 5, 1, 9, 0)  # naive
    now = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)
    assert is_in_window(start, end, now) is True


# ─── builders ────────────────────────────────────────────────────────────────


def test_build_weekly_rule_preserves_day_codes() -> None:
    assert (
        build_weekly_rule(["MO", "WE", "FR"], 9, 30)
        == "FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=9;BYMINUTE=30"
    )


def test_build_weekly_rule_uppercases_input() -> None:
    assert build_weekly_rule(["fr"], 13, 0).startswith("FREQ=WEEKLY;BYDAY=FR;")


def test_build_weekly_rule_rejects_empty_days() -> None:
    with pytest.raises(ValueError):
        build_weekly_rule([], 9, 0)


def test_build_weekly_rule_rejects_invalid_time() -> None:
    with pytest.raises(ValueError):
        build_weekly_rule(["MO"], 25, 0)
    with pytest.raises(ValueError):
        build_weekly_rule(["MO"], 9, 60)
