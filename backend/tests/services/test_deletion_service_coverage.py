"""Coverage tests for app.services.resource.deletion_service."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.models import DeletionRequest, DeletionRequestStatus
from app.services.resource import deletion_service

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _request(
    *,
    status: DeletionRequestStatus = DeletionRequestStatus.pending,
    node: str | None = "pve1",
    resource_type: str | None = "lxc",
    resource_vmid: int | None = 150,
    created_at: datetime = _NOW,
) -> DeletionRequest:
    return DeletionRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        vmid=150,
        resource_vmid=resource_vmid,
        name="ct-150",
        node=node,
        resource_type=resource_type,
        status=status,
        created_at=created_at,
    )


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value

    def all(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal session double: queued exec results, a get map, counters."""

    def __init__(
        self,
        *,
        exec_results: list[Any] | None = None,
        get_map: dict[Any, Any] | None = None,
        rowcount: int = 1,
        commit_error: Exception | None = None,
        rollback_error: Exception | None = None,
    ) -> None:
        self.exec_results = list(exec_results or [])
        self.get_map = get_map or {}
        self.rowcount = rowcount
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed: list[Any] = []
        self.executed: list[Any] = []

    def exec(self, _stmt: Any) -> _Result:
        return _Result(self.exec_results.pop(0))

    def execute(self, stmt: Any) -> SimpleNamespace:
        self.executed.append(stmt)
        return SimpleNamespace(rowcount=self.rowcount)

    def get(self, _model: Any, key: Any) -> Any:
        value = self.get_map.get(key)
        if isinstance(value, Exception):
            raise value
        return value

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            err, self.commit_error = self.commit_error, None
            raise err

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    # context manager support for ``with Session(engine) as session``
    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deletion_service, "_utc_now", lambda: _NOW)


# ── helpers: presence check / template guard / queue / worker ─────────────


def _patch_connections(monkeypatch: pytest.MonkeyPatch, ids: list[int | None]) -> None:
    monkeypatch.setattr(
        deletion_service.proxmox_connection_repo,
        "get_all_connections",
        lambda _s: [SimpleNamespace(id=i) for i in ids],
    )


def test_ensure_absent_passes_when_vmid_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_connections(monkeypatch, [])
    asked: list[Any] = []

    def _find(vmid: int, ids: list[Any]) -> None:
        asked.append(ids)
        return None

    monkeypatch.setattr(
        deletion_service.proxmox_service, "find_vmid_on_connections", _find
    )
    deletion_service._ensure_vm_absent_everywhere(_FakeSession(), 150)  # type: ignore[arg-type]
    assert asked == [[None]]


# ── create_deletion_request ────────────────────────────────────────────────


def _integrity_error() -> IntegrityError:
    return IntegrityError("insert", {}, Exception("duplicate"))


def test_create_deletion_request_inserts_new_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deletion_service.resource_repo, "linked_resource_vmid", lambda _s, v: v
    )
    session = _FakeSession(exec_results=[None])
    req = deletion_service.create_deletion_request(
        session=session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        vmid=150,
        resource_info={"name": "ct", "node": "pve1", "type": "lxc"},
    )
    assert req.status == DeletionRequestStatus.pending
    assert req.resource_vmid == 150
    assert req.node == "pve1"
    assert session.commits == 1
    assert session.refreshed == [req]


def test_create_deletion_request_integrity_race_returns_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deletion_service.resource_repo, "linked_resource_vmid", lambda _s, _v: None
    )
    winner = _request()
    session = _FakeSession(exec_results=[None, winner], commit_error=_integrity_error())
    req = deletion_service.create_deletion_request(
        session=session,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        vmid=150,
        resource_info={},
    )
    assert req is winner
    assert session.rollbacks == 1


def test_create_deletion_request_integrity_error_without_winner_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deletion_service.resource_repo, "linked_resource_vmid", lambda _s, _v: None
    )
    session = _FakeSession(exec_results=[None, None], commit_error=_integrity_error())
    with pytest.raises(IntegrityError):
        deletion_service.create_deletion_request(
            session=session,  # type: ignore[arg-type]
            user_id=uuid.uuid4(),
            vmid=150,
            resource_info={},
        )
    assert session.rollbacks == 1


# ── _ensure_not_template_update_temp_vm ───────────────────────────────────


# ── request_deletion ───────────────────────────────────────────────────────


_USER = SimpleNamespace(id=uuid.uuid4(), email="u@example.com")


def _patch_request_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    db_resource: Any,
    is_admin: bool,
    find: Any,
) -> dict[str, Any]:
    state: dict[str, Any] = {"enqueued": [], "created": []}
    monkeypatch.setattr(
        deletion_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kw: db_resource,
    )
    monkeypatch.setattr(
        deletion_service, "can_bypass_resource_ownership", lambda _u: is_admin
    )

    def _find(vmid: int) -> dict[str, Any]:
        if isinstance(find, Exception):
            raise find
        return find

    monkeypatch.setattr(deletion_service.proxmox_service, "find_resource", _find)
    return state


