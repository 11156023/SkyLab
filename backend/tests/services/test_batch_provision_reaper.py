"""批量建立：卡死回收與終態判定的純邏輯測試（無 DB / PVE）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.vm import batch_provision_service as svc

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=UTC)


def _job(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "id": uuid.uuid4(),
        "created_at": NOW - timedelta(days=1),
        "reviewed_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _task(
    *, started_at: datetime | None = None, finished_at: datetime | None = None
) -> SimpleNamespace:
    return SimpleNamespace(started_at=started_at, finished_at=finished_at)


def test_last_progress_prefers_task_timestamps() -> None:
    job = _job(reviewed_at=NOW - timedelta(hours=5))
    tasks = [
        _task(started_at=NOW - timedelta(hours=5), finished_at=NOW - timedelta(hours=4)),
        _task(started_at=NOW - timedelta(minutes=30)),
    ]
    assert svc._last_progress_at(job, tasks) == NOW - timedelta(minutes=30)


def test_last_progress_falls_back_to_review_time() -> None:
    job = _job(reviewed_at=NOW - timedelta(hours=3))
    assert svc._last_progress_at(job, []) == NOW - timedelta(hours=3)


def test_last_progress_falls_back_to_creation_time() -> None:
    job = _job()
    assert svc._last_progress_at(job, []) == NOW - timedelta(days=1)


def test_naive_timestamps_are_treated_as_utc() -> None:
    job = _job(reviewed_at=(NOW - timedelta(hours=3)).replace(tzinfo=None))
    assert svc._last_progress_at(job, []) == NOW - timedelta(hours=3)
