"""被硬殺的 script run 會由排程 tick 從 running 收成 failed。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.ai.teacher_judge import script_executor_service as svc


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    def exec(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result(self.rows)


def test_reaper_marks_each_stale_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    stale_ids = [uuid.uuid4(), uuid.uuid4()]
    marked: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(
        svc,
        "_mark_run_executor_failed",
        lambda run_id, msg: marked.append((run_id, msg)),
    )
    session = _Session(stale_ids)

    reaped = svc.reap_stale_script_runs(
        session,  # type: ignore[arg-type]
        now=datetime.now(timezone.utc),
    )

    assert reaped == 2
    assert [run_id for run_id, _ in marked] == stale_ids
    assert all("reaped" in msg for _, msg in marked)


def test_reaper_query_targets_pending_and_running_before_cutoff() -> None:
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    session = _Session([])

    assert svc.reap_stale_script_runs(session, now=now) == 0  # type: ignore[arg-type]

    compiled = str(
        session.statements[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert "teacher_judge_script_runs.status IN" in compiled
    assert "pending" in compiled and "running" in compiled
    cutoff = now - timedelta(hours=svc.STALE_RUN_HOURS)
    assert cutoff.strftime("%Y-%m-%d %H:%M:%S") in compiled


def test_reaper_is_registered_on_scheduler() -> None:
    from app.services.scheduling import coordinator

    assert callable(coordinator.reap_stale_script_runs_task)
    assert "reap_stale_script_runs" in coordinator.run_scheduler.__code__.co_consts
