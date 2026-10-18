"""app.services.scheduling.leader 的 advisory lock context manager 測試（假 engine）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.services.scheduling import leader


class _FakeConnection:
    def __init__(self, acquired: Any, *, rollback_error: bool = False) -> None:
        self.acquired = acquired
        self.rollback_error = rollback_error
        self.executed: list[tuple[str, dict]] = []
        self.rollbacks = 0
        self.closed = False

    def execute(self, statement, params):
        self.executed.append((str(statement), params))
        return SimpleNamespace(scalar=lambda: self.acquired)

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error:
            raise RuntimeError("connection already closed")

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.closed = True
        return False


def _fake_engine(dialect: str, connection: _FakeConnection | None = None):
    return SimpleNamespace(
        dialect=SimpleNamespace(name=dialect),
        connect=lambda: connection,
    )


def test_non_postgres_is_always_leader(monkeypatch) -> None:
    monkeypatch.setattr(leader, "engine", _fake_engine("sqlite"))
    with leader.scheduler_leader_lock() as acquired:
        assert acquired is True


def test_postgres_rollback_failure_is_logged_not_raised(monkeypatch, caplog) -> None:
    connection = _FakeConnection(True, rollback_error=True)
    monkeypatch.setattr(leader, "engine", _fake_engine("postgresql", connection))

    with caplog.at_level("WARNING", logger=leader.logger.name):
        with leader.scheduler_leader_lock() as acquired:
            assert acquired is True

    assert connection.rollbacks == 1
    assert "Failed to release scheduler leader lock" in caplog.text
