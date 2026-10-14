"""Coverage tests for app.ai.pve_log.collector PveToolContext branches."""

from typing import Any

import pytest

from app.ai.pve_log import collector


class _Api:
    """Minimal proxmoxer stand-in: attribute/call chains resolve to a path key."""

    def __init__(self, handlers: dict[str, Any], path: tuple[str, ...] = ()) -> None:
        self._handlers = handlers
        self._path = path

    def __getattr__(self, name: str) -> "_Api":
        if name.startswith("_"):
            raise AttributeError(name)
        return _Api(self._handlers, self._path + (name,))

    def __call__(self, *args: Any) -> "_Api":
        return _Api(self._handlers, self._path + tuple(str(a) for a in args))

    def get(self, **kwargs: Any) -> Any:
        handler = self._handlers["/".join(self._path)]
        if isinstance(handler, Exception):
            raise handler
        return handler(**kwargs) if callable(handler) else handler


@pytest.fixture(autouse=True)
def _fast_settings(monkeypatch):
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 1)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    monkeypatch.setattr(collector.settings, "collector_max_workers", 2)
    monkeypatch.setattr(collector.settings, "collector_fetch_config", True)
    monkeypatch.setattr(collector.settings, "collector_fetch_lxc_interfaces", True)


_RESOURCES = [
    {"vmid": 101, "name": "vm", "type": "qemu", "node": "pve", "status": "running"},
    {"vmid": 102, "name": "ct", "type": "lxc", "node": "pve", "status": "running"},
    {"vmid": 103, "name": "ct2", "type": "lxc", "node": "pve2", "status": "stopped"},
]


def test_retry_sleeps_with_backoff(monkeypatch) -> None:
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 2)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0.5)
    sleeps: list[float] = []
    monkeypatch.setattr(collector.time, "sleep", sleeps.append)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("once")
        return "ok"

    assert collector._retry(flaky) == "ok"
    assert sleeps == [0.5]


def test_cluster_info_counts_standalone_node() -> None:
    api = _Api({"cluster/status": [{"type": "node"}, {"type": "node"}]})
    info = collector._collect_cluster_info(api)
    assert info.node_count == 1
    assert info.is_cluster is False


def test_lxc_detail_collects_interfaces_and_tolerates_bad_disk_size() -> None:
    api = _Api(
        {
            "cluster/resources": _RESOURCES,
            "nodes/pve/lxc/102/status/current": {"status": "running", "cpus": 2},
            "nodes/pve/lxc/102/config": {
                "hostname": "ct",
                "rootfs": "local:102/disk.raw,size=abcG",
            },
            "nodes/pve/lxc/102/interfaces": [
                {"name": "eth0", "inet": "10.0.0.2/24"},
                {"inet6": "fe80::1/64"},
            ],
        }
    )
    context = collector.PveToolContext(proxmox=api)
    detail = context.execute("get_resource_detail", {"vmid": "102"})
    assert detail["config"]["disk_size_gb"] is None
    assert detail["config"]["name"] == "ct"
    assert [item["name"] for item in detail["network_interfaces"]] == [
        "eth0",
        "unknown",
    ]
    assert context.errors == []


def test_detail_field_failures_are_recorded(monkeypatch) -> None:
    monkeypatch.setattr(collector.settings, "collector_fetch_config", False)
    api = _Api(
        {
            "cluster/resources": _RESOURCES,
            "nodes/pve/lxc/102/status/current": OSError("status down"),
            "nodes/pve/lxc/102/interfaces": OSError("iface down"),
        }
    )
    context = collector.PveToolContext(proxmox=api)
    detail = context.execute("get_resource_detail", {"vmid": 102})
    assert detail["status"] is None
    assert detail["config"] is None
    assert detail["network_interfaces"] == []
    assert len(context.errors) == 2


def test_resource_filters_and_lookup_errors() -> None:
    context = collector.PveToolContext(proxmox=_Api({"cluster/resources": _RESOURCES}))
    by_node = context.execute("get_resources", {"node": "pve2"})
    assert [item["vmid"] for item in by_node] == [103]
    by_status = context.execute("get_resources", {"status": "running"})
    assert [item["vmid"] for item in by_status] == [101, 102]
    scoped = context.execute("get_resources", {}, allowed_vmids={101})
    assert [item["vmid"] for item in scoped] == [101]

    assert "vmid" in context.execute("get_resource_detail", {"vmid": "abc"})["error"]
    assert "vmid" in context.execute("get_resource_detail", {})["error"]
    assert context.execute("get_resource_detail", {"vmid": 101}, allowed_vmids={102})[
        "error"
    ]
    assert context.execute("get_resource_detail", {"vmid": 999})["error"]
    assert context.execute("no_such_tool", {})["error"]


def test_multi_connection_failures_are_partial(monkeypatch) -> None:
    good = _Api(
        {
            "nodes": [{"node": "pve", "status": "online"}],
            "nodes/pve/storage": OSError("storage down"),
            "cluster/resources": OSError("resources down"),
        }
    )
    bad = _Api(
        {
            "nodes": OSError("nodes down"),
            "cluster/resources": OSError("resources down"),
        }
    )
    clients = {1: good, 2: bad}
    monkeypatch.setattr(collector, "list_enabled_connection_ids", lambda: [1, 2])
    monkeypatch.setattr(collector, "get_proxmox_api", lambda cid: clients[cid])
    context = collector.PveToolContext()

    nodes = context.execute("get_nodes", {})
    assert [item["node"] for item in nodes] == ["pve"]
    assert any("連線 2 節點清單" in item for item in context.errors)

    assert context.execute("get_storage", {"node": "ghost"}) == []
    assert context.execute("get_storage", {"node": "pve"}) == []
    assert any("節點 pve 儲存空間" in item for item in context.errors)

    detail = context.execute("get_resource_detail", {"vmid": 101})
    assert "無法取得" in detail["error"]
    assert any("連線 1 cluster.resources" in item for item in context.errors)


def test_default_connection_failures_use_generic_labels(monkeypatch) -> None:
    broken = _Api(
        {
            "nodes": OSError("nodes down"),
            "cluster/resources": OSError("resources down"),
        }
    )
    monkeypatch.setattr(collector, "list_enabled_connection_ids", lambda: [])
    monkeypatch.setattr(collector, "get_proxmox_api", lambda *_a: broken)
    context = collector.PveToolContext()
    assert context.execute("get_nodes", {}) == []
    assert context.execute("get_resources", {}) == []
    assert any(item.startswith("節點清單") for item in context.errors)
    assert any(item.startswith("cluster.resources") for item in context.errors)


def test_client_for_unknown_node_is_resolved_and_cached(monkeypatch) -> None:
    resolved: list[str] = []
    api = _Api({})

    def for_node(node: str) -> _Api:
        resolved.append(node)
        return api

    monkeypatch.setattr(collector, "get_proxmox_api_for_node", for_node)
    context = collector.PveToolContext()
    assert context._client_for_node("pve9") is api
    assert context._client_for_node("pve9") is api
    assert resolved == ["pve9"]
