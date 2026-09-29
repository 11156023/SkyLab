"""舊資料裡的無效時區不能讓整輪 recurrence 排程中斷。

API 已經擋掉新的無效時區，但 teaching_classes／vm_requests 既有列仍可能帶著
壞值；排程每輪都會掃到它們，一筆壞資料就不應該讓所有租戶的視窗更新停擺。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.models import TeachingClass, TeachingClassStatus
from app.services.scheduling import recurrence_scheduler as rs
from app.services.scheduling.recurrence import DEFAULT_TIMEZONE

DAILY_RULE = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """依 process_recurrence_windows 的查詢順序回傳：班級 → 申請 → 批次任務。"""

    def __init__(self, env: dict[str, Any]) -> None:
        self.env = env

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def exec(self, _stmt: Any) -> _Result:
        return _Result(self.env["exec_results"].pop(0))

    def get(self, model: Any, key: Any) -> Any:
        if model is TeachingClass:
            return self.env["classes"].get(key)
        return None

    def add(self, value: Any) -> None:
        self.env["added"].append(value)

    def commit(self) -> None:
        self.env["commits"] += 1


def _teaching_class(**overrides: Any) -> SimpleNamespace:
    today = datetime.now(UTC).date()
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "status": TeachingClassStatus.active,
        "timezone": "Invalid/Zone",
        "start_date": today - timedelta(days=30),
        "start_time": time(8, 0),
        "end_date": today + timedelta(days=60),
        "end_time": time(17, 0),
        "resources_reclaimed_at": None,
        "reclaim_requested_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _stale_request(**overrides: Any) -> SimpleNamespace:
    stale_end = datetime.now(UTC) - timedelta(days=1)
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "recurrence_rule": DAILY_RULE,
        "recurrence_duration_minutes": 60,
        "schedule_timezone": None,
        "next_window_start": stale_end - timedelta(hours=1),
        "next_window_end": stale_end,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "exec_results": [],
        "classes": {},
        "added": [],
        "commits": 0,
        "archived": [],
    }
    monkeypatch.setattr(rs, "Session", lambda _engine: _FakeSession(state))

    def fake_archive(**kwargs: Any) -> dict[str, list[Any]]:
        state["archived"].append(kwargs["item"].id)
        return {"queued_vmids": [], "failed": []}

    monkeypatch.setattr(
        rs.class_lifecycle_service, "archive_and_reclaim", fake_archive
    )
    return state


def test_safe_zone_falls_back_to_default_for_invalid_names() -> None:
    assert rs._safe_zone("Invalid/Zone") == ZoneInfo(DEFAULT_TIMEZONE)
    assert rs._safe_zone("../etc/passwd") == ZoneInfo(DEFAULT_TIMEZONE)
    assert rs._safe_zone(None) == ZoneInfo(DEFAULT_TIMEZONE)
    assert rs._safe_zone("UTC") == ZoneInfo("UTC")


def test_class_expired_uses_default_zone_for_invalid_timezone() -> None:
    teaching_class = _teaching_class(
        end_date=date(2026, 8, 25), end_time=time(15, 0)
    )
    # 15:00 Asia/Taipei == 07:00 UTC
    assert rs._class_expired(
        teaching_class, datetime(2026, 8, 25, 6, 59, tzinfo=UTC)
    ) is False
    assert rs._class_expired(
        teaching_class, datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
    ) is True


def test_invalid_class_timezone_does_not_block_window_refresh(
    env: dict[str, Any],
) -> None:
    bad_tz_class = _teaching_class()
    # end_date 缺值這類無法判斷的班級要被跳過，不能中斷整輪
    broken_class = _teaching_class(timezone="Asia/Taipei", end_date=None)
    env["classes"] = {bad_tz_class.id: bad_tz_class}

    request = _stale_request()
    bad_request = _stale_request(schedule_timezone="Invalid/Zone")
    bad_request_end = bad_request.next_window_end
    job = SimpleNamespace(
        id=uuid.uuid4(),
        teaching_class_id=bad_tz_class.id,
        recurrence_rule=DAILY_RULE,
        recurrence_duration_minutes=60,
        schedule_timezone=None,
        next_window_start=None,
        next_window_end=None,
    )
    env["exec_results"] = [
        [bad_tz_class, broken_class],
        [request, bad_request],
        [job],
    ]

    rs.process_recurrence_windows()

    now = datetime.now(UTC)
    assert request.next_window_end is not None
    assert request.next_window_end > now
    assert request.next_window_start is not None
    assert (
        request.next_window_end - request.next_window_start
        == timedelta(minutes=60)
    )
    # 申請本身時區壞掉：跳過不動，其他列照常更新
    assert bad_request.next_window_end == bad_request_end
    assert bad_request not in env["added"]
    # 班級時區無效時退回預設時區，正式課程排程仍會算出視窗
    assert job.next_window_end is not None
    assert job.next_window_end > now
    assert env["commits"] == 1
    assert env["archived"] == []
    assert env["exec_results"] == []
