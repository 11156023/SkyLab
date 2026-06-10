"""Regression tests for the deletion-request scheduler safety net.

Covers two zombie scenarios fixed in ``process_pending_deletions``:
- a tick failure used to leave the row stuck in ``running`` forever (later
  ticks only query ``pending``), which also blocks new deletion requests for
  the same vmid;
- a server restart mid-deletion leaves ``running`` rows nobody re-picks —
  the tick now recovers stale running rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models import DeletionRequest, DeletionRequestStatus
from app.services.resource import deletion_service


def _request(
    *,
    status: DeletionRequestStatus,
    started_at: datetime | None = None,
) -> DeletionRequest:
    return DeletionRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        vmid=150,
        name="ct-150",
        node="pve1",
        resource_type="lxc",
        status=status,
        created_at=datetime.now(timezone.utc),
        started_at=started_at,
    )


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Returns queued query results in order; records mutations."""

    def __init__(self, results: list[list[Any]], get_map: dict[Any, Any]) -> None:
        self._results = list(results)
        self._get_map = get_map
        self.rolled_back = 0
        self.committed = 0
        self.added: list[Any] = []

    def exec(self, stmt: Any) -> _FakeResult:  # noqa: ARG002
        return _FakeResult(self._results.pop(0))

    def get(self, model: Any, key: Any) -> Any:  # noqa: ARG002
        return self._get_map.get(key)

    def rollback(self) -> None:
        self.rolled_back += 1

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.committed += 1


def test_tick_failure_finalizes_request_as_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.pending)
    session = _FakeSession(results=[[req], []], get_map={req.id: req})

    def exploding_execute(s: Any, r: DeletionRequest) -> None:  # noqa: ARG001
        # mimic the real flow: pending → running, then the deletion blows up
        r.status = DeletionRequestStatus.running
        raise RuntimeError("proxmox down")

    monkeypatch.setattr(deletion_service, "_execute_deletion", exploding_execute)

    deletion_service.process_pending_deletions(session)  # must not raise

    assert req.status == DeletionRequestStatus.failed
    assert "proxmox down" in (req.error_message or "")
    assert req.completed_at is not None
    assert session.rolled_back == 1
    assert session.committed == 1


def test_stale_running_request_is_recovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _request(
        status=DeletionRequestStatus.running,
        started_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    session = _FakeSession(results=[[], [stale]], get_map={stale.id: stale})

    executed: list[DeletionRequest] = []
    monkeypatch.setattr(
        deletion_service,
        "_execute_deletion",
        lambda s, r: executed.append(r),  # noqa: ARG005
    )

    deletion_service.process_pending_deletions(session)

    assert executed == [stale]


def test_successful_tick_does_not_touch_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.pending)
    session = _FakeSession(results=[[req], []], get_map={req.id: req})

    monkeypatch.setattr(
        deletion_service, "_execute_deletion", lambda s, r: None  # noqa: ARG005
    )

    deletion_service.process_pending_deletions(session)

    assert req.status == DeletionRequestStatus.pending  # untouched by the wrapper
    assert session.rolled_back == 0
    assert session.committed == 0