def test_request_deletion_admin_orphan_without_db_record_nothing_to_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_request_deps(
        monkeypatch,
        db_resource=None,
        is_admin=True,
        find=NotFoundError("gone"),
    )
    result = deletion_service.request_deletion(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=_USER,  # type: ignore[arg-type]
        vmid=150,
    )
    assert result.status == DeletionRequestStatus.completed
    assert result.vmid == 150


def test_request_deletion_refuses_platform_template_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 平台範本的母機在 PVE 端是沒有資源紀錄的一般機器，不能從資源頁刪
    _patch_request_deps(
        monkeypatch,
        db_resource=None,
        is_admin=True,
        find={"vmid": 213},
    )
    monkeypatch.setattr(
        deletion_service.proxmox_service, "platform_template_vmids", lambda: {213}
    )
    with pytest.raises(ConflictError):
        deletion_service.request_deletion(
            session=_FakeSession(),  # type: ignore[arg-type]
            user=_USER,  # type: ignore[arg-type]
            vmid=213,
        )


def test_request_deletion_non_admin_without_management_rights_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_request_deps(
        monkeypatch,
        db_resource=SimpleNamespace(vmid=150, environment_type=None),
        is_admin=False,
        find={"vmid": 150},
    )

    def _deny(**_kw: Any) -> None:
        raise PermissionDeniedError("no")

    monkeypatch.setattr(deletion_service, "require_resource_management", _deny)
    with pytest.raises(PermissionDeniedError):
        deletion_service.request_deletion(
            session=_FakeSession(),  # type: ignore[arg-type]
            user=_USER,  # type: ignore[arg-type]
            vmid=150,
        )


def test_request_deletion_queues_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_request_deps(
        monkeypatch,
        db_resource=None,
        is_admin=True,
        find={"vmid": 150, "node": "pve1", "type": "lxc", "name": "ct"},
    )
    req = _request()
    created_kwargs: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> DeletionRequest:
        created_kwargs.append(kwargs)
        return req

    enqueued: list[DeletionRequest] = []
    monkeypatch.setattr(deletion_service, "create_deletion_request", _create)
    monkeypatch.setattr(
        deletion_service,
        "enqueue_processing",
        lambda **kw: enqueued.append(kw["req"]),
    )
    result = deletion_service.request_deletion(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=_USER,  # type: ignore[arg-type]
        vmid=150,
        purge=False,
        force=True,
    )
    assert result.id == req.id
    assert result.status == DeletionRequestStatus.pending
    assert result.message == "Deletion request queued"
    assert enqueued == [req]
    assert created_kwargs[0]["purge"] is False
    assert created_kwargs[0]["force"] is True


def test_list_active_for_vmids_empty_short_circuits() -> None:
    assert (
        deletion_service.list_active_for_vmids(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmids=[],
        )
        == {}
    )


# ── _execute_deletion ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        DeletionRequestStatus.cancelled,
        DeletionRequestStatus.completed,
        DeletionRequestStatus.failed,
    ],
)
def test_execute_deletion_skips_non_active_requests(
    status: DeletionRequestStatus,
) -> None:
    req = _request(status=status)
    session = _FakeSession()
    deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]
    assert session.commits == 0
    assert req.status == status


def test_execute_deletion_missing_snapshot_marks_failed() -> None:
    req = _request(status=DeletionRequestStatus.running, node=None)
    session = _FakeSession(exec_results=[None])
    deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]
    assert req.status == DeletionRequestStatus.failed
    assert "missing node/resource_type" in (req.error_message or "")
    assert req.completed_at == _NOW
    assert session.commits == 1


def _patch_proxmox_status(
    monkeypatch: pytest.MonkeyPatch, status: dict[str, Any] | Exception
) -> None:
    from app.services.proxmox import proxmox_service

    def _get_status(_node: str, _vmid: int, _type: str) -> dict[str, Any]:
        if isinstance(status, Exception):
            raise status
        return status

    monkeypatch.setattr(proxmox_service, "get_status", _get_status)


def test_execute_deletion_claims_pending_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request()
    fresh = _request(status=DeletionRequestStatus.running)
    fresh.id = req.id
    # resource exists, belongs to this request (created before it)
    resource = SimpleNamespace(created_at=_NOW - timedelta(days=1), user_id=req.user_id)
    session = _FakeSession(exec_results=[resource], get_map={req.id: fresh})
    _patch_proxmox_status(monkeypatch, {"status": "running"})
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        deletion_service.resource_service,
        "delete",
        lambda **kw: seen.append(kw),
    )

    deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]

    assert seen[0]["resource_info"]["status"] == "running"
    assert seen[0]["resource_info"]["node"] == "pve1"
    assert fresh.status == DeletionRequestStatus.completed
    assert fresh.completed_at == _NOW
    # claim commit + completion commit
    assert session.commits == 2


def test_execute_deletion_status_lookup_failure_still_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.running)
    session = _FakeSession(exec_results=[None], get_map={})
    _patch_proxmox_status(monkeypatch, RuntimeError("pve down"))
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        deletion_service.resource_service,
        "delete",
        lambda **kw: seen.append(kw),
    )

    # fresh row vanished (None) → returns without finalizing
    deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]

    assert seen[0]["resource_info"]["status"] == ""
    assert session.commits == 0


