from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from app.ai.pve_log import chat as pve_chat_module
from app.ai.pve_log import collector


def test_admin_prompt_limits_scope_and_uses_operator_anomaly_rules():
    prompt = pve_chat_module._SYSTEM_PROMPT

    assert "問題 A 不得自行" in prompt
    assert "不要列出正常資源" in prompt
    assert "不要為了讓回答看起來完整而額外呼叫工具" in prompt
    assert "節點 CPU、記憶體、磁碟使用率達 100% 滿載" in prompt
    assert "VM/LXC 的 stopped、關機" in prompt
    assert "滿載都視為正常狀態" in prompt
    assert "額外錯誤或故障訊號" in prompt
    assert "同一問題範圍內使用 tools 做必要的下一步診斷" in prompt
    assert "不得自行擴張成修復或變更操作" in prompt
    assert "**建議操作：**" in prompt
    assert "不得把收集錯誤算成特定 VM/LXC" in prompt
    assert "同一項狀態、異常與證據不得換句話重複" in prompt
    assert "只保留「異常」" in prompt
    assert "判定規則是內部回答準則" in prompt
    assert "stopped 視為" in prompt
    assert "只有使用者明確詢問判定標準時才說明" in prompt
    assert "結論 → 主要證據 → 建議下一步" in prompt
    assert "依使用者問的是狀態、清單還是原因決定格式" in prompt
    assert "使用此工具不代表必須輸出完整報告" in prompt
    assert "可能原因一律標「待確認」" in prompt
    assert "未詢問原因、" in prompt
    assert "不主動加入其他延伸段落" in prompt


class _Endpoint:
    def __init__(self, calls: Counter[str], key: str, value):
        self._calls = calls
        self._key = key
        self._value = value

    def get(self, **_kwargs):
        self._calls[self._key] += 1
        return self._value


class _NodeEndpoint:
    def __init__(self, calls: Counter[str], node: str):
        self.storage = _Endpoint(calls, f"storage:{node}", [
            {
                "storage": "local-lvm",
                "type": "lvmthin",
                "content": "images",
                "avail": 80,
                "used": 20,
                "total": 100,
                "active": 1,
            }
        ])
        self._calls = calls
        self._node = node

    def qemu(self, vmid: int):
        return _GuestEndpoint(self._calls, self._node, vmid, "qemu")

    def lxc(self, vmid: int):
        return _GuestEndpoint(self._calls, self._node, vmid, "lxc")


class _NodesEndpoint:
    def __init__(self, calls: Counter[str]):
        self._calls = calls
        self._nodes = {
            "pve-a": _NodeEndpoint(calls, "pve-a"),
            "pve-b": _NodeEndpoint(calls, "pve-b"),
        }

    def get(self):
        self._calls["nodes"] += 1
        return [
            {
                "node": node,
                "status": "online",
                "cpu": 0.1,
                "maxcpu": 8,
                "mem": 10,
                "maxmem": 100,
                "disk": 20,
                "maxdisk": 200,
                "uptime": 100,
            }
            for node in self._nodes
        ]

    def __call__(self, node: str):
        return self._nodes[node]


class _GuestEndpoint:
    def __init__(self, calls: Counter[str], node: str, vmid: int, kind: str):
        self.status = SimpleNamespace(
            current=_Endpoint(
                calls,
                f"status:{vmid}",
                {
                    "status": "running",
                    "cpu": 0.1,
                    "cpus": 2,
                    "mem": 10,
                    "maxmem": 100,
                    "diskread": 1,
                    "diskwrite": 2,
                    "maxdisk": 100,
                    "netin": 3,
                    "netout": 4,
                    "uptime": 100,
                    "pid": 10,
                },
            )
        )
        self.config = _Endpoint(
            calls,
            f"config:{vmid}",
            {"cores": 2, "memory": 100, "scsi0": "local-lvm:vm-101-disk-0,size=10G"},
        )
        self.interfaces = _Endpoint(calls, f"interfaces:{vmid}", [])
        self.node = node
        self.kind = kind


