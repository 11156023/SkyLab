"""Coverage tests for app.services.proxmox.provisioning_service (provisioning flows)."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.exceptions import ProxmoxError
from app.schemas import LXCCreateRequest
from app.services.proxmox import provisioning_service as svc
from app.services.template import clone_service

_NET_CFG = {
    "bridge_name": "vmbr0",
    "prefix_len": 24,
    "gateway": "10.0.0.1",
    "dns_servers": "1.1.1.1",
}


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal Session stand-in: exec() returns queued values in order."""

    def __init__(self, exec_values: list[Any] | None = None) -> None:
        self.exec_values = list(exec_values or [])
        self.commits = 0
        self.rollbacks = 0

    def exec(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self.exec_values.pop(0) if self.exec_values else None)

    def get_bind(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture
def pm(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace proxmox_service in the module namespace with a MagicMock."""
    mock = MagicMock()
    mock.get_status.return_value = {"status": "stopped"}
    mock.vmid_allocation_lock.side_effect = lambda **_: nullcontext()
    mock.next_vmid.return_value = 500
    mock.find_vm_template.return_value = {"node": "pve1", "vmid": 9000}
    mock.get_config.return_value = {}
    monkeypatch.setattr(svc, "proxmox_service", mock)
    return mock


@pytest.fixture
def fw(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    mock.get_vm_firewall_rules.return_value = []
    monkeypatch.setattr(svc, "firewall_service", mock)
    return mock


@pytest.fixture
def stack(
    monkeypatch: pytest.MonkeyPatch, pm: MagicMock, fw: MagicMock
) -> SimpleNamespace:
    """Stub every collaborator used by create_lxc / create_vm / execute_provision."""
    ipm = MagicMock()
    ipm.get_network_config_for_vm.return_value = dict(_NET_CFG)
    ipm.allocate_ip.return_value = "10.0.0.50"
    monkeypatch.setattr(svc, "ip_management_service", ipm)

    repo = MagicMock()
    repo.get_allocated_ip_address.return_value = None
    repo.get_resource_by_vmid.return_value = None
    repo.create_resource.side_effect = lambda **kw: SimpleNamespace(guest_os=None, **kw)
    monkeypatch.setattr(svc, "resource_repo", repo)

    audit = MagicMock()
    monkeypatch.setattr(svc, "audit_service", audit)
    guest_login = MagicMock()
    monkeypatch.setattr(svc, "guest_ssh_login", guest_login)

    monkeypatch.setattr(
        svc,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(pool_name="skylab"),
    )
    monkeypatch.setattr(svc, "get_connection_id_for_node", lambda node: 1)
    monkeypatch.setattr(
        svc, "generate_ed25519_keypair", lambda: ("PRIVATE", "ssh-ed25519 AAAA")
    )
    monkeypatch.setattr(svc, "_resolve_managed_storage", lambda **_: "local-lvm")
    monkeypatch.setattr(
        svc,
        "initial_guest_os_identity",
        lambda **_: {"family": "linux", "id": "debian"},
    )
    return SimpleNamespace(pm=pm, fw=fw, ipm=ipm, repo=repo, audit=audit)


def _gpu_detail(
    *, available: int = 1, profiles: list[Any] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        available_count=available,
        used_count=1,
        capacity_count=1,
        profiles=profiles or [],
        maps=[SimpleNamespace(node="pve1")],
    )


def _patch_gpu(monkeypatch: pytest.MonkeyPatch, getter) -> None:
    monkeypatch.setattr(
        svc,
        "gpu_service",
        SimpleNamespace(is_valid_mapping_id=lambda _m: True, get_gpu_mapping=getter),
    )


# ── should_start_now ───────────────────────────────────────


def test_should_start_now_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(svc, "_utc_now", lambda: now)

    assert svc.should_start_now(SimpleNamespace(start_at=None)) is True
    naive_past = (now - timedelta(hours=1)).replace(tzinfo=None)
    naive_future = (now + timedelta(hours=1)).replace(tzinfo=None)
    # Naive timestamps are treated as UTC; an ended window never starts.
    assert (
        svc.should_start_now(
            SimpleNamespace(start_at=naive_past - timedelta(hours=2), end_at=naive_past)
        )
        is False
    )
    assert (
        svc.should_start_now(SimpleNamespace(start_at=naive_past, end_at=naive_future))
        is True
    )
    assert (
        svc.should_start_now(
            SimpleNamespace(start_at=now + timedelta(minutes=5), end_at=None)
        )
        is False
    )


def test_utc_now_is_timezone_aware() -> None:
    assert svc._utc_now().tzinfo is not None


# ── _ensure_resource_stopped ───────────────────────────────


def _fake_clock(monkeypatch: pytest.MonkeyPatch, step: float) -> None:
    ticks = {"now": 0.0}

    def _monotonic() -> float:
        ticks["now"] += step
        return ticks["now"]

    monkeypatch.setattr(
        svc, "time", SimpleNamespace(monotonic=_monotonic, sleep=lambda _s: None)
    )


def test_ensure_resource_stopped_waits_until_stopped(
    monkeypatch: pytest.MonkeyPatch, pm: MagicMock
) -> None:
    _fake_clock(monkeypatch, 1.0)
    pm.get_status.side_effect = [
        {"status": "running"},
        RuntimeError("transient"),
        {"status": "running"},
        {"status": "stopped"},
    ]
    svc._ensure_resource_stopped("pve1", 100, "qemu")
    pm.control.assert_called_once_with("pve1", 100, "qemu", "stop")


def test_ensure_resource_stopped_times_out(
    monkeypatch: pytest.MonkeyPatch, pm: MagicMock
) -> None:
    _fake_clock(monkeypatch, 10.0)
    pm.get_status.return_value = {"status": "running"}
    with pytest.raises(ProxmoxError):
        svc._ensure_resource_stopped("pve1", 100, "qemu", timeout_seconds=25)


# ── cleanup helpers ────────────────────────────────────────


def test_cleanup_failed_resource_qemu_survives_stop_error(
    monkeypatch: pytest.MonkeyPatch, pm: MagicMock
) -> None:
    def _stop_fails(*_a: Any, **_k: Any) -> None:
        raise ProxmoxError("still running")

    monkeypatch.setattr(svc, "_ensure_resource_stopped", _stop_fails)
    svc.cleanup_failed_resource("pve1", 101, "qemu")
    pm.delete_resource.assert_called_once_with(
        "pve1", 101, "qemu", purge=1, **{"destroy-unreferenced-disks": 1}
    )


def test_cleanup_failed_resource_swallows_delete_error(pm: MagicMock) -> None:
    pm.delete_resource.side_effect = RuntimeError("locked")
    svc.cleanup_failed_resource("pve1", 102, "lxc")
    pm.delete_resource.assert_called_once_with("pve1", 102, "lxc", purge=1)


def test_cleanup_provisioned_resource_looks_up_node(
    monkeypatch: pytest.MonkeyPatch, pm: MagicMock
) -> None:
    pm.find_resource.return_value = {"node": "pve3", "type": "lxc"}
    calls: list[tuple] = []
    monkeypatch.setattr(svc, "cleanup_failed_resource", lambda *a: calls.append(a))
    svc.cleanup_provisioned_resource(103)
    assert calls == [("pve3", 103, "lxc")]


def test_rollback_created_resource_ignores_rule_delete_errors(
    monkeypatch: pytest.MonkeyPatch, fw: MagicMock
) -> None:
    # A rule without "pos" sorts as 0 and is skipped.
    fw.get_vm_firewall_rules.return_value = [{"pos": 0}, {}, {"pos": 2}]
    fw.delete_rule_by_pos.side_effect = RuntimeError("rule gone")
    cleaned: list[tuple] = []
    monkeypatch.setattr(svc, "cleanup_failed_resource", lambda *a: cleaned.append(a))
    svc._rollback_created_resource("pve1", 104, "qemu")
    assert [c.args[3] for c in fw.delete_rule_by_pos.call_args_list] == [2, 0]
    assert cleaned == [("pve1", 104, "qemu")]


def test_release_ip_after_failure_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ipm = MagicMock()
    ipm.release_ip.side_effect = RuntimeError("db gone")
    monkeypatch.setattr(svc, "ip_management_service", ipm)

    class _Session:
        def __init__(self, _bind: Any) -> None:
            pass

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(svc, "Session", _Session)
    svc._release_ip_after_failure(None, 105, "key", label="VM")
    ipm.release_ip.assert_called_once()


# ── GPU hostpci ────────────────────────────────────────────


def test_build_gpu_hostpci_rejects_exhausted_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_gpu(monkeypatch, lambda _m: _gpu_detail(available=0))
    with pytest.raises(ProxmoxError):
        svc._build_gpu_hostpci("gpu0", None)


def test_build_gpu_hostpci_wraps_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_m: str) -> Any:
        raise RuntimeError("PVE unreachable")

    _patch_gpu(monkeypatch, _boom)
    with pytest.raises(ProxmoxError):
        svc._build_gpu_hostpci("gpu0", None)


# ── _select_request_placement ──────────────────────────────


def _placement(node: str | None, feasible: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        node=node, strategy="balanced", plan=SimpleNamespace(feasible=feasible)
    )


@pytest.fixture
def placement_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    env = SimpleNamespace(
        gpu_nodes=["pve1"],
        template_nodes=None,
        pinned_plan_feasible=True,
        fallback=_placement(None, feasible=False),
        selection=_placement("pve1"),
        overlapping=[],
    )
    _patch_gpu(
        monkeypatch,
        lambda _m: SimpleNamespace(
            maps=[SimpleNamespace(node=n) for n in env.gpu_nodes]
        ),
    )
    monkeypatch.setattr(
        svc,
        "placement_support",
        SimpleNamespace(
            allowed_template_nodes_for_request=lambda _r: env.template_nodes,
            load_cluster_state=lambda: ([], []),
            build_live_node_capacities=lambda **_: [
                SimpleNamespace(node="pve1"),
                SimpleNamespace(node="pve2"),
            ],
        ),
    )
    monkeypatch.setattr(
        svc,
        "placement_advisor",
        SimpleNamespace(decide_resource_type=lambda _r: ("vm", "rule")),
    )
    monkeypatch.setattr(
        svc,
        "vm_request_placement_service",
        SimpleNamespace(
            get_overcommit_ratios=lambda _s: (1.0, 1.0),
            get_memory_overcommit_ratio=lambda _s: 1.0,
            get_node_priorities=lambda _s: {},
            build_plan=lambda **_: SimpleNamespace(feasible=env.pinned_plan_feasible),
            CurrentPlacementSelection=SimpleNamespace,
            select_reserved_target_node=lambda **_: env.fallback,
            select_current_target_node=lambda **_: env.selection,
        ),
    )
    monkeypatch.setattr(
        svc,
        "vm_request_repo",
        SimpleNamespace(
            get_approved_vm_requests_overlapping_window=lambda **_: env.overlapping
        ),
    )
    return env


def _placement_request_row(**overrides: Any) -> SimpleNamespace:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        gpu_mapping_id="gpu0",
        desired_node=None,
        assigned_node=None,
        start_at=None,
        end_at=None,
        ostemplate=None,
        template_id=9000,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _select(row: SimpleNamespace) -> Any:
    return svc._select_request_placement(
        session=MagicMock(),
        db_request=row,
        placement_request=object(),
        placement_strategy="balanced",
    )


def test_pinned_node_incompatible_with_gpu(placement_env: SimpleNamespace) -> None:
    with pytest.raises(ProxmoxError, match="Pinned node"):
        _select(_placement_request_row(desired_node="pve2"))


def test_pinned_infeasible_fallback_not_gpu_compatible(
    placement_env: SimpleNamespace,
) -> None:
    placement_env.pinned_plan_feasible = False
    placement_env.fallback = _placement("pve3")
    row = _placement_request_row(
        desired_node="pve1",
        start_at=datetime(2026, 10, 1, tzinfo=UTC),
        end_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    placement_env.overlapping = [SimpleNamespace(id=row.id), SimpleNamespace(id=1)]
    with pytest.raises(ProxmoxError, match="GPU-compatible"):
        _select(row)


def test_pinned_infeasible_fallback_outside_template_whitelist(
    placement_env: SimpleNamespace,
) -> None:
    placement_env.gpu_nodes = []
    placement_env.template_nodes = {"pve1"}
    placement_env.pinned_plan_feasible = False
    placement_env.fallback = _placement("pve3")
    with pytest.raises(ProxmoxError):
        _select(_placement_request_row(desired_node="pve1", ostemplate="local:vz"))


def test_pinned_infeasible_without_fallback_returns_pinned(
    placement_env: SimpleNamespace,
) -> None:
    placement_env.pinned_plan_feasible = False
    result = _select(_placement_request_row(desired_node="pve1"))
    assert result.node == "pve1"
    assert result.plan.feasible is False


def test_auto_selection_incompatible_with_gpu(
    placement_env: SimpleNamespace,
) -> None:
    placement_env.selection = _placement("pve2")
    with pytest.raises(ProxmoxError, match="Selected node"):
        _select(_placement_request_row())


def test_auto_selection_outside_template_whitelist(
    placement_env: SimpleNamespace,
) -> None:
    placement_env.template_nodes = set()
    with pytest.raises(ProxmoxError):
        _select(_placement_request_row(template_id=None))


def test_auto_selection_returned(placement_env: SimpleNamespace) -> None:
    placement_env.gpu_nodes = []
    placement_env.template_nodes = {"pve1"}
    assert _select(_placement_request_row(gpu_mapping_id=None)) is (
        placement_env.selection
    )


# ── _resolve_managed_storage ───────────────────────────────


def test_resolve_managed_storage_without_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "vm_request_placement_service",
        SimpleNamespace(select_best_storage_name=lambda **_: None),
    )
    with pytest.raises(ProxmoxError, match="No enabled managed storage"):
        svc._resolve_managed_storage(
            session=MagicMock(),
            node="pve1",
            resource_type="vm",
            requested_storage=None,
            disk_gb=20,
            required_content="images",
        )


# ── _open_lxc_ssh_login ────────────────────────────────────


def test_open_lxc_ssh_login_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(
        clone_service, "inject_lxc_platform_key", lambda *a: calls.append(a)
    )
    svc._open_lxc_ssh_login("pve1", 200, "ssh-ed25519 KEY")
    assert calls == [("pve1", 200, "ssh-ed25519 KEY")]

    def _boom(*_a: Any) -> None:
        raise RuntimeError("pct exec failed")

    monkeypatch.setattr(clone_service, "inject_lxc_platform_key", _boom)
    svc._open_lxc_ssh_login("pve1", 200, "ssh-ed25519 KEY")


# ── create_lxc / create_vm ─────────────────────────────────


def test_create_lxc_with_dns_and_guest_os(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    opened: list[tuple] = []
    monkeypatch.setattr(svc, "_open_lxc_ssh_login", lambda *a: opened.append(a))
    stack.pm.create_lxc.return_value = "UPID:lxc"
    session = _FakeSession()
    data = LXCCreateRequest(
        hostname="ct-one",
        ostemplate="local:vztmpl/debian.tar.zst",
        password="Secret123",
        environment_type="personal",
    )

    response = svc.create_lxc(session=session, lxc_data=data, user_id=uuid.uuid4())

    assert response.vmid == 500
    assert stack.pm.create_lxc.call_args.kwargs["nameserver"] == "1.1.1.1"
    created = stack.repo.create_resource.call_args.kwargs
    assert created["vmid"] == 500
    assert opened == [(stack.pm.pick_target_node.return_value, 500, "ssh-ed25519 AAAA")]
    assert session.commits == 1


def test_create_vm_with_gpu_and_guest_os(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    _patch_gpu(monkeypatch, lambda _m: _gpu_detail())
    stack.pm.clone_vm.return_value = "UPID:clone"
    vm_data = SimpleNamespace(
        hostname="vm-one",
        template_id=9000,
        username="student",
        password="Str0ng!Pass",
        cores=2,
        memory=2048,
        disk_size=None,
        storage="local-lvm",
        environment_type="personal",
        os_info=None,
        expiry_date=None,
        start=True,
        gpu_mapping_id="gpu0",
        gpu_mdev_profile=None,
    )
    session = _FakeSession()

    response = svc.create_vm(session=session, vm_data=vm_data, user_id=uuid.uuid4())

    assert response.vmid == 500
    updates = stack.pm.update_config.call_args.kwargs
    assert updates["hostpci0"] == "mapping=gpu0"
    assert updates["ciuser"] == "student"
    assert updates["nameserver"] == "1.1.1.1"
    assert session.commits == 1


def test_create_vm_rolls_back_after_clone_failure(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    rolled: list[tuple] = []
    released: list[tuple] = []
    monkeypatch.setattr(svc, "_rollback_created_resource", lambda *a: rolled.append(a))
    monkeypatch.setattr(
        svc,
        "_release_ip_after_failure",
        lambda bind, vmid, key, *, label: released.append((vmid, label)),
    )
    stack.pm.update_config.side_effect = RuntimeError("config locked")
    vm_data = SimpleNamespace(
        hostname="vm-two",
        template_id=9000,
        username=None,
        password="Str0ng!Pass",
        cores=2,
        memory=2048,
        disk_size=None,
        storage="local-lvm",
        environment_type="personal",
        os_info=None,
        expiry_date=None,
        start=False,
    )
    session = _FakeSession()

    with pytest.raises(ProxmoxError, match="Failed to create VM"):
        svc.create_vm(session=session, vm_data=vm_data, user_id=uuid.uuid4())

    assert rolled == [("pve1", 500, "qemu")]
    assert released == [(500, "VM")]
    assert session.rollbacks == 1


# ── plan_provision ─────────────────────────────────────────


def _db_request(**overrides: Any) -> SimpleNamespace:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        resource_type="vm",
        username="student",
        placement_strategy_used=None,
        hostname="host-a",
        cores=2,
        memory=2048,
        password=None,
        password_hash=None,
        request_kind="",
        start_at=None,
        end_at=None,
        user_id=uuid.uuid4(),
        environment_type="personal",
        os_info=None,
        expiry_date=None,
        storage="local-lvm",
        template_id=9000,
        rootfs_size=None,
        ostemplate=None,
        unprivileged=True,
        disk_size=30,
        gpu_mapping_id=None,
        gpu_mdev_profile=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


@pytest.fixture
def plan_env(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> SimpleNamespace:
    env = SimpleNamespace(placement=_placement("pve2"), storages=[])
    monkeypatch.setattr(svc, "allocate_free_vmid", lambda _s: 700)
    monkeypatch.setattr(
        svc,
        "vm_request_placement_service",
        SimpleNamespace(
            _to_placement_request=lambda _r: object(),
            DEFAULT_PLACEMENT_STRATEGY="priority_dominant_share",
        ),
    )
    monkeypatch.setattr(svc, "_select_request_placement", lambda **_: env.placement)

    def _storage(**kwargs: Any) -> str:
        env.storages.append(kwargs)
        return f"store-{kwargs['node']}"

    monkeypatch.setattr(svc, "_resolve_managed_storage", _storage)
    env.stack = stack
    return env


def test_plan_provision_rejects_infeasible_placement(
    plan_env: SimpleNamespace,
) -> None:
    plan_env.placement = _placement(None, feasible=False)
    with pytest.raises(ProxmoxError, match="No feasible placement"):
        svc.plan_provision(session=_FakeSession(), db_request=_db_request())


def test_plan_provision_vm_cross_node_with_gpu_and_quick_key(
    plan_env: SimpleNamespace,
) -> None:
    practice = SimpleNamespace(session_id="sess-1", node_key="web")
    session = _FakeSession([practice])
    row = _db_request(
        request_kind="quick_template",
        placement_strategy_used="balanced",
        gpu_mapping_id="gpu0",
        gpu_mdev_profile="nvidia-1",
    )

    plan = svc.plan_provision(session=session, db_request=row)

    assert plan["vmid"] == 700
    assert plan["target_node"] == "pve2"
    assert plan["resource_type"] == "qemu"
    assert plan["ip_reservation_key"] == "quick:sess-1:web"
    assert plan["gpu_mapping_id"] == "gpu0"
    assert plan["gpu_mdev_profile"] == "nvidia-1"
    assert plan["target_storage"] == "store-pve2"
    assert plan["fallback_storage"] == "store-pve1"
    assert plan["password_custom"] is False


def test_plan_provision_lxc_template_clone(plan_env: SimpleNamespace) -> None:
    template_row = SimpleNamespace(node="pve3", default_disk=12)
    row = _db_request(resource_type="lxc", template_id=8000, username="Bad User")
    plan = svc.plan_provision(session=_FakeSession([template_row]), db_request=row)
    assert plan["lxc_clone"] is True
    assert plan["target_node"] == "pve3"
    assert plan["template_node"] == "pve3"
    assert plan["target_storage"] == "store-pve3"
    assert plan["rootfs_size"] == 8
    assert plan["template_disk_gb"] == 12


def test_plan_provision_lxc_template_not_registered(
    plan_env: SimpleNamespace,
) -> None:
    row = _db_request(resource_type="lxc", template_id=8001)
    with pytest.raises(ProxmoxError, match="not registered"):
        svc.plan_provision(session=_FakeSession([None]), db_request=row)


def test_plan_provision_lxc_ostemplate_missing_on_node(
    plan_env: SimpleNamespace,
) -> None:
    plan_env.stack.pm.get_lxc_template_node_map.return_value = {
        "local:vztmpl/other.tar.zst": {"pve1"}
    }
    row = _db_request(
        resource_type="lxc", template_id=None, ostemplate="local:vztmpl/deb.tar.zst"
    )
    with pytest.raises(ProxmoxError):
        svc.plan_provision(session=_FakeSession(), db_request=row)


def test_plan_provision_lxc_ostemplate(plan_env: SimpleNamespace) -> None:
    plan_env.stack.pm.get_lxc_template_node_map.return_value = {
        "local:vztmpl/deb.tar.zst": {"pve2"}
    }
    row = _db_request(
        resource_type="lxc",
        template_id=None,
        ostemplate="local:vztmpl/deb.tar.zst",
        rootfs_size=16,
        unprivileged=False,
    )
    plan = svc.plan_provision(session=_FakeSession(), db_request=row)
    assert plan["ostemplate"] == "local:vztmpl/deb.tar.zst"
    assert plan["rootfs_size"] == 16
    assert plan["unprivileged"] is False
    assert plan["target_storage"] == "store-pve2"


# ── execute_provision ──────────────────────────────────────


def _lxc_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "vmid": 800,
        "target_node": "pve1",
        "resource_type": "lxc",
        "hostname": "ct-x",
        "cores": 1,
        "memory": 512,
        "start_immediately": True,
        "ssh_public_key": "ssh-ed25519 PLATFORM",
        "password": "Rand0mPass",
        "allocated_ip": "10.0.0.80",
        "net_cfg": dict(_NET_CFG),
        "ostemplate": "local:vztmpl/deb.tar.zst",
        "target_storage": "local-lvm",
        "rootfs_size": 8,
        "unprivileged": True,
    }
    plan.update(overrides)
    return plan


def _vm_plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "vmid": 900,
        "target_node": "pve1",
        "template_node": "pve1",
        "template_id": 9000,
        "resource_type": "qemu",
        "hostname": "vm-x",
        "cores": 2,
        "memory": 2048,
        "start_immediately": True,
        "ssh_public_key": "ssh-ed25519 PLATFORM",
        "password": None,
        "password_hash": None,
        "username": "student",
        "allocated_ip": "10.0.0.90",
        "net_cfg": dict(_NET_CFG),
        "target_storage": "local-lvm",
        "disk_size": 40,
    }
    plan.update(overrides)
    return plan


def test_execute_provision_lxc_network_incomplete(stack: SimpleNamespace) -> None:
    with pytest.raises(ProxmoxError):
        svc.execute_provision(_lxc_plan(allocated_ip=None))
    stack.pm.create_lxc.assert_not_called()


def test_execute_provision_lxc_ostemplate_with_dns(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    monkeypatch.setattr(svc, "_open_lxc_ssh_login", lambda *a: None)
    plan = _lxc_plan()
    assert svc.execute_provision(plan) == (800, "pve1")
    config = stack.pm.create_lxc.call_args.kwargs
    assert config["nameserver"] == "1.1.1.1"
    assert config["password"] == "Rand0mPass"
    assert plan["login_password_applied"] is True


def test_execute_provision_lxc_clone_with_dns(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    monkeypatch.setattr(clone_service, "clone_with_fallback", lambda **_: None)
    monkeypatch.setattr(clone_service, "set_lxc_root_password", lambda *a, **k: True)
    monkeypatch.setattr(clone_service, "inject_lxc_platform_key", lambda *a: None)
    plan = _lxc_plan(
        lxc_clone=True,
        template_node="pve2",
        template_id=8000,
        template_disk_gb=8,
    )
    assert svc.execute_provision(plan) == (800, "pve2")
    assert stack.pm.update_config.call_args.kwargs["nameserver"] == "1.1.1.1"
    assert plan["login_password_applied"] is True


def test_execute_provision_qemu_same_node_clone(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    clones: list[dict] = []
    monkeypatch.setattr(
        clone_service, "clone_with_fallback", lambda **kw: clones.append(kw)
    )
    _patch_gpu(monkeypatch, lambda _m: _gpu_detail())
    monkeypatch.setattr(svc, "template_disk_floor_gb", lambda _t: 20)
    monkeypatch.setattr(svc, "_clone_boot_disk_key", lambda _n, _v: "scsi0")
    plan = _vm_plan(password_hash="$6$salt$hash", gpu_mapping_id="gpu0")

    assert svc.execute_provision(plan) == (900, "pve1")

    assert clones[0]["node"] == "pve1"
    updates = stack.pm.update_config.call_args.kwargs
    assert updates["cipassword"] == "$6$salt$hash"
    assert updates["ciuser"] == "student"
    assert updates["hostpci0"] == "mapping=gpu0"
    assert updates["ipconfig0"] == "ip=10.0.0.90/24,gw=10.0.0.1"
    assert plan["login_password_applied"] is True
    stack.pm.resize_disk.assert_called_once_with("pve1", 900, "qemu", "scsi0", "40G")
    stack.pm.control.assert_called_once_with("pve1", 900, "qemu", "start")


def test_execute_provision_qemu_cross_node_clone(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    plan = _vm_plan(
        target_node="pve2",
        username=None,
        start_immediately=False,
        disk_size=None,
    )
    assert svc.execute_provision(plan) == (900, "pve2")
    assert stack.pm.clone_vm.call_args.kwargs["target"] == "pve2"
    updates = stack.pm.update_config.call_args.kwargs
    assert "cipassword" not in updates
    assert "ciuser" not in updates
    assert plan["login_password_applied"] is False


def test_execute_provision_qemu_cross_node_falls_back_to_template_node(
    stack: SimpleNamespace,
) -> None:
    stack.pm.clone_vm.side_effect = [RuntimeError("cross-node failed"), "UPID"]
    plan = _vm_plan(
        target_node="pve2",
        fallback_storage="fallback-store",
        start_immediately=False,
        disk_size=None,
    )
    assert svc.execute_provision(plan) == (900, "pve1")
    second = stack.pm.clone_vm.call_args_list[1]
    assert second.args == ("pve1", 9000)
    assert second.kwargs["storage"] == "fallback-store"
    assert "target" not in second.kwargs


def test_execute_provision_qemu_network_incomplete_rolls_back(
    monkeypatch: pytest.MonkeyPatch, stack: SimpleNamespace
) -> None:
    monkeypatch.setattr(clone_service, "clone_with_fallback", lambda **_: None)
    rolled: list[tuple] = []
    monkeypatch.setattr(svc, "_rollback_created_resource", lambda *a: rolled.append(a))
    with pytest.raises(ProxmoxError):
        svc.execute_provision(_vm_plan(net_cfg={}))
    assert rolled == [("pve1", 900, "qemu")]


# ── disk helpers / template helpers ────────────────────────


def test_clone_source_disk_gb_non_numeric_custom_image() -> None:
    node = SimpleNamespace(
        source_type="custom",
        source_template_id=None,
        resource_type="qemu",
        custom_image_ref="not-a-vmid",
        disk_gb=15,
    )
    assert svc.clone_source_disk_gb(MagicMock(), node) == 15


def test_resize_clone_disk_skips_when_template_is_larger(pm: MagicMock) -> None:
    pm.find_vm_template.return_value = {"node": "pve1", "maxdisk": 40 * 1024**3}
    svc._resize_clone_disk_if_needed("pve1", 900, 9000, 20)
    pm.resize_disk.assert_not_called()


def test_lxc_rootfs_gb_config_error_returns_zero(pm: MagicMock) -> None:
    pm.get_config.side_effect = RuntimeError("down")
    assert svc._lxc_rootfs_gb("pve1", 800) == 0


def test_template_ostype_reads_config(pm: MagicMock) -> None:
    pm.get_config.return_value = {"ostype": "win11"}
    assert svc._template_ostype({"node": "pve1", "vmid": "9000"}) == "win11"
    pm.get_config.return_value = {}
    assert svc._template_ostype({"node": "pve1", "vmid": 9000}) is None


def test_is_windows_template_lookup_failure(pm: MagicMock) -> None:
    pm.find_vm_template.side_effect = RuntimeError("missing")
    assert svc.is_windows_template(9000) is False