def test_execute_deletion_cancelled_during_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.running)
    fresh = _request(status=DeletionRequestStatus.cancelled)
    session = _FakeSession(exec_results=[None], get_map={req.id: fresh})
    _patch_proxmox_status(monkeypatch, {})
    monkeypatch.setattr(deletion_service.resource_service, "delete", lambda **_kw: None)

    deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]

    assert fresh.status == DeletionRequestStatus.cancelled
    assert session.commits == 0


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_execute_deletion_delete_failure_rolls_back_and_reraises(
    monkeypatch: pytest.MonkeyPatch, rollback_fails: bool
) -> None:
    req = _request(status=DeletionRequestStatus.running)
    session = _FakeSession(
        exec_results=[None],
        rollback_error=RuntimeError("rollback broke") if rollback_fails else None,
    )
    _patch_proxmox_status(monkeypatch, {"status": "stopped"})

    def _boom(**_kw: Any) -> None:
        raise RuntimeError("delete failed")

    monkeypatch.setattr(deletion_service.resource_service, "delete", _boom)

    with pytest.raises(RuntimeError, match="delete failed"):
        deletion_service._execute_deletion(session, req)  # type: ignore[arg-type]
    assert session.rollbacks == 1
    assert req.status == DeletionRequestStatus.running


# ── process_one_request ────────────────────────────────────────────────────


def _patch_sessions(
    monkeypatch: pytest.MonkeyPatch, sessions: list[_FakeSession]
) -> list[_FakeSession]:
    queue = list(sessions)
    monkeypatch.setattr(deletion_service, "Session", lambda _engine: queue.pop(0))
    return queue


def test_process_one_request_missing_row_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remaining = _patch_sessions(monkeypatch, [_FakeSession()])
    deletion_service.process_one_request(uuid.uuid4())
    assert remaining == []


def test_process_one_request_terminal_row_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.completed)
    remaining = _patch_sessions(monkeypatch, [_FakeSession(get_map={req.id: req})])
    called: list[Any] = []
    monkeypatch.setattr(
        deletion_service, "_execute_deletion", lambda *a: called.append(a)
    )
    deletion_service.process_one_request(req.id)
    assert called == []
    assert remaining == []


def test_process_one_request_success_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request()
    _patch_sessions(monkeypatch, [_FakeSession(get_map={req.id: req})])
    called: list[Any] = []
    monkeypatch.setattr(
        deletion_service, "_execute_deletion", lambda _s, r: called.append(r)
    )
    deletion_service.process_one_request(req.id)
    assert called == [req]


def test_process_one_request_retries_then_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _request(status=DeletionRequestStatus.running)
    final = _FakeSession(get_map={req.id: req})
    _patch_sessions(
        monkeypatch,
        [
            _FakeSession(get_map={req.id: req}),
            _FakeSession(get_map={req.id: req}),
            final,
        ],
    )
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    def _boom(_s: Any, _r: Any) -> None:
        raise RuntimeError("x" * 3000)

    monkeypatch.setattr(deletion_service, "_execute_deletion", _boom)

    deletion_service.process_one_request(
        req.id, max_retries=1, retry_delay=200.0, retry_backoff=2.0
    )

    assert sleeps == [200.0]
    assert req.status == DeletionRequestStatus.failed
    assert req.error_message == "x" * 2000
    assert req.completed_at == _NOW
    assert final.commits == 1


@pytest.mark.parametrize(
    "final_value",
    [
        None,
        DeletionRequestStatus.cancelled,
        DeletionRequestStatus.completed,
        DeletionRequestStatus.failed,
    ],
)
def test_process_one_request_exhausted_respects_final_state(
    monkeypatch: pytest.MonkeyPatch, final_value: DeletionRequestStatus | None
) -> None:
    req = _request(status=DeletionRequestStatus.running)
    if final_value is None:
        final = _FakeSession()
    else:
        final_req = _request(status=final_value)
        final = _FakeSession(get_map={req.id: final_req})
    _patch_sessions(monkeypatch, [_FakeSession(get_map={req.id: req}), final])
    monkeypatch.setattr("time.sleep", lambda _d: None)

    def _boom(_s: Any, _r: Any) -> None:
        raise RuntimeError("nope")

    monkeypatch.setattr(deletion_service, "_execute_deletion", _boom)

    deletion_service.process_one_request(req.id, max_retries=0, retry_delay=-5)

    assert final.commits == 0
    assert req.status == DeletionRequestStatus.running


# ── process_pending_deletions ──────────────────────────────────────────────


def test_tick_finalize_failure_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    session = _FakeSession(
        exec_results=[[req], []],
        get_map={req.id: RuntimeError("db gone")},
    )

    def _boom(_s: Any, _r: Any) -> None:
        raise RuntimeError("delete exploded")

    monkeypatch.setattr(deletion_service, "_execute_deletion", _boom)

    deletion_service.process_pending_deletions(session)  # type: ignore[arg-type]

    assert session.rollbacks == 1
    assert session.commits == 0