def _fake_proxmox(calls: Counter[str]):
    cluster = SimpleNamespace(
        status=_Endpoint(
            calls,
            "cluster",
            [{"type": "cluster", "name": "test", "nodes": 2, "quorate": 1}],
        ),
        resources=_Endpoint(
            calls,
            "resources",
            [
                {
                    "vmid": 101,
                    "name": "test-vm",
                    "type": "qemu",
                    "node": "pve-a",
                    "status": "running",
                    "maxmem": 100,
                    "mem": 10,
                    "maxdisk": 100,
                    "disk": 20,
                },
                {
                    "vmid": 102,
                    "name": "test-lxc",
                    "type": "lxc",
                    "node": "pve-a",
                    "status": "running",
                    "maxmem": 100,
                    "mem": 10,
                    "maxdisk": 100,
                    "disk": 20,
                },
            ],
        ),
    )
    return SimpleNamespace(cluster=cluster, nodes=_NodesEndpoint(calls))


def test_tool_context_collects_only_requested_data_and_reuses_it(monkeypatch):
    calls: Counter[str] = Counter()
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 1)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    monkeypatch.setattr(collector.settings, "collector_max_workers", 4)
    monkeypatch.setattr(collector.settings, "collector_fetch_config", True)
    monkeypatch.setattr(collector.settings, "collector_fetch_lxc_interfaces", True)

    context = collector.PveToolContext(proxmox=_fake_proxmox(calls))

    # node-filtered storage only needs the node list and the requested node's storage.
    assert context.execute("get_storage", {"node": "pve-a"})
    assert calls["nodes"] == 1
    assert calls["storage:pve-a"] == 1
    assert calls["storage:pve-b"] == 0
    assert calls["resources"] == 0
    assert calls["status:101"] == 0
    assert calls["config:101"] == 0

    # A second tool call in the same request reuses pve-a and fetches only pve-b.
    assert len(context.execute("get_storage", {})) == 2
    assert calls["nodes"] == 1
    assert calls["storage:pve-a"] == 1
    assert calls["storage:pve-b"] == 1

    # Detail loads the summary once and then only the selected guest's fields.
    detail = context.execute("get_resource_detail", {"vmid": 101})
    assert detail["summary"]["vmid"] == 101
    assert calls["resources"] == 1
    assert calls["status:101"] == 1
    assert calls["config:101"] == 1

    lxc_detail = context.execute("get_resource_detail", {"vmid": 102})
    assert lxc_detail["summary"]["resource_type"] == "lxc"
    assert calls["status:102"] == 1
    assert calls["config:102"] == 1
    assert calls["interfaces:102"] == 1
    assert calls["interfaces:101"] == 0
    context.execute("get_resource_detail", {"vmid": 101})
    assert calls["resources"] == 1
    assert calls["status:101"] == 1
    assert calls["config:101"] == 1

    # Scope checks happen before detail reads and resource lists are filtered
    # server-side; the context never widens the allowed VMID set.
    assert "指定範圍" in context.execute(
        "get_resource_detail", {"vmid": 101}, allowed_vmids={999}
    )["error"]
    assert context.execute("get_resources", {}, allowed_vmids={999}) == []
    assert calls["resources"] == 1
    assert calls["status:101"] == 1
    assert calls["config:101"] == 1

    # Cluster is independent and is also cached per request.
    assert context.execute("get_cluster", {})["quorate"] is True
    context.execute("get_cluster", {})
    assert calls["cluster"] == 1


@pytest.mark.asyncio
async def test_chat_does_not_collect_full_snapshot_for_storage_tool(monkeypatch):
    calls: Counter[str] = Counter()
    proxmox = _fake_proxmox(calls)
    responses = [
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "storage-call",
                                "type": "function",
                                "function": {
                                    "name": "get_storage",
                                    "arguments": '{"node": "pve-a"}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        {"choices": [{"message": {"role": "assistant", "content": "完成"}}]},
    ]

    async def fake_completion(_payload, *, timeout):
        del timeout
        return responses.pop(0)

    monkeypatch.setattr(collector, "get_proxmox_api", lambda: proxmox)
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 1)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    monkeypatch.setattr(collector.settings, "collector_max_workers", 4)
    monkeypatch.setattr(
        pve_chat_module,
        "settings",
        SimpleNamespace(
            VLLM_BASE_URL="http://vllm/v1",
            VLLM_MODEL_NAME="test-model",
            VLLM_TIMEOUT=30,
        ),
    )
    monkeypatch.setattr(
        pve_chat_module.vllm_client,
        "create_chat_completion",
        fake_completion,
    )
    monkeypatch.setattr(
        pve_chat_module,
        "collect_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("full snapshot is not allowed")),
    )

    result = await pve_chat_module.chat(message="查 pve-a 儲存空間")

    assert result.reply == "完成"
    assert calls["nodes"] == 1
    assert calls["storage:pve-a"] == 1
    assert calls["resources"] == 0
    assert calls["cluster"] == 0
