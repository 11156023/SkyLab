"""Coverage for app.domain.scheduling.runner (recovery / gate failures) and app.domain.username_policy loader."""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.domain import username_policy
from app.domain.scheduling import runner
from app.domain.scheduling.models import ScheduledTask


def _op_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("db down"))


class _Observer:
    def __init__(self, stop_event: asyncio.Event, *, stop_after_ticks: int) -> None:
        self.stop_event = stop_event
        self.stop_after_ticks = stop_after_ticks
        self.ticks: list[bool] = []
        self.tasks: list[tuple[str, bool]] = []

    async def on_start(self, task_names: list[str]) -> None:
        return None

    async def on_tick(self, *, is_leader: bool) -> None:
        self.ticks.append(is_leader)
        if len(self.ticks) >= self.stop_after_ticks:
            self.stop_event.set()

    async def on_task(
        self,
        task_name: str,
        *,
        ok: bool,
        duration_seconds: float,
        error: BaseException | None,
    ) -> None:
        self.tasks.append((task_name, ok))


async def test_scheduler_recovers_after_database_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop_event = asyncio.Event()
    outcomes: list[Exception | None] = [_op_error(), None]

    async def _fake_run(task: ScheduledTask) -> object:
        outcome = outcomes.pop(0)
        if outcome is not None:
            raise outcome
        return None

    monkeypatch.setattr(runner, "run_sync_task", _fake_run)
    observer = _Observer(stop_event, stop_after_ticks=2)
    caplog.set_level("INFO", logger=runner.logger.name)

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop_event,
            interval_seconds=0,
            tasks=[ScheduledTask(name="job", handler=lambda: None)],
            observer=observer,
        ),
        5,
    )

    assert observer.tasks == [("job", False), ("job", True)]
    assert "database connection recovered" in caplog.text


class _FailingGate(AbstractContextManager[bool]):
    def __init__(self, error: BaseException | None) -> None:
        self.error = error

    def __enter__(self) -> bool:
        if self.error is not None:
            raise self.error
        return False

    def __exit__(self, *_exc: Any) -> None:
        return None


async def test_scheduler_survives_leader_gate_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop_event = asyncio.Event()
    errors: list[BaseException | None] = [
        _op_error(),
        _op_error(),
        RuntimeError("gate exploded"),
        None,
    ]

    def _gate() -> _FailingGate:
        return _FailingGate(errors.pop(0))

    observer = _Observer(stop_event, stop_after_ticks=1)
    caplog.set_level("INFO", logger=runner.logger.name)

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop_event,
            interval_seconds=0,
            tasks=[],
            leader_gate=_gate,
            observer=observer,
        ),
        5,
    )

    assert observer.ticks == [False]
    assert caplog.text.count("database is unavailable") == 1
    assert "Scheduler leader gate failed" in caplog.text


async def test_scheduler_logs_failed_task_without_observer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stop_event = asyncio.Event()

    async def _fake_run(task: ScheduledTask) -> object:
        stop_event.set()
        raise ValueError("task blew up")

    monkeypatch.setattr(runner, "run_sync_task", _fake_run)

    await asyncio.wait_for(
        runner.run_polling_scheduler(
            stop_event=stop_event,
            interval_seconds=0,
            tasks=[ScheduledTask(name="boom", handler=lambda: None)],
        ),
        5,
    )

    assert "Scheduled task 'boom' failed" in caplog.text


async def test_notify_swallows_observer_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Broken:
        async def on_start(self, task_names: list[str]) -> None:
            raise RuntimeError("observer down")

    await runner._notify(_Broken(), "on_start", ["a"])  # type: ignore[arg-type]

    assert "Scheduler observer on_start failed" in caplog.text


# ---------------------------------------------------------------- username_policy


def test_violation_to_dict() -> None:
    violation = username_policy.Violation(code="X", message="m")
    assert violation.to_dict() == {"code": "X", "message": "m", "severity": "error"}


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "username_policy.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_policy_rejects_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        username_policy.load_policy(_write(tmp_path, "- a\n- b\n"))


def test_load_policy_requires_linux_section(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="'linux' section"):
        username_policy.load_policy(_write(tmp_path, "platform_reserved: []\n"))


def test_load_policy_rejects_non_string_list(tmp_path: Path) -> None:
    text = (
        "linux:\n"
        "  pattern: '^[a-z]+$'\n"
        "  min_length: 1\n"
        "  max_length: 32\n"
        "  reserved: [root, 3]\n"
        "  reserved_prefixes: []\n"
        "  default_user_warn: []\n"
        "platform_reserved: []\n"
    )
    with pytest.raises(ValueError, match="'reserved' must be a list of strings"):
        username_policy.load_policy(_write(tmp_path, text))


def test_validate_username_field_passes_none() -> None:
    assert username_policy.validate_username_field(None) is None
