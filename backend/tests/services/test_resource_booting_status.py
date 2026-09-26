"""開機 task 還在跑的機器標 ``starting``，主控台不可開（VM 438 事故）。

GPU 直通機開機時 QEMU 要先配置並鎖定整段記憶體，qmstart 跑完前 QMP 不回應，
vncproxy 會 ``set_password`` 逾時；但 cluster/resources 早就回報 running。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import vm as vm_routes
from app.exceptions import ConflictError
from app.infrastructure.proxmox import operations
from app.services.resource import resource_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FakeTasks:
    def __init__(self, node: str, tasks_by_node: dict[str, Any], calls: list[str]):
        self._node = node
        self._tasks_by_node = tasks_by_node
        self._calls = calls

    def get(self, **params: Any) -> list[dict]:
        assert params == {"source": "active"}
        self._calls.append(self._node)
        result = self._tasks_by_node[self._node]
        if isinstance(result, Exception):
            raise result
        return result


def _fake_api(tasks_by_node: dict[str, Any], calls: list[str]):
    def get_api(node: str) -> SimpleNamespace:
        return SimpleNamespace(
            nodes=lambda name: SimpleNamespace(
                tasks=_FakeTasks(name, tasks_by_node, calls)
            )
        )

    return get_api


def test_list_booting_vmids_only_counts_active_boot_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    tasks = {
        "pve": [
            {"type": "qmstart", "id": "438", "status": "RUNNING"},
            {"type": "vncproxy", "id": "439", "status": "RUNNING"},
            {"type": "qmreboot", "id": "440"},
            {"type": "qmstart", "id": "not-a-vmid"},
        ],
        "pve2": [{"type": "vzstart", "id": "500"}],
        "pve3": RuntimeError("node offline"),
    }
    monkeypatch.setattr(
        operations, "get_proxmox_api_for_node", _fake_api(tasks, calls)
    )

    booting = operations.list_booting_vmids(["pve", "pve2", "pve", "pve3", ""])

    assert booting == {438, 440, 500}
    assert calls == ["pve", "pve2", "pve3"]


def _item(vmid: int, status: str, node: str = "pve") -> SimpleNamespace:
    return SimpleNamespace(vmid=vmid, status=status, node=node)


def test_mark_booting_only_rewrites_running_machines_with_boot_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_nodes: list[list[str]] = []

    def fake_booting(nodes: Any) -> set[int]:
        seen_nodes.append(sorted(nodes))
        return {438, 486}

    monkeypatch.setattr(
        resource_service.proxmox_service, "list_booting_vmids", fake_booting
    )
    items = [
        _item(438, "running"),
        _item(101, "running", node="pve210"),
        _item(486, "stopped"),
    ]

    resource_service._mark_booting(items)  # type: ignore[arg-type]

    assert [item.status for item in items] == ["starting", "running", "stopped"]
    assert seen_nodes == [["pve", "pve210"]]


def test_mark_booting_skips_proxmox_when_nothing_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(nodes: Any) -> set[int]:
        raise AssertionError("should not query Proxmox")

    monkeypatch.setattr(resource_service.proxmox_service, "list_booting_vmids", fail)
    items = [_item(1, "stopped"), _item(0, "running")]

    resource_service._mark_booting(items)  # type: ignore[arg-type]

    assert [item.status for item in items] == ["stopped", "running"]


def test_console_refused_while_booting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vm_routes.proxmox_service, "list_booting_vmids", lambda nodes: {438}
    )

    async def must_not_get_ticket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("vncproxy must not be requested while booting")

    monkeypatch.setattr(
        vm_routes.proxmox_service, "get_session_ticket", must_not_get_ticket
    )

    with pytest.raises(ConflictError) as exc_info:
        asyncio.run(
            vm_routes.get_vm_console(438, {"type": "qemu", "node": "pve", "vmid": 438})
        )
    assert exc_info.value.status_code == 409
