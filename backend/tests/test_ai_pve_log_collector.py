from types import SimpleNamespace

import pytest
from proxmoxer.core import ResourceException

from app.ai.pve_log import collector
from app.ai.pve_log.chat import _execute_tool_sync


class _FakeResourceEndpoint:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "vmid": 101,
                "name": "test-vm",
                "type": "qemu",
                "node": "pve",
                "status": "stopped",
            },
            {
                "vmid": 102,
                "name": "test-lxc",
                "type": "lxc",
                "node": "pve",
                "status": "stopped",
            },
        ]


def test_tool_context_resources_use_single_query_and_include_lxc():
    resources = _FakeResourceEndpoint()
    proxmox = SimpleNamespace(
        cluster=SimpleNamespace(resources=resources),
    )
    context = collector.PveToolContext(proxmox=proxmox)

    first = context.execute("get_resources", {})
    second = context.execute("get_resources", {"resource_type": "lxc"})

    # 同一 request 內只查一次 cluster.resources，且 VM 與 LXC 都收進來
    assert resources.calls == [{"type": "vm"}]
    assert [(item["vmid"], item["resource_type"]) for item in first] == [
        (101, "qemu"),
        (102, "lxc"),
    ]
    assert [item["vmid"] for item in second] == [102]
    assert context.errors == []


# ── _retry 經由 PveToolContext 的覆蓋（原 collect_snapshot 版本已隨死碼移除）──


def _fake_pve(monkeypatch, cluster_get):
    proxmox = SimpleNamespace(
        cluster=SimpleNamespace(
            status=SimpleNamespace(get=cluster_get),
            resources=SimpleNamespace(get=lambda **kwargs: []),
        ),
        nodes=SimpleNamespace(get=lambda: []),
    )
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 3)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    return collector.PveToolContext(proxmox=proxmox)


def test_tool_context_retries_before_returning_partial_cluster(monkeypatch):
    calls = []

    def unavailable():
        calls.append(1)
        raise OSError("synthetic unavailable")

    context = _fake_pve(monkeypatch, unavailable)
    tool = _execute_tool_sync(context, "get_cluster", {})
    assert len(calls) == 3
    assert context.errors
    assert tool.get("error")
    assert tool["quorate"] is False


def test_tool_context_transient_cluster_failure_recovers(monkeypatch):
    calls = []

    def recover():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("temporary")
        return [{"type": "cluster", "name": "test", "nodes": 2, "quorate": 1}]

    context = _fake_pve(monkeypatch, recover)
    tool = _execute_tool_sync(context, "get_cluster", {})
    assert len(calls) == 2
    assert tool["quorate"] is True
    assert "error" not in tool
    assert context.errors == []


@pytest.mark.parametrize(
    "status,expected_calls", [(401, 1), (403, 1), (404, 1), (429, 3), (503, 3)]
)
def test_tool_context_retries_only_transient_http_failures(
    monkeypatch, status, expected_calls
):
    calls = []

    def unavailable():
        calls.append(1)
        raise ResourceException(status, "synthetic", "unavailable")

    context = _fake_pve(monkeypatch, unavailable)
    _execute_tool_sync(context, "get_cluster", {})
    assert len(calls) == expected_calls
    assert context.errors
