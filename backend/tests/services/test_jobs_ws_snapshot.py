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

    def fake_list_recent(*, session: Any, user: Any, limit: int) -> Any:  # noqa: ARG001
        session.calls.append("query")
        return sentinel

    monkeypatch.setattr(jobs_service, "list_recent_for_user", fake_list_recent)

    result = jobs_ws._fetch_snapshot(session, user=object(), limit=20)

    assert result is sentinel
    assert session.calls == ["expire_all", "query", "rollback"]


def test_fetch_snapshot_rolls_back_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _FakeSession()

    def failing_list_recent(**kwargs: Any) -> Any:  # noqa: ARG001
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(jobs_service, "list_recent_for_user", failing_list_recent)

    with pytest.raises(RuntimeError):
        jobs_ws._fetch_snapshot(session, user=object(), limit=20)

    # rollback in finally keeps the session usable for the next poll cycle
    assert session.calls == ["expire_all", "rollback"]
