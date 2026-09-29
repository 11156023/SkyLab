"""Regression tests for the jobs WebSocket snapshot helper.

The long-lived WS session must expire its identity map before each poll
(otherwise job status never updates) and end its transaction afterwards
(otherwise the connection idles in-transaction for the WS lifetime — and a
failed poll would leave the session permanently broken).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.websocket import jobs as jobs_ws
from app.services.jobs import jobs_service


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def expire_all(self) -> None:
        self.calls.append("expire_all")

    def rollback(self) -> None:
        self.calls.append("rollback")


def test_fetch_snapshot_expires_then_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()
    sentinel = object()

    def fake_list_recent(*, session: Any, user: Any, limit: int) -> Any:
        session.calls.append("query")
        return sentinel

    monkeypatch.setattr(jobs_service, "list_recent_for_user", fake_list_recent)

    result = jobs_ws._fetch_snapshot(
        session, user=object(), limit=20, include_reminders=False
    )

    assert result is sentinel
    assert session.calls == ["expire_all", "query", "rollback"]


def test_fetch_snapshot_rolls_back_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()

    def failing_list_recent(**kwargs: Any) -> Any:
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_service, "list_recent_for_user", failing_list_recent)

    with pytest.raises(RuntimeError):
        jobs_ws._fetch_snapshot(
            session, user=object(), limit=20, include_reminders=False
        )

    # rollback in finally keeps the session usable for the next poll cycle
    assert session.calls == ["expire_all", "rollback"]


def test_fetch_snapshot_attaches_reminders_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()

    class _Snapshot:
        reminders: Any = None

    snapshot = _Snapshot()

    def fake_list_recent(*, session: Any, user: Any, limit: int) -> Any:
        session.calls.append("query")
        return snapshot

    def fake_reminders(session: Any, *, user_id: Any, now: Any = None) -> Any:
        session.calls.append("reminders")
        return ["reminder"]

    monkeypatch.setattr(jobs_service, "list_recent_for_user", fake_list_recent)
    monkeypatch.setattr(
        jobs_ws.reminder_service, "list_student_reminders", fake_reminders
    )

    class _User:
        id = "user-id"

    result = jobs_ws._fetch_snapshot(
        session, user=_User(), limit=20, include_reminders=True
    )

    assert result.reminders == ["reminder"]
    assert session.calls == ["expire_all", "query", "reminders", "rollback"]


def test_recent_jobs_are_shared_briefly_and_returned_as_copies(monkeypatch) -> None:
    """同一位使用者的多個分頁／REST／推播在 TTL 內共用一次彙總，且互不汙染。"""
    from types import SimpleNamespace

    from app.schemas.jobs import JobsListResponse
    from app.services.jobs import jobs_service

    builds: list[int] = []

    def fake_build(*, session, user, limit, own_only):
        builds.append(limit)
        return JobsListResponse(items=[], total=0, active_count=0)

    monkeypatch.setattr(jobs_service, "_build_recent_for_user", fake_build)
    user = SimpleNamespace(id="u1")

    first = jobs_service.list_recent_for_user(session=None, user=user, limit=20)
    first.reminders = ["mutated"]
    second = jobs_service.list_recent_for_user(session=None, user=user, limit=20)
    jobs_service.list_recent_for_user(session=None, user=user, limit=5)

    assert builds == [20, 5]
    assert second.reminders != ["mutated"]

    monkeypatch.setattr(jobs_service, "_RECENT_CACHE_TTL_SECONDS", 0.0)
    jobs_service.list_recent_for_user(session=None, user=user, limit=20)
    assert builds == [20, 5, 20]
