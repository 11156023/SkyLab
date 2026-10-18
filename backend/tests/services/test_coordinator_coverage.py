"""app.services.scheduling.coordinator 的分支覆蓋測試（全部以假 session／monkeypatch 進行）。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import NotFoundError
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.scheduling import coordinator
from app.services.scheduling import support as scheduling_support

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


class _FakeSession:
    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows or []
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.expired = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def expire_all(self) -> None:
        self.expired += 1

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def exec(self, _statement):
        rows = self.rows
        return SimpleNamespace(all=lambda: rows)

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _request(**overrides) -> VMRequest:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "hostname": "host-1",
        "resource_type": "vm",
        "status": VMRequestStatus.approved,
        "vmid": None,
        "cores": 2,
        "memory": 2048,
        "environment_type": "Custom",
    }
    defaults.update(overrides)
    return VMRequest(**defaults)


@pytest.fixture
def noop_audit(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        coordinator.audit_service, "log_action", lambda **kw: calls.append(kw)
    )
    return calls


# ---------------------------------------------------------------------------
# _sync_lxc_platform_key
# ---------------------------------------------------------------------------


def test_sync_lxc_platform_key_lxc_sets_key_and_password(monkeypatch) -> None:
    from app.services.resource import resource_service

    calls: list[str] = []
    monkeypatch.setattr(
        resource_service,
        "ensure_lxc_platform_key",
        lambda **kw: calls.append(f"key:{kw['node']}:{kw['vmid']}"),
    )
    monkeypatch.setattr(
        resource_service,
        "ensure_lxc_login_password",
        lambda **kw: calls.append(f"pw:{kw['node']}:{kw['vmid']}"),
    )
    monkeypatch.setattr(
        coordinator.guest_ssh_login,
        "schedule_after_start",
        lambda *a: calls.append("ssh"),
    )

    coordinator._sync_lxc_platform_key(
        session=_FakeSession(), node="pve1", vmid=101, resource_type="lxc"
    )

    assert calls == ["key:pve1:101", "pw:pve1:101"]


# ---------------------------------------------------------------------------
# _adopt_existing_resource
# ---------------------------------------------------------------------------


def test_adopt_returns_none_when_no_existing_resource(monkeypatch) -> None:
    monkeypatch.setattr(
        coordinator.scheduling_support,
        "find_existing_resource_for_request",
        lambda **kw: None,
    )
    assert (
        coordinator._adopt_existing_resource(session=_FakeSession(), request=_request())
        is None
    )


def test_adopt_untracked_lxc_creates_resource_and_starts(
    monkeypatch, noop_audit
) -> None:
    req = _request(
        resource_type="lxc",
        password="enc-copy",
        password_hash="hash-copy",
        desired_node=None,
        assigned_node=None,
        placement_strategy_used=None,
    )
    session = _FakeSession()
    created: dict = {}
    updates: list[dict] = []
    controls: list[tuple] = []
    synced: list[dict] = []

    monkeypatch.setattr(
        coordinator.scheduling_support,
        "find_existing_resource_for_request",
        lambda **kw: {"vmid": "321", "node": "pve2"},
    )
    monkeypatch.setattr(
        coordinator.resource_repo, "get_resource_by_vmid", lambda **kw: None
    )
    monkeypatch.setattr(
        coordinator.provisioning_service,
        "request_password_plan",
        lambda r: {"plan": "lxc"},
    )
    monkeypatch.setattr(
        coordinator.provisioning_service,
        "pending_login_password_encrypted",
        lambda plan: "pending-enc",
    )
    monkeypatch.setattr(
        coordinator.provisioning_service,
        "pending_login_password_hash",
        lambda plan: "pending-hash",
    )
    monkeypatch.setattr(coordinator, "get_connection_id_for_node", lambda node: 7)
    monkeypatch.setattr(
        coordinator.resource_repo, "create_resource", lambda **kw: created.update(kw)
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "update_vm_request_provisioning",
        lambda **kw: updates.append(kw),
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": "stopped"},
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "control",
        lambda *a, **k: controls.append(a),
    )
    monkeypatch.setattr(
        coordinator, "_sync_lxc_platform_key", lambda **kw: synced.append(kw)
    )

    result = coordinator._adopt_existing_resource(session=session, request=req)

    assert result == (
        321,
        "pve2",
        coordinator.vm_request_placement_service.DEFAULT_PLACEMENT_STRATEGY,
        True,
    )
    assert created["connection_id"] == 7
    assert created["login_password_pending_encrypted"] == "pending-enc"
    assert created["login_password_pending_hash"] == "pending-hash"
    assert req.password is None
    assert req.password_hash is None
    assert req in session.added
    assert updates[0]["assigned_node"] == "pve2"
    assert controls == [("pve2", 321, "lxc", "start")]
    assert synced[0]["resource_type"] == "lxc"
    assert noop_audit[0]["action"] == "resource_start"


# ---------------------------------------------------------------------------
# _provision_new_resource
# ---------------------------------------------------------------------------


def _patch_plan_phase(monkeypatch, plan: dict | Exception) -> None:
    from app.services.resource import quota_service

    monkeypatch.setattr(quota_service, "check_quota_for_provision", lambda s, r: None)
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "vmid_allocation_lock",
        lambda db_engine: nullcontext(),
    )

    def _plan(**_kw):
        if isinstance(plan, Exception):
            raise plan
        return plan

    monkeypatch.setattr(coordinator.provisioning_service, "plan_provision", _plan)


def test_provision_plan_failure_marks_request_failed(monkeypatch) -> None:
    req = _request()
    session = _FakeSession()
    _patch_plan_phase(monkeypatch, RuntimeError("no storage"))
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )

    with pytest.raises(RuntimeError, match="no storage"):
        coordinator._provision_new_resource(session=session, request=req)

    assert session.rollbacks == 1
    assert req.provisioning_status == VMProvisioningStatus.failed
    assert req.provisioning_error == "Failed to plan provisioning: no storage"


def test_provision_execute_failure_releases_ip_and_marks_failed(monkeypatch) -> None:
    req = _request()
    plan = {"vmid": 500, "ip_reservation_key": "rk"}
    _patch_plan_phase(monkeypatch, plan)
    rollback_session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: rollback_session)
    released: list[tuple] = []
    monkeypatch.setattr(
        coordinator.ip_management_service,
        "release_ip",
        lambda s, vmid, **kw: released.append((vmid, kw)),
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )

    def _execute(_plan):
        raise RuntimeError("clone failed")

    monkeypatch.setattr(coordinator.provisioning_service, "execute_provision", _execute)

    with pytest.raises(RuntimeError, match="clone failed"):
        coordinator._provision_new_resource(session=_FakeSession(), request=req)

    assert released == [(500, {"restore_reservation": True, "reservation_key": "rk"})]
    assert req.provisioning_status == VMProvisioningStatus.failed
    assert req.provisioning_error == "Failed to execute provisioning: clone failed"
    assert rollback_session.commits == 2


def test_provision_execute_failure_tolerates_release_error(monkeypatch) -> None:
    req = _request()
    _patch_plan_phase(monkeypatch, {"vmid": 501})
    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())

    def _release(*_a, **_kw):
        raise RuntimeError("ip db down")

    monkeypatch.setattr(coordinator.ip_management_service, "release_ip", _release)
    # 申請單已有 vmid（別的 worker 寫回）時不覆寫成 failed
    already = _request(vmid=777, provisioning_status=VMProvisioningStatus.completed)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: already
    )
    monkeypatch.setattr(
        coordinator.provisioning_service,
        "execute_provision",
        lambda plan: (_ for _ in ()).throw(RuntimeError("clone failed")),
    )

    with pytest.raises(RuntimeError):
        coordinator._provision_new_resource(session=_FakeSession(), request=req)

    assert already.provisioning_status == VMProvisioningStatus.completed


def test_provision_request_vanished_after_clone_raises(monkeypatch) -> None:
    req = _request()
    _patch_plan_phase(monkeypatch, {"vmid": 502})
    monkeypatch.setattr(
        coordinator.provisioning_service, "execute_provision", lambda p: (502, "pve1")
    )
    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())
    # finish 階段鎖定重讀時申請單已不存在
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: None
    )

    with pytest.raises(NotFoundError):
        coordinator._provision_new_resource(session=_FakeSession(), request=req)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_provision_orphan_removed_when_request_no_longer_approved(
    monkeypatch, cleanup_fails: bool
) -> None:
    req = _request()
    _patch_plan_phase(
        monkeypatch,
        {"vmid": 503, "resource_type": "qemu", "ip_reservation_key": None},
    )
    monkeypatch.setattr(
        coordinator.provisioning_service, "execute_provision", lambda p: (503, "pve1")
    )
    finish_session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: finish_session)
    cancelled = _request(status=VMRequestStatus.cancelled)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: cancelled
    )
    cleaned: list[tuple] = []
    released: list[int] = []

    def _cleanup(node, vmid, rtype):
        if cleanup_fails:
            raise RuntimeError("pve gone")
        cleaned.append((node, vmid, rtype))

    def _release(_s, vmid, **_kw):
        if cleanup_fails:
            raise RuntimeError("ip db down")
        released.append(vmid)

    monkeypatch.setattr(
        coordinator.provisioning_service, "cleanup_failed_resource", _cleanup
    )
    monkeypatch.setattr(coordinator.ip_management_service, "release_ip", _release)

    result = coordinator._provision_new_resource(session=_FakeSession(), request=req)

    assert result is None
    if cleanup_fails:
        assert cleaned == [] and released == []
        assert finish_session.commits == 1
    else:
        assert cleaned == [("pve1", 503, "qemu")]
        assert released == [503]
        assert finish_session.commits == 2


# ---------------------------------------------------------------------------
# _mark_request_runtime_error / _refresh_actual_node
# ---------------------------------------------------------------------------


def test_mark_request_runtime_error_delegates(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        coordinator.scheduling_support,
        "mark_request_runtime_error",
        lambda **kw: calls.append(kw),
    )
    rid = uuid.uuid4()
    coordinator._mark_request_runtime_error(
        session=_FakeSession(), request_id=rid, message="bad"
    )
    assert calls[0]["request_id"] == rid
    assert calls[0]["message"] == "bad"


def test_refresh_actual_node_requires_vmid(monkeypatch) -> None:
    req = _request(vmid=None)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: None
    )
    with pytest.raises(NotFoundError, match="no provisioned VMID"):
        coordinator._refresh_actual_node(session=_FakeSession(), request=req)


def test_refresh_actual_node_rejects_hostname_mismatch(monkeypatch) -> None:
    req = _request(vmid=600, hostname="mine")
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )
    monkeypatch.setattr(
        coordinator.scheduling_support,
        "find_resource_strict",
        lambda vmid: {"name": "someone-else", "node": "pve1"},
    )
    with pytest.raises(NotFoundError, match="does not match"):
        coordinator._refresh_actual_node(session=_FakeSession(), request=req)


def test_refresh_actual_node_skips_write_when_already_recorded(monkeypatch) -> None:
    req = _request(
        vmid=601,
        hostname="h",
        assigned_node="pve1",
        desired_node="pve1",
        actual_node="pve1",
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )
    monkeypatch.setattr(
        coordinator.scheduling_support,
        "find_resource_strict",
        lambda vmid: {"name": "h", "node": "pve1"},
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "update_vm_request_provisioning",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no write")),
    )
    node, entry = coordinator._refresh_actual_node(session=_FakeSession(), request=req)
    assert node == "pve1"
    assert entry["name"] == "h"


# ---------------------------------------------------------------------------
# _adopt_or_provision_due_request
# ---------------------------------------------------------------------------


def test_adopt_or_provision_skips_when_locked_elsewhere(monkeypatch) -> None:
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: None
    )
    assert (
        coordinator._adopt_or_provision_due_request(
            session=_FakeSession(), request=_request()
        )
        is None
    )


def test_adopt_or_provision_skips_when_vmid_already_set(monkeypatch) -> None:
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: _request(vmid=700),
    )
    assert (
        coordinator._adopt_or_provision_due_request(
            session=_FakeSession(), request=_request()
        )
        is None
    )


def test_adopt_or_provision_skips_fresh_running_provision(monkeypatch) -> None:
    locked = _request(
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=NOW - timedelta(minutes=1),
    )
    monkeypatch.setattr(coordinator, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: locked
    )
    assert (
        coordinator._adopt_or_provision_due_request(
            session=_FakeSession(), request=locked
        )
        is None
    )


def test_adopt_or_provision_takes_over_stale_and_adopts(monkeypatch) -> None:
    locked = _request(
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=NOW - timedelta(hours=2),
    )
    session = _FakeSession()
    monkeypatch.setattr(coordinator, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: locked
    )
    monkeypatch.setattr(
        coordinator,
        "_adopt_existing_resource",
        lambda **kw: (800, "pve1", "balanced", True),
    )

    result = coordinator._adopt_or_provision_due_request(
        session=session, request=locked
    )

    assert result == (800, "pve1", "balanced", True)
    assert session.commits == 1


@pytest.mark.parametrize("refreshed_vmid", [None, 801])
def test_adopt_or_provision_falls_back_to_provision(
    monkeypatch, refreshed_vmid: int | None
) -> None:
    locked = _request(provisioning_started_at=NOW - timedelta(hours=2))
    refreshed = _request(
        id=locked.id,
        vmid=refreshed_vmid,
        actual_node="pve2",
        placement_strategy_used="balanced",
    )
    lookups = iter([locked, refreshed])
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: next(lookups),
    )
    monkeypatch.setattr(coordinator, "_adopt_existing_resource", lambda **kw: None)
    provisioned: list = []
    monkeypatch.setattr(
        coordinator,
        "_provision_new_resource",
        lambda **kw: provisioned.append(kw["request"]),
    )

    result = coordinator._adopt_or_provision_due_request(
        session=_FakeSession(), request=locked
    )

    assert provisioned == [locked]
    if refreshed_vmid is None:
        assert result is None
    else:
        assert result == (801, "pve2", "balanced", True)


# ---------------------------------------------------------------------------
# _ensure_request_running
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(None, False), ((1, "pve1", "b", True), True), ((1, "pve1", "b", False), False)],
)
def test_ensure_running_without_vmid_delegates_to_provision(
    monkeypatch, outcome, expected
) -> None:
    monkeypatch.setattr(
        coordinator, "_adopt_or_provision_due_request", lambda **kw: outcome
    )
    assert (
        coordinator._ensure_request_running(session=_FakeSession(), request=_request())
        is expected
    )


# ---------------------------------------------------------------------------
# process_single_request_start
# ---------------------------------------------------------------------------


def test_single_start_returns_false_for_missing_request(monkeypatch) -> None:
    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: None
    )
    assert coordinator.process_single_request_start(uuid.uuid4()) is False


def test_single_start_runs_and_reconciles_quick_practice(monkeypatch) -> None:
    from app.services import quick_practice

    req = _request()
    session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )
    monkeypatch.setattr(coordinator, "_ensure_request_running", lambda **kw: True)
    reconciled: list = []
    monkeypatch.setattr(
        quick_practice,
        "reconcile_for_request",
        lambda s, *, request_id: reconciled.append(request_id),
    )

    assert coordinator.process_single_request_start(req.id) is True
    assert reconciled == [req.id]
    assert session.expired == 1
    # 建機結果先 commit 一次，拓撲再 commit 一次
    assert session.commits == 2


def test_single_start_keeps_provisioning_when_reconcile_fails(monkeypatch) -> None:
    """快速練習的拓撲／發布出錯不能把建機結果 rollback，也不能把機器標成失敗。"""
    from app.services import quick_practice

    req = _request(vmid=55)
    session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: req
    )
    monkeypatch.setattr(coordinator, "_ensure_request_running", lambda **kw: True)
    marked: list = []
    monkeypatch.setattr(
        coordinator, "_mark_request_runtime_error", lambda **kw: marked.append(kw)
    )

    def broken(_session, *, request_id):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(quick_practice, "reconcile_for_request", broken)

    assert coordinator.process_single_request_start(req.id) is True
    assert session.commits == 1
    assert marked == []


# ---------------------------------------------------------------------------
# process_due_request_starts
# ---------------------------------------------------------------------------


def _patch_due_starts(monkeypatch, requests: list[VMRequest]) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(coordinator, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "list_active_approved_vm_requests",
        lambda **kw: requests,
    )
    monkeypatch.setattr(
        coordinator.governance_repo,
        "get_governance_config",
        lambda **kw: SimpleNamespace(provision_max_concurrency=3),
    )
    monkeypatch.setattr(coordinator.time, "sleep", lambda s: None)
    return session


def test_due_starts_submits_unprovisioned_and_skips_in_flight(monkeypatch) -> None:
    in_flight = _request(
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=NOW - timedelta(minutes=1),
    )
    pending = _request(provisioning_status=VMProvisioningStatus.pending)
    _patch_due_starts(monkeypatch, [in_flight, pending])
    submitted: list[dict] = []
    monkeypatch.setattr(
        coordinator.provision_pool,
        "submit_provision",
        lambda s, **kw: submitted.append(kw),
    )

    assert coordinator.process_due_request_starts() == 0
    assert [s["request_id"] for s in submitted] == [pending.id]
    assert submitted[0]["concurrency"] == 3


def test_due_starts_counts_started_and_handles_generic_error(monkeypatch) -> None:
    ok = _request(vmid=10)
    bad = _request(vmid=11)
    session = _patch_due_starts(monkeypatch, [ok, bad])

    def _ensure(*, session, request):
        if request is bad:
            raise RuntimeError("weird")
        return True

    monkeypatch.setattr(coordinator, "_ensure_request_running", _ensure)
    marks: list[dict] = []
    monkeypatch.setattr(
        coordinator, "_mark_request_runtime_error", lambda **kw: marks.append(kw)
    )

    assert coordinator.process_due_request_starts() == 1
    assert marks[0]["request_id"] == bad.id
    assert marks[0]["message"] == "weird"
    assert session.rollbacks == 1


def test_due_starts_stale_vmid_still_exists_after_retry(monkeypatch) -> None:
    req = _request(vmid=12)
    _patch_due_starts(monkeypatch, [req])

    def _ensure(**_kw):
        raise NotFoundError("missing")

    monkeypatch.setattr(coordinator, "_ensure_request_running", _ensure)
    attempts: list[int] = []

    def _find(vmid):
        attempts.append(vmid)
        if len(attempts) == 1:
            raise NotFoundError("hiccup")
        return {"vmid": vmid, "node": "pve1"}

    monkeypatch.setattr(scheduling_support, "find_resource_strict", _find)
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no recovery")),
    )

    assert coordinator.process_due_request_starts() == 0
    assert attempts == [12, 12]


def _confirm_gone(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(coordinator.time, "sleep", sleeps.append)

    def _find(_vmid):
        raise NotFoundError("gone")

    monkeypatch.setattr(scheduling_support, "find_resource_strict", _find)
    return sleeps


@pytest.mark.parametrize(
    "fresh",
    [
        None,
        "failed",
        "rejected",
    ],
)
def test_due_starts_stale_vmid_skips_consumed_request(monkeypatch, fresh) -> None:
    req = _request(vmid=13)
    _patch_due_starts(monkeypatch, [req])
    monkeypatch.setattr(
        coordinator,
        "_ensure_request_running",
        lambda **kw: (_ for _ in ()).throw(NotFoundError("missing")),
    )
    sleeps = _confirm_gone(monkeypatch)
    fresh_obj = {
        None: None,
        "failed": _request(
            id=req.id, vmid=13, provisioning_status=VMProvisioningStatus.failed
        ),
        "rejected": _request(id=req.id, vmid=13, status=VMRequestStatus.rejected),
    }[fresh]
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: fresh_obj
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "clear_vm_request_provisioning",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no clear")),
    )

    assert coordinator.process_due_request_starts() == 0
    assert sleeps == [2, 2]


@pytest.mark.parametrize("second_started", [True, False])
def test_due_starts_recovers_stale_vmid(monkeypatch, second_started: bool) -> None:
    req = _request(vmid=14)
    session = _patch_due_starts(monkeypatch, [req])
    _confirm_gone(monkeypatch)
    fresh = _request(id=req.id, vmid=14)
    calls: list[Any] = []

    def _ensure(*, session, request):
        calls.append(request.vmid)
        if len(calls) == 1:
            raise NotFoundError("missing")
        return second_started

    monkeypatch.setattr(coordinator, "_ensure_request_running", _ensure)
    monkeypatch.setattr(
        coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kw: fresh
    )

    def _clear(*, session, db_request, commit):
        db_request.vmid = None

    monkeypatch.setattr(
        coordinator.vm_request_repo, "clear_vm_request_provisioning", _clear
    )

    assert coordinator.process_due_request_starts() == (1 if second_started else 0)
    assert calls == [14, None]
    assert fresh.status == VMRequestStatus.approved
    assert session.commits == 2


def test_due_starts_recovery_failure_marks_error(monkeypatch) -> None:
    req = _request(vmid=15)
    session = _patch_due_starts(monkeypatch, [req])
    _confirm_gone(monkeypatch)
    calls: list[int] = []

    def _ensure(**_kw):
        calls.append(1)
        if len(calls) == 1:
            raise NotFoundError("missing")
        raise RuntimeError("reclone failed")

    monkeypatch.setattr(coordinator, "_ensure_request_running", _ensure)
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: _request(id=req.id, vmid=15),
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "clear_vm_request_provisioning",
        lambda **kw: None,
    )
    marks: list[dict] = []
    monkeypatch.setattr(
        coordinator, "_mark_request_runtime_error", lambda **kw: marks.append(kw)
    )

    assert coordinator.process_due_request_starts() == 0
    assert marks[0]["message"] == "reclone failed"
    assert session.rollbacks == 2


# ---------------------------------------------------------------------------
# process_due_request_stops
# ---------------------------------------------------------------------------


def _patch_due_stops(monkeypatch, rows: list[VMRequest]) -> _FakeSession:
    session = _FakeSession(rows)
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(coordinator, "_utc_now", lambda: NOW)
    return session


@pytest.mark.parametrize(
    ("db_resource", "expect_set"),
    [
        (None, False),
        (SimpleNamespace(auto_stop_at=None), True),
        (SimpleNamespace(auto_stop_at=NOW + timedelta(days=1)), True),
        (SimpleNamespace(auto_stop_at=NOW - timedelta(days=1)), False),
    ],
)
def test_due_stops_shuts_down_running_machine(
    monkeypatch, noop_audit, db_resource, expect_set: bool
) -> None:
    req = _request(vmid=30, end_at=NOW - timedelta(minutes=5))
    session = _patch_due_stops(monkeypatch, [req])
    monkeypatch.setattr(
        scheduling_support,
        "find_resource_strict",
        lambda vmid: {"node": "pve1", "status": "running"},
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": "running"},
    )
    monkeypatch.setattr(
        coordinator.resource_repo, "get_resource_by_vmid", lambda **kw: db_resource
    )
    auto_stops: list[dict] = []
    monkeypatch.setattr(
        coordinator.resource_repo, "set_auto_stop", lambda **kw: auto_stops.append(kw)
    )
    controls: list[tuple] = []
    monkeypatch.setattr(
        coordinator.proxmox_service, "control", lambda *a: controls.append(a)
    )

    assert coordinator.process_due_request_stops() == 1
    assert controls == [("pve1", 30, "qemu", "shutdown")]
    assert bool(auto_stops) is expect_set
    if expect_set:
        assert auto_stops[0]["auto_stop_reason"] == "window_grace"
    assert noop_audit[0]["action"] == "resource_shutdown"
    assert session.commits == 1


def test_due_stops_skips_without_vmid_and_already_stopped(monkeypatch) -> None:
    no_vmid = _request(vmid=None, end_at=NOW - timedelta(hours=1))
    listed_stopped = _request(vmid=20, end_at=NOW - timedelta(hours=1))
    live_stopped = _request(vmid=21, end_at=NOW - timedelta(hours=1))
    session = _FakeSession([no_vmid, listed_stopped, live_stopped])
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(coordinator, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        scheduling_support,
        "find_resource_strict",
        lambda vmid: {
            "node": "pve1",
            "status": "stopped" if vmid == 20 else "running",
        },
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": "paused"},
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "control",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no shutdown")),
    )

    assert coordinator.process_due_request_stops() == 0
    assert session.commits == 0


# ---------------------------------------------------------------------------
# run_scheduler 與各 tick 包裝
# ---------------------------------------------------------------------------


def test_run_scheduler_registers_all_tasks(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_runner(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(coordinator, "run_polling_scheduler", _fake_runner)
    asyncio.run(coordinator.run_scheduler(asyncio.Event()))

    from app.services.scheduling.leader import scheduler_leader_lock

    names = [task.name for task in captured["tasks"]]
    assert captured["interval_seconds"] == coordinator.SCHEDULER_POLL_SECONDS
    assert captured["leader_gate"] is scheduler_leader_lock
    assert captured["observer"].loop == "scheduler"
    assert "process_due_request_starts" in names
    assert "process_system_health_alerts" in names
    assert len(names) == 19


@pytest.mark.parametrize(
    ("task", "module", "attr"),
    [
        (
            "reap_stale_batch_jobs_task",
            "app.services.vm.batch_provision_service",
            "reap_stale_batch_jobs",
        ),
        (
            "requeue_stalled_batch_jobs_task",
            "app.services.vm.batch_provision_service",
            "requeue_stalled_approved_jobs",
        ),
        (
            "process_expired_requests_task",
            "app.services.vm.vm_request_expiry_service",
            "process_expired_requests",
        ),
        (
            "process_expired_unprovisioned_requests_task",
            "app.services.vm.vm_request_expiry_service",
            "process_expired_unprovisioned_requests",
        ),
        (
            "process_quick_practice_lifecycle_task",
            "app.services.quick_practice",
            "process_lifecycle",
        ),
        (
            "process_resource_alerts_task",
            "app.services.monitoring.alert_service",
            "process_resource_alerts",
        ),
        (
            "process_system_health_alerts_task",
            "app.services.monitoring.system_health_service",
            "process_system_health_alerts",
        ),
        (
            "process_ttl_lifecycle_task",
            "app.services.governance.lifecycle_service",
            "process_ttl_lifecycle",
        ),
        (
            "process_idle_detection_task",
            "app.services.governance.lifecycle_service",
            "process_idle_detection",
        ),
        (
            "process_mining_detection_task",
            "app.services.security.mining_service",
            "process_mining_detection",
        ),
        (
            "process_snapshot_cleanup_task",
            "app.services.governance.snapshot_cleanup_service",
            "process_snapshot_cleanup",
        ),
    ],
)
def test_tick_wrappers_delegate(monkeypatch, task: str, module: str, attr: str) -> None:
    import importlib

    target = importlib.import_module(module)
    monkeypatch.setattr(target, attr, lambda: 42)
    assert getattr(coordinator, task)() == 42


def test_reap_stale_task_records_task(monkeypatch) -> None:
    from app.repositories import task_record as task_record_repo

    session = _FakeSession()
    monkeypatch.setattr(coordinator, "Session", lambda engine: session)
    monkeypatch.setattr(
        task_record_repo, "reap_stale_task_records", lambda *, session: 3
    )
    assert coordinator.reap_stale_task_records_task() == 3

    def _boom(*, session):
        raise RuntimeError("db down")

    monkeypatch.setattr(task_record_repo, "reap_stale_task_records", _boom)
    assert coordinator.reap_stale_task_records_task() == 0


def test_reap_stale_script_runs_task(monkeypatch) -> None:
    from app.ai.teacher_judge import script_executor_service

    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())
    monkeypatch.setattr(script_executor_service, "reap_stale_script_runs", lambda s: 2)
    assert coordinator.reap_stale_script_runs_task() == 2

    def _boom(_s):
        raise RuntimeError("db down")

    monkeypatch.setattr(script_executor_service, "reap_stale_script_runs", _boom)
    assert coordinator.reap_stale_script_runs_task() == 0


def test_process_pending_deletions_task(monkeypatch) -> None:
    from app.services.resource import deletion_service

    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())
    processed: list = []
    monkeypatch.setattr(deletion_service, "process_pending_deletions", processed.append)
    assert coordinator.process_pending_deletions_task() == 0
    assert len(processed) == 1

    def _boom(_s):
        raise RuntimeError("db down")

    monkeypatch.setattr(deletion_service, "process_pending_deletions", _boom)
    assert coordinator.process_pending_deletions_task() == 0
