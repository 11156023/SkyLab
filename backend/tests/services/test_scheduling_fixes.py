"""排程器回歸測試。

- 鎖定重讀後發現申請單已被刪機流程標成 failed，不得寫回 completed／開機
- 全新的申請單不去認領同名既有機器；範本不能被認領
- 有 PVE 連線列不出資源時，找不到機器不能當成「機器被刪」
- 自動關機要等機器真的停了才清排程，guest 不理 shutdown 就強制斷電
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import NotFoundError, ProxmoxError
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.scheduling import coordinator, recurrence_scheduler
from app.services.scheduling import support as scheduling_support

DELETED_MARKER = "Resource deleted by user"


@pytest.fixture()
def engine() -> Engine:
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    return eng


def _vm_request(**overrides: Any) -> VMRequest:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "reason": "lab",
        "resource_type": "vm",
        "hostname": "h",
        "status": VMRequestStatus.approved,
        "vmid": 480,
        "provisioning_status": VMProvisioningStatus.completed,
        "actual_node": "pve1",
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return VMRequest(**values)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def test_consumed_request_is_not_rewritten_to_completed_nor_started(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        scheduling_support,
        "find_resource_strict",
        lambda vmid: {"vmid": vmid, "name": "h", "node": "pve1"},
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "get_status",
        lambda *a: calls.append("get_status") or {"status": "stopped"},
    )
    monkeypatch.setattr(
        coordinator.proxmox_service, "control", lambda *a, **k: calls.append("start")
    )

    with Session(engine) as tick_session:
        tick_session.add(_vm_request())
        tick_session.commit()
        # 本 tick 撈到的是 completed 的舊資料
        stale = tick_session.exec(select(VMRequest)).one()
        request_id = stale.id

        # 使用者刪機流程在另一個 session 把申請單標成已消耗並 commit
        with Session(engine) as delete_session:
            row = delete_session.get(VMRequest, request_id)
            assert row is not None
            row.provisioning_status = VMProvisioningStatus.failed
            row.provisioning_error = DELETED_MARKER
            delete_session.add(row)
            delete_session.commit()

        started = coordinator._ensure_request_running(
            session=tick_session, request=stale
        )
        tick_session.commit()

    assert started is False
    assert calls == []
    with Session(engine) as check:
        row = check.get(VMRequest, request_id)
        assert row is not None
        assert row.provisioning_status == VMProvisioningStatus.failed
        assert row.provisioning_error == DELETED_MARKER


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def _patch_adopt_flow(
    monkeypatch: pytest.MonkeyPatch, locked: SimpleNamespace
) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: locked,
    )
    monkeypatch.setattr(
        coordinator,
        "_adopt_existing_resource",
        lambda **kw: calls.append("find_existing") or None,
    )
    monkeypatch.setattr(
        coordinator,
        "_provision_new_resource",
        lambda **kw: calls.append("provision"),
    )
    return calls


def test_fresh_request_is_provisioned_without_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = SimpleNamespace(
        id=uuid.uuid4(),
        vmid=None,
        provisioning_status=VMProvisioningStatus.idle,
        provisioning_started_at=None,
    )
    calls = _patch_adopt_flow(monkeypatch, locked)

    coordinator._adopt_or_provision_due_request(
        session=SimpleNamespace(commit=lambda: None), request=locked
    )

    assert calls == ["provision"]


def test_interrupted_provisioning_still_tries_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = SimpleNamespace(
        id=uuid.uuid4(),
        vmid=None,
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=datetime.now(UTC) - timedelta(hours=3),
    )
    calls = _patch_adopt_flow(monkeypatch, locked)

    coordinator._adopt_or_provision_due_request(
        session=SimpleNamespace(commit=lambda: None), request=locked
    )

    assert calls == ["find_existing", "provision"]


class _ClaimedSession:
    def exec(self, statement: Any) -> Any:
        return SimpleNamespace(all=lambda: [])


def _adoption_candidates(
    monkeypatch: pytest.MonkeyPatch,
    resources: list[dict[str, Any]],
    template_vmids: set[int],
) -> dict | None:
    monkeypatch.setattr(
        scheduling_support.proxmox_service, "list_all_resources", lambda: resources
    )
    monkeypatch.setattr(
        scheduling_support.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: None,
    )
    monkeypatch.setattr(
        scheduling_support, "_template_vmids", lambda session: template_vmids
    )
    request = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), hostname="web-server", resource_type="vm"
    )
    return scheduling_support.find_existing_resource_for_request(
        session=_ClaimedSession(), request=request
    )


def test_pve_template_with_same_name_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = _adoption_candidates(
        monkeypatch,
        [{"vmid": 900, "type": "qemu", "name": "web-server", "template": 1}],
        set(),
    )
    assert found is None


def test_registered_template_vmid_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = _adoption_candidates(
        monkeypatch,
        [{"vmid": 901, "type": "qemu", "name": "web-server", "template": 0}],
        {901},
    )
    assert found is None


def test_plain_unowned_machine_is_still_adoptable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    found = _adoption_candidates(
        monkeypatch,
        [{"vmid": 902, "type": "qemu", "name": "web-server", "template": 0}],
        set(),
    )
    assert found is not None
    assert found["vmid"] == 902


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def _patch_connections(
    monkeypatch: pytest.MonkeyPatch,
    keys: list[int],
    listed: list[tuple[int, list[dict[str, Any]]]],
) -> None:
    monkeypatch.setattr(
        scheduling_support.proxmox_service, "_connection_keys", lambda: keys
    )
    monkeypatch.setattr(
        scheduling_support.proxmox_service, "_raw_vms_by_connection", lambda: listed
    )
    # find_resource_strict 委派給 operations.find_resource(strict=True)，
    # pool 比對讀的是 operations 自己的 get_proxmox_settings
    monkeypatch.setattr(
        scheduling_support.proxmox_service,
        "get_proxmox_settings",
        lambda key: SimpleNamespace(pool_name=f"pool{key}"),
    )


def test_strict_lookup_raises_when_a_connection_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_connections(
        monkeypatch, [1, 2], [(1, [{"vmid": 100, "pool": "pool1", "node": "a"}])]
    )
    with pytest.raises(scheduling_support.ProxmoxConnectionUnavailableError):
        scheduling_support.find_resource_strict(480)


def test_strict_lookup_finds_vm_on_reachable_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_connections(
        monkeypatch,
        [1, 2],
        [
            (1, [{"vmid": 480, "pool": "pool1", "node": "a"}]),
            (2, [{"vmid": 481, "pool": "other", "node": "b"}]),
        ],
    )
    assert scheduling_support.find_resource_strict(480)["node"] == "a"
    # 不在該連線自己 pool 裡的機器不算
    with pytest.raises(NotFoundError):
        scheduling_support.find_resource_strict(481)


def test_strict_lookup_all_connections_down_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def all_down() -> list:
        raise ProxmoxError("All Proxmox connections are unavailable.")

    monkeypatch.setattr(scheduling_support.proxmox_service, "_connection_keys", lambda: [1])
    monkeypatch.setattr(
        scheduling_support.proxmox_service, "_raw_vms_by_connection", all_down
    )
    with pytest.raises(scheduling_support.ProxmoxConnectionUnavailableError):
        scheduling_support.find_resource_strict(480)


class _ScopedSession:
    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.commits = 0

    def __enter__(self) -> _ScopedSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def exec(self, statement: Any) -> Any:
        return SimpleNamespace(all=lambda: self.rows)

    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


def _unavailable(vmid: int) -> dict:
    raise scheduling_support.ProxmoxConnectionUnavailableError(
        f"connection down while looking up {vmid}"
    )


def test_stops_do_not_mark_failed_during_connection_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _vm_request(end_at=datetime.now(UTC) - timedelta(hours=1))
    fake = _ScopedSession([req])
    monkeypatch.setattr(coordinator, "Session", lambda engine: fake)
    monkeypatch.setattr(scheduling_support, "find_resource_strict", _unavailable)

    assert coordinator.process_due_request_stops() == 0

    assert req.provisioning_status == VMProvisioningStatus.completed
    assert req.vmid == 480


def _patch_starts(
    monkeypatch: pytest.MonkeyPatch, req: VMRequest
) -> dict[str, list]:
    seen: dict[str, list] = {"cleared": [], "marked": []}
    fake = _ScopedSession([])
    monkeypatch.setattr(coordinator, "Session", lambda engine: fake)
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "list_active_approved_vm_requests",
        lambda **kw: [req],
    )
    monkeypatch.setattr(
        coordinator.governance_repo,
        "get_governance_config",
        lambda **kw: SimpleNamespace(provision_max_concurrency=1),
    )
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "clear_vm_request_provisioning",
        lambda **kw: seen["cleared"].append(kw),
    )
    monkeypatch.setattr(
        coordinator,
        "_mark_request_runtime_error",
        lambda **kw: seen["marked"].append(kw["message"]),
    )
    monkeypatch.setattr(coordinator.time, "sleep", lambda s: None)
    return seen


def test_starts_skip_request_when_connection_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _vm_request()
    seen = _patch_starts(monkeypatch, req)

    def raise_unavailable(**kw: Any) -> bool:
        raise scheduling_support.ProxmoxConnectionUnavailableError("down")

    monkeypatch.setattr(coordinator, "_ensure_request_running", raise_unavailable)

    assert coordinator.process_due_request_starts() == 0
    assert seen == {"cleared": [], "marked": []}


def test_stale_vmid_is_not_recovered_when_confirmation_hits_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = _vm_request()
    seen = _patch_starts(monkeypatch, req)

    def raise_not_found(**kw: Any) -> bool:
        raise NotFoundError("Resource 480 not found")

    monkeypatch.setattr(coordinator, "_ensure_request_running", raise_not_found)
    monkeypatch.setattr(scheduling_support, "find_resource_strict", _unavailable)

    assert coordinator.process_due_request_starts() == 0
    assert seen == {"cleared": [], "marked": []}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


@pytest.fixture()
def stop_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    env: dict[str, Any] = {
        "status": "running",
        "actions": [],
        "cleared": [],
        "clock": 1000.0,
    }
    monkeypatch.setattr(recurrence_scheduler, "_shutdown_requested_at", {})
    monkeypatch.setattr(
        recurrence_scheduler,
        "_resource_info",
        lambda *, vmid: {"node": "pve1", "type": "qemu", "status": env["status"]},
    )
    monkeypatch.setattr(
        recurrence_scheduler.proxmox_service,
        "control",
        lambda node, vmid, rtype, action: env["actions"].append(action),
    )
    monkeypatch.setattr(
        recurrence_scheduler, "Session", lambda engine: nullcontext(None)
    )
    monkeypatch.setattr(
        recurrence_scheduler.resource_repo,
        "set_auto_stop",
        lambda **kw: env["cleared"].append(kw["vmid"]),
    )
    monkeypatch.setattr(
        recurrence_scheduler.time, "monotonic", lambda: env["clock"]
    )
    return env


def _due_resource() -> SimpleNamespace:
    return SimpleNamespace(
        vmid=321,
        auto_stop_at=datetime.now(UTC) - timedelta(minutes=1),
        auto_stop_reason="practice_quota",
    )


def test_auto_stop_keeps_schedule_until_vm_is_stopped(stop_env: dict) -> None:
    resource = _due_resource()

    recurrence_scheduler._stop_one(resource=resource)
    assert stop_env["actions"] == ["shutdown"]
    assert stop_env["cleared"] == []

    # 寬限期內：不重送、不清排程
    stop_env["clock"] += 60
    recurrence_scheduler._stop_one(resource=resource)
    assert stop_env["actions"] == ["shutdown"]
    assert stop_env["cleared"] == []

    # guest 一直不理 shutdown：超過寬限期強制斷電
    stop_env["clock"] += recurrence_scheduler.FORCE_STOP_AFTER_SECONDS
    recurrence_scheduler._stop_one(resource=resource)
    assert stop_env["actions"] == ["shutdown", "stop"]
    assert stop_env["cleared"] == []

    # 確認停了才清排程
    stop_env["status"] = "stopped"
    recurrence_scheduler._stop_one(resource=resource)
    assert stop_env["cleared"] == [321]
    assert recurrence_scheduler._shutdown_requested_at == {}


def test_auto_stop_forgets_rescheduled_vm(
    stop_env: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource = _due_resource()
    recurrence_scheduler._stop_one(resource=resource)
    assert recurrence_scheduler._shutdown_requested_at

    # 排程被延後（不再到期）：追蹤紀錄要丟掉，之後重新到期時從 shutdown 開始
    monkeypatch.setattr(
        recurrence_scheduler.resource_repo,
        "list_due_auto_stops",
        lambda **kw: [],
    )
    recurrence_scheduler.process_auto_stops()
    assert recurrence_scheduler._shutdown_requested_at == {}


def test_resource_info_keeps_schedule_on_connection_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduling_support, "find_resource_strict", _unavailable)
    with pytest.raises(scheduling_support.ProxmoxConnectionUnavailableError):
        recurrence_scheduler._resource_info(vmid=321)

    def gone(vmid: int) -> dict:
        raise NotFoundError("gone")

    monkeypatch.setattr(scheduling_support, "find_resource_strict", gone)
    assert recurrence_scheduler._resource_info(vmid=321) is None


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeBatchSession:
    """第一次 exec 回 job 清單，之後回該 job 的 task 清單。"""

    def __init__(self, jobs: list[Any], tasks: list[Any]) -> None:
        self._results = [jobs, tasks]

    def exec(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._results.pop(0))

    def get(self, _model: Any, _key: Any) -> Any:
        return SimpleNamespace()


def test_batch_boot_skips_only_vm_on_unreachable_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        teaching_class_id=uuid.uuid4(),
        next_window_end=now + timedelta(hours=1),
    )
    tasks = [SimpleNamespace(vmid=701), SimpleNamespace(vmid=702)]
    session = _FakeBatchSession([job], tasks)

    monkeypatch.setattr(
        recurrence_scheduler, "_class_schedule_enabled", lambda *_a: True
    )
    monkeypatch.setattr(
        recurrence_scheduler.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: SimpleNamespace(
            teaching_class_id=job.teaching_class_id, auto_stop_at=None
        ),
    )

    def fake_info(*, vmid: int) -> dict | None:
        if vmid == 701:
            raise scheduling_support.ProxmoxConnectionUnavailableError("down")
        return {"status": "stopped", "node": "pve2", "type": "qemu"}

    monkeypatch.setattr(recurrence_scheduler, "_resource_info", fake_info)

    specs = recurrence_scheduler._batch_boot_specs(
        session=session,  # type: ignore[arg-type]
        now=now,
    )

    assert [spec.vmid for spec in specs] == [702]
    assert specs[0].node == "pve2"
    assert specs[0].resource_type == "qemu"
    assert specs[0].window_end == job.next_window_end
