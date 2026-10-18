"""app.infrastructure.proxmox.operations 的分支覆蓋：VMID 鎖、節點／儲存挑選、資源操作與 ticket。

全部以假的 proxmoxer client（MagicMock）與假的 DB session 進行，不連任何外部系統。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import sqlmodel

import app.core.db as core_db
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.infrastructure.proxmox import operations as ops
from app.infrastructure.proxmox.settings import ProxmoxSettings

# conftest 會在單元測試裡把這兩個要連 DB 的函式換掉；測它們本身時用模組載入
# 當下的原函式，並傳入隔離的 engine
_real_clone_source_lock = ops.clone_source_lock
_real_platform_template_vmids = ops.platform_template_vmids


def _settings(**overrides: Any) -> ProxmoxSettings:
    values: dict[str, Any] = {
        "host": "10.0.0.1",
        "user": "root@pam",
        "password": "pw",
        "verify_ssl": False,
        "iso_storage": "iso-store",
        "data_storage": "local-lvm",
        "api_timeout": 20,
        "task_check_interval": 1,
        "pool_name": "SkyLab",
        "port": 8006,
    }
    values.update(overrides)
    return ProxmoxSettings(**values)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """節點路由到同一個假的 proxmoxer client；任務等待直接成功。"""
    fake = MagicMock(name="proxmox")
    monkeypatch.setattr(ops, "get_proxmox_api_for_node", lambda _node: fake)
    return fake


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, dict]]:
    calls: list[tuple[str, Any, dict]] = []
    monkeypatch.setattr(
        ops,
        "basic_blocking_task_status",
        lambda node, task, **kw: calls.append((node, task, kw)),
    )
    return calls


@pytest.fixture
def invalidations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(
        ops, "invalidate_cluster_resources_cache", lambda: calls.append(1)
    )
    return calls


# ---------------------------------------------------------------------------
# vmid_allocation_lock
# ---------------------------------------------------------------------------


class _Conn:
    def __init__(self, rollback_error: Exception | None = None) -> None:
        self.executed: list[tuple[str, dict]] = []
        self.rollbacks = 0
        self.rollback_error = rollback_error
        self.dialect = SimpleNamespace(name="postgresql")

    def execute(self, statement: Any, params: dict) -> None:
        self.executed.append((str(statement), params))

    def rollback(self) -> None:
        self.rollbacks += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class _Engine:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn
        self.dialect = SimpleNamespace(name="postgresql")

    @contextmanager
    def connect(self) -> Any:
        yield self.conn


def test_lock_resolves_application_engine_and_skips_non_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_db, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    with ops.vmid_allocation_lock():
        pass


def test_lock_takes_xact_advisory_lock_and_rolls_back() -> None:
    conn = _Conn()
    with ops.vmid_allocation_lock(db_engine=_Engine(conn)):
        assert conn.rollbacks == 0
    assert "pg_advisory_xact_lock" in conn.executed[0][0]
    assert conn.executed[0][1] == {"lock_key": ops._VMID_ALLOCATION_LOCK_KEY}
    assert conn.rollbacks == 1


def test_lock_accepts_connection_without_connect_and_survives_rollback_error() -> None:
    conn = _Conn(rollback_error=RuntimeError("connection closed"))

    def fail_inside_lock() -> None:
        with ops.vmid_allocation_lock(db_engine=conn):
            raise ValueError("provisioning failed")

    with pytest.raises(ValueError, match="provisioning failed"):
        fail_inside_lock()

    assert conn.rollbacks == 1


# ---------------------------------------------------------------------------
# connection keys / cluster listing helpers
# ---------------------------------------------------------------------------


def test_connection_keys_use_enabled_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "list_enabled_connection_ids", lambda: [3, 1])
    assert ops._connection_keys() == [3, 1]


def test_remember_listed_vmids_skips_unparseable_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops._LastSeenVmids, "by_connection", {})

    ops._remember_listed_vmids(
        [(1, [{"vmid": 100}, {"vmid": "101"}, {"vmid": "x"}, {}, {"vmid": None}])]
    )

    assert ops._LastSeenVmids.by_connection == {1: frozenset({100, 101})}


def test_list_all_resources_by_vmid_skips_missing_vmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops,
        "list_all_resources",
        lambda: [{"vmid": "100", "node": "a"}, {"node": "b"}, {"vmid": 7}],
    )
    assert ops.list_all_resources_by_vmid() == {
        100: {"vmid": "100", "node": "a"},
        7: {"vmid": 7},
    }


def test_find_vm_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ops,
        "_pool_vms",
        lambda **_: [{"vmid": 9000, "template": 0}, {"vmid": 9001, "template": 1}],
    )

    assert ops.find_vm_template(9001)["vmid"] == 9001
    with pytest.raises(NotFoundError):
        ops.find_vm_template(9000)


def test_platform_template_vmids_reads_ready_and_updating_only() -> None:
    from sqlalchemy.pool import StaticPool

    from app.models import VMTemplate, VMTemplateStatus

    engine = sqlmodel.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    sqlmodel.SQLModel.metadata.create_all(engine)
    with sqlmodel.Session(engine) as session:
        for vmid, status in [
            (901, VMTemplateStatus.ready),
            (902, VMTemplateStatus.updating),
            (903, VMTemplateStatus.creating),
            (904, VMTemplateStatus.failed),
            (905, VMTemplateStatus.deleted),
        ]:
            session.add(
                VMTemplate(
                    pve_vmid=vmid,
                    name=f"tpl-{vmid}",
                    node="pve1",
                    resource_type="lxc",
                    status=status,
                )
            )
        session.commit()

    # creating 的母機還是使用者的機器、failed／deleted 已不是範本
    assert _real_platform_template_vmids(db_engine=engine) == {901, 902}


# ---------------------------------------------------------------------------
# node picking
# ---------------------------------------------------------------------------


def test_get_available_nodes_falls_back_to_all_when_none_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "admin_disabled_node_names", set)
    monkeypatch.setattr(
        ops, "list_nodes", lambda: [{"name": "pve1"}, {"node": "pve2", "status": "?"}]
    )
    assert len(ops.get_available_nodes()) == 2


def test_default_node_candidates_dedupe_and_skip_broken_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    defaults: dict[int, Any] = {1: "pve1", 3: "pve1", 4: None, 5: "pve5"}

    def _settings_for(key: int) -> ProxmoxSettings:
        if key == 2:
            raise RuntimeError("password decrypt failed")
        return _settings(default_node=defaults[key])

    monkeypatch.setattr(ops, "_connection_keys", lambda: [1, 2, 3, 4, 5])
    monkeypatch.setattr(ops, "get_proxmox_settings", _settings_for)

    assert ops._default_node_candidates() == ["pve1", "pve5"]


def test_pick_target_node_without_nodes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "get_available_nodes", list)
    with pytest.raises(ProxmoxError, match="No Proxmox nodes"):
        ops.pick_target_node()


def test_pick_target_node_uses_default_then_first_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ops, "get_available_nodes", lambda: [{"node": "pve1"}, {"node": "pve2"}]
    )
    monkeypatch.setattr(ops, "_default_node_candidates", lambda: ["pve2"])
    assert ops.pick_target_node() == "pve2"

    monkeypatch.setattr(ops, "_default_node_candidates", lambda: ["gone"])
    assert ops.pick_target_node() == "pve1"

    monkeypatch.setattr(ops, "_default_node_candidates", list)
    assert ops.pick_target_node() == "pve1"


def test_pick_target_node_rejects_nameless_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "get_available_nodes", lambda: [{"status": "online"}])
    monkeypatch.setattr(ops, "_default_node_candidates", list)
    with pytest.raises(ProxmoxError, match="No usable Proxmox node name"):
        ops.pick_target_node()


# ---------------------------------------------------------------------------
# storage selection
# ---------------------------------------------------------------------------


def test_list_node_storages(api: MagicMock) -> None:
    api.nodes.return_value.storage.get.return_value = [{"storage": "local"}]
    assert ops.list_node_storages("pve1") == [{"storage": "local"}]
    api.nodes.assert_called_with("pve1")


def test_storage_flag_helpers() -> None:
    assert ops._storage_is_enabled({"enabled": 1}) is True
    assert ops._storage_is_enabled({"enabled": "0"}) is False
    assert ops._storage_is_enabled({"disable": 1}) is False
    assert ops._storage_is_enabled({}) is True
    assert ops._storage_is_active({"active": 0}) is False
    assert ops._storage_is_active({"status": "disabled"}) is False
    assert ops._storage_is_active({"status": "available"}) is True
    assert ops._storage_supports_content({}, "images") is True
    assert ops._storage_supports_content({"content": "iso, images"}, "images") is True
    assert ops._storage_supports_content({"content": "rootdir"}, "images") is False


_STORAGES = [
    {"storage": "local", "content": "iso,vztmpl", "enabled": 1, "active": 1},
    {"storage": "ssd", "content": "images,rootdir", "enabled": 1, "active": 1},
    {"storage": "nas", "content": "images", "enabled": 0, "active": 1},
]


def test_resolve_target_storage_keeps_requested_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "list_node_storages", lambda _n: _STORAGES)
    assert ops.resolve_target_storage("pve1", "ssd", required_content="images") == "ssd"


def test_resolve_target_storage_falls_back_to_first_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "list_node_storages", lambda _n: _STORAGES)
    assert (
        ops.resolve_target_storage("pve1", "nas", required_content="rootdir") == "ssd"
    )
    assert ops.resolve_target_storage("pve1", None, required_content="images") == "ssd"


def test_resolve_target_storage_without_named_storage_uses_configured_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "list_node_storages", lambda _n: [{"content": "images"}])
    monkeypatch.setattr(
        ops, "get_proxmox_settings_for_node", lambda _n: _settings(data_storage="cfg")
    )
    with pytest.raises(BadRequestError, match="'cfg'.*Node storages: none"):
        ops.resolve_target_storage("pve1", None, required_content="images")


# ---------------------------------------------------------------------------
# config / storage content / simple resource calls
# ---------------------------------------------------------------------------


def test_get_config_pending_and_current(api: MagicMock) -> None:
    config = api.nodes.return_value.qemu.return_value.config
    config.get.side_effect = lambda **kw: {"current": kw.get("current")}

    assert ops.get_config("pve1", 100, "qemu") == {"current": None}
    assert ops.get_config("pve1", 100, "qemu", current=True) == {"current": 1}


def test_list_storage_content_with_and_without_filter(api: MagicMock) -> None:
    content = api.nodes.return_value.storage.return_value.content
    content.get.side_effect = lambda **kw: [kw] if kw else None

    assert ops.list_storage_content("pve1", "local", "iso") == [{"content": "iso"}]
    assert ops.list_storage_content("pve1", "local") == []


def test_list_iso_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ops, "get_proxmox_settings_for_node", lambda _n: _settings(iso_storage="")
    )
    assert ops.list_iso_images("pve1") == []

    monkeypatch.setattr(
        ops, "get_proxmox_settings_for_node", lambda _n: _settings(iso_storage="iso")
    )
    monkeypatch.setattr(
        ops,
        "list_storage_content",
        lambda node, storage, content: [
            {"volid": "iso:iso/a.iso", "content": "iso"},
            {"volid": "iso:iso/b.iso"},
            {"volid": "iso:vztmpl/c.tar", "content": "vztmpl"},
        ],
    )
    assert [i["volid"] for i in ops.list_iso_images("pve1")] == [
        "iso:iso/a.iso",
        "iso:iso/b.iso",
    ]


def test_resize_snapshots_rrd_and_tickets(api: MagicMock) -> None:
    node = api.nodes.return_value
    qemu = node.qemu.return_value
    qemu.snapshot.get.return_value = [{"name": "s1"}]
    qemu.rrddata.get.return_value = [{"cpu": 0.1}]
    node.rrddata.get.return_value = [{"loadavg": 1}]
    node.lxc.return_value.termproxy.post.return_value = {"port": 5900}

    ops.resize_disk("pve1", 100, "qemu", "scsi0", "+5G")
    qemu.resize.put.assert_called_once_with(disk="scsi0", size="+5G")
    assert ops.list_snapshots("pve1", 100, "qemu") == [{"name": "s1"}]
    assert ops.get_rrd_data("pve1", 100, "qemu", "hour") == [{"cpu": 0.1}]
    assert ops.get_node_rrd_data("pve1", "day") == [{"loadavg": 1}]
    node.rrddata.get.assert_called_with(timeframe="day")
    assert ops.get_terminal_ticket("pve1", 101) == {"port": 5900}


def test_delete_resource_waits_and_invalidates(
    api: MagicMock, waits: list, invalidations: list
) -> None:
    api.nodes.return_value.lxc.return_value.delete.return_value = "UPID:del"

    assert ops.delete_resource("pve1", 101, "lxc", purge=1) == "UPID:del"
    api.nodes.return_value.lxc.return_value.delete.assert_called_once_with(purge=1)
    assert waits == [("pve1", "UPID:del", {})]
    assert invalidations == [1]


# ---------------------------------------------------------------------------
# get_ip_address
# ---------------------------------------------------------------------------


def test_get_ip_address_lxc_skips_unusable_interfaces(api: MagicMock) -> None:
    api.nodes.return_value.lxc.return_value.interfaces.get.return_value = [
        {"name": "lo", "inet": "127.0.0.1/8"},
        {"name": "eth9"},
        {"name": "eth1", "inet": "169.254.0.5/16"},
        {"name": "eth0", "inet": "10.0.0.5/24"},
    ]
    assert ops.get_ip_address("pve1", 101, "lxc") == "10.0.0.5"


def _agent_get(api: MagicMock) -> MagicMock:
    return api.nodes.return_value.qemu.return_value.return_value.return_value.get


def test_get_ip_address_qemu_reads_guest_agent(api: MagicMock) -> None:
    _agent_get(api).return_value = {
        "result": [
            {"name": "lo", "ip-addresses": [{"ip-address-type": "ipv4"}]},
            {"name": "eth1"},
            {
                "name": "eth0",
                "ip-addresses": [
                    {"ip-address-type": "ipv6", "ip-address": "fe80::1"},
                    {"ip-address-type": "ipv4", "ip-address": "169.254.3.3"},
                    {"ip-address-type": "ipv4", "ip-address": "192.168.1.20"},
                ],
            },
        ]
    }
    assert ops.get_ip_address("pve1", 100, "qemu") == "192.168.1.20"
    api.nodes.return_value.qemu.return_value.assert_called_with("agent")


def test_get_ip_address_qemu_without_result_or_on_error(api: MagicMock) -> None:
    _agent_get(api).return_value = {}
    assert ops.get_ip_address("pve1", 100, "qemu") is None

    _agent_get(api).side_effect = RuntimeError("QEMU guest agent is not running")
    assert ops.get_ip_address("pve1", 100, "qemu") is None


# ---------------------------------------------------------------------------
# get_current_specs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resource_type", "config", "expected"),
    [
        (
            "qemu",
            {"cores": 4, "memory": 4096, "scsi0": "local-lvm:vm-100-disk-0,size=32G"},
            {"cpu": 4, "memory": 4096, "disk": 32},
        ),
        (
            "qemu",
            {"cores": 2, "memory": 2048, "scsi0": "local:vm-100-disk-0,size=512M"},
            {"cpu": 2, "memory": 2048, "disk": None},
        ),
        ("qemu", {"cores": 1}, {"cpu": 1, "memory": None, "disk": None}),
        (
            "lxc",
            {"cpus": 2, "memory": 1024, "rootfs": "local-lvm:subvol-101,size=8G"},
            {"cpu": 2, "memory": 1024, "disk": 8},
        ),
        (
            "lxc",
            {"cpus": 1, "rootfs": "local-lvm:subvol-101,size=900M"},
            {"cpu": 1, "memory": None, "disk": None},
        ),
        ("lxc", {}, {"cpu": None, "memory": None, "disk": None}),
    ],
)
def test_get_current_specs_reads_effective_config(
    monkeypatch: pytest.MonkeyPatch,
    resource_type: str,
    config: dict,
    expected: dict,
) -> None:
    seen: dict[str, Any] = {}

    def _get_config(node: str, vmid: int, rtype: str, *, current: bool) -> dict:
        seen["current"] = current
        return config

    monkeypatch.setattr(ops, "get_config", _get_config)

    assert ops.get_current_specs("pve1", 100, resource_type) == expected  # type: ignore[arg-type]
    assert seen["current"] is True


# ---------------------------------------------------------------------------
# create / clone / template
# ---------------------------------------------------------------------------


def test_create_lxc_waits_and_invalidates(
    api: MagicMock, waits: list, invalidations: list
) -> None:
    api.nodes.return_value.lxc.create.return_value = "UPID:create"

    assert ops.create_lxc("pve1", vmid=101, hostname="c1") == "UPID:create"
    api.nodes.return_value.lxc.create.assert_called_once_with(vmid=101, hostname="c1")
    assert waits == [("pve1", "UPID:create", {})]
    assert invalidations == [1]


def test_clone_source_lock_without_postgres_uses_process_lock() -> None:
    engine = sqlmodel.create_engine("sqlite://")
    with _real_clone_source_lock(213, db_engine=engine):
        assert ops._clone_source_thread_lock.locked()
    assert not ops._clone_source_thread_lock.locked()


def test_clone_source_lock_release_failure_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = MagicMock()
    connection.rollback.side_effect = RuntimeError("connection gone")
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.connect.return_value.__enter__.return_value = connection

    with caplog.at_level("WARNING", logger=ops.logger.name):
        with _real_clone_source_lock(213, db_engine=engine):
            pass
    assert "clone source lock" in caplog.text


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, ops.TEMPLATE_GUEST_NOTE),
        ("學生用 Ubuntu 22.04", f"{ops.TEMPLATE_GUEST_NOTE}\n\n學生用 Ubuntu 22.04"),
        # 重試轉換不重複加標記
        (
            f"{ops.TEMPLATE_GUEST_NOTE}\n\n學生用 Ubuntu 22.04",
            f"{ops.TEMPLATE_GUEST_NOTE}\n\n學生用 Ubuntu 22.04",
        ),
    ],
)
@pytest.mark.parametrize("resource_type", ["qemu", "lxc"])
def test_protect_and_unprotect_template_guest(
    monkeypatch: pytest.MonkeyPatch,
    resource_type: str,
    existing: str | None,
    expected: str,
) -> None:
    updates: list[tuple] = []
    monkeypatch.setattr(
        ops,
        "get_config",
        lambda node, vmid, rt: {} if existing is None else {"description": existing},
    )
    monkeypatch.setattr(
        ops, "update_config", lambda *a, **kw: updates.append((*a, kw))
    )

    ops.protect_template_guest("pve1", 213, resource_type)
    ops.unprotect_template_guest("pve1", 213, resource_type)

    assert updates == [
        (
            "pve1",
            213,
            resource_type,
            {"protection": 1, "onboot": 0, "description": expected},
        ),
        ("pve1", 213, resource_type, {"protection": 0}),
    ]


@pytest.mark.parametrize(
    ("inherited", "expected"),
    [
        # PVE 複製會原樣抄來源的 protection 與備註
        (
            {"protection": 1, "description": ops.TEMPLATE_GUEST_NOTE},
            {"delete": "protection,description"},
        ),
        (
            {"protection": "1", "description": f"{ops.TEMPLATE_GUEST_NOTE}\n\n備註"},
            {"description": "備註", "delete": "protection"},
        ),
        # 舊的 PVE 範本沒有這些標記：什麼都不改
        ({"description": "自己的備註"}, None),
        ({}, None),
    ],
)
def test_clone_vm_clears_inherited_template_marks_on_target_node(
    api: MagicMock,
    waits: list,
    invalidations: list,
    monkeypatch: pytest.MonkeyPatch,
    inherited: dict[str, Any],
    expected: dict[str, Any] | None,
) -> None:
    api.nodes.return_value.qemu.return_value.clone.post.return_value = "UPID:c"
    asked: list[tuple] = []
    updates: list[tuple] = []

    def get_config(node: str, vmid: int, rt: str) -> dict[str, Any]:
        asked.append((node, vmid, rt))
        return inherited

    monkeypatch.setattr(ops, "get_config", get_config)
    monkeypatch.setattr(
        ops, "update_config", lambda *a, **kw: updates.append((*a, kw))
    )

    ops.clone_vm("pve1", 9001, newid=150, full=1, target="pve2")

    # 跨節點複製：新機器在 target 節點上
    assert asked == [("pve2", 150, "qemu")]
    assert updates == ([] if expected is None else [("pve2", 150, "qemu", expected)])
    assert invalidations == [1]


def test_clone_lxc_clears_inherited_protection(
    api: MagicMock, waits: list, invalidations: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.nodes.return_value.lxc.return_value.clone.post.return_value = "UPID:c"
    updates: list[tuple] = []
    monkeypatch.setattr(
        ops,
        "get_config",
        lambda node, vmid, rt: {"protection": 1, "description": ops.TEMPLATE_GUEST_NOTE},
    )
    monkeypatch.setattr(
        ops, "update_config", lambda *a, **kw: updates.append((*a, kw))
    )

    ops.clone_lxc("pve1", 213, newid=151, hostname="stu", full=1)

    assert updates == [
        ("pve1", 151, "lxc", {"delete": "protection,description"})
    ]


# ---------------------------------------------------------------------------
# DB-claimed VMIDs (fake sqlmodel Session)
# ---------------------------------------------------------------------------


class _FakeSession:
    results: list[list[Any]] = []

    def __init__(self, _engine: Any) -> None:
        self.statements: list[Any] = []

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def exec(self, statement: Any) -> Any:
        self.statements.append(statement)
        rows = _FakeSession.results.pop(0)
        return SimpleNamespace(all=lambda: rows)


def test_db_claimed_vmids_merges_resources_and_ip_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sqlmodel, "Session", _FakeSession)
    _FakeSession.results = [[100, 101], [101, None, "205"]]

    assert ops._db_claimed_vmids() == {100, 101, 205}


def test_db_template_vmids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqlmodel, "Session", _FakeSession)
    _FakeSession.results = [[9000, "9001"]]

    assert ops._db_template_vmids() == {9000, 9001}


# ---------------------------------------------------------------------------
# LXC templates
# ---------------------------------------------------------------------------


def test_get_lxc_templates_reads_iso_storage(
    api: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ops, "get_proxmox_settings_for_node", lambda _n: _settings(iso_storage="iso")
    )
    content = api.nodes.return_value.storage.return_value.content
    content.get.return_value = [{"volid": "iso:vztmpl/debian.tar.zst"}]

    assert ops.get_lxc_templates("pve1") == [{"volid": "iso:vztmpl/debian.tar.zst"}]
    api.nodes.return_value.storage.assert_called_with("iso")


def test_lxc_template_contents_skips_nameless_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ops, "_lxc_template_cache", ops._LxcTemplateCache())
    monkeypatch.setattr(ops, "get_available_nodes", lambda: [{}, {"node": "pve1"}])
    queried: list[str] = []

    def _templates(node: str) -> list[dict]:
        queried.append(node)
        return [{"volid": "iso:vztmpl/a", "content": "vztmpl"}]

    monkeypatch.setattr(ops, "get_lxc_templates", _templates)

    assert ops.list_lxc_templates() == [{"volid": "iso:vztmpl/a", "content": "vztmpl"}]
    assert queried == ["pve1"]


# ---------------------------------------------------------------------------
# session / VNC tickets (fake httpx)
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, data: dict) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> dict:
        return {"data": self._data}


class _AsyncClient:
    response: _Response = _Response(200, {})
    instances: list[_AsyncClient] = []

    def __init__(self, *, verify: Any) -> None:
        self.verify = verify
        self.posts: list[tuple[str, dict]] = []
        _AsyncClient.instances.append(self)

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.posts.append((url, kwargs))
        return _AsyncClient.response


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> type[_AsyncClient]:
    _AsyncClient.instances = []
    monkeypatch.setattr(ops.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(ops, "get_connection_id_for_node", lambda _n: 2)
    monkeypatch.setattr(
        ops,
        "get_proxmox_settings",
        lambda cid=None: _settings(
            connection_id=cid, ca_cert="PEM" if cid == 2 else None, port=8443
        ),
    )
    monkeypatch.setattr(ops, "get_active_host", lambda cid=None: f"host-{cid}")
    monkeypatch.setattr(ops, "build_ws_ssl_context", lambda cfg: f"ctx-{cfg.ca_cert}")
    return _AsyncClient


async def test_session_ticket_default_connection_without_csrf(
    http: type[_AsyncClient],
) -> None:
    http.response = _Response(200, {"ticket": "PVE:t"})

    assert await ops.get_session_ticket() == ("PVE:t", "")
    assert http.instances[0].verify is False
    assert http.instances[0].posts[0][0].startswith("https://host-None:8443/")


async def test_session_ticket_http_error(http: type[_AsyncClient]) -> None:
    http.response = _Response(401, {})
    with pytest.raises(ProxmoxError, match="HTTP 401"):
        await ops.get_session_ticket("pve2")


async def test_vnc_ticket_sends_cookie_and_csrf(http: type[_AsyncClient]) -> None:
    http.response = _Response(200, {"port": "5900", "ticket": "vnc"})

    result = await ops.get_vnc_ticket_with_session("pve2", 100, "cookie", "csrf")

    assert result == {"port": "5900", "ticket": "vnc"}
    url, kwargs = http.instances[0].posts[0]
    assert url == "https://host-2:8443/api2/json/nodes/pve2/qemu/100/vncproxy"
    assert kwargs["data"] == {"websocket": 1}
    assert kwargs["headers"] == {
        "Cookie": "PVEAuthCookie=cookie",
        "CSRFPreventionToken": "csrf",
    }


async def test_vnc_ticket_without_csrf_and_http_error(
    http: type[_AsyncClient],
) -> None:
    http.response = _Response(500, {})

    with pytest.raises(ProxmoxError, match="HTTP 500"):
        await ops.get_vnc_ticket_with_session("pve2", 100, "cookie", "")

    assert http.instances[0].posts[0][1]["headers"] == {
        "Cookie": "PVEAuthCookie=cookie"
    }


# ---------------------------------------------------------------------------
# PCI mappings
# ---------------------------------------------------------------------------


def test_pci_mapping_helpers(api: MagicMock) -> None:
    proxmox = MagicMock(name="connection-client")

    ops.create_pci_mapping(
        proxmox, mapping_id="gpu", description="H200", map_entries=["node=pve1"]
    )
    proxmox.cluster.mapping.pci.post.assert_called_once_with(
        id="gpu", description="H200", map=["node=pve1"]
    )

    api.nodes.return_value.hardware.pci.return_value.mdev.get.return_value = [
        {"type": "nvidia-1"}
    ]
    assert ops.list_pci_mdev_types("pve1", "0000:01:00.0") == [{"type": "nvidia-1"}]
    api.nodes.return_value.hardware.pci.assert_called_with("0000:01:00.0")


# ---------------------------------------------------------------------------
# connection iteration / node listing / admin-disabled nodes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# status / booting tasks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# backups
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------
