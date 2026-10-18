"""app.infrastructure.proxmox.router 的分支覆蓋：HA 節點讀取、節點狀態更新與叢集節點探測。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.proxmox import router
from app.repositories import proxmox_node as node_repo


def test_get_nodes_for_ha_returns_empty_list_when_db_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_session: Any, *, connection_id: int | None = None) -> list:
        raise RuntimeError("db down")

    monkeypatch.setattr(node_repo, "get_all_nodes", _boom)

    assert router.get_nodes_for_ha() == []


def test_update_node_online_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_session: Any, _node_id: int, _is_online: bool) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(node_repo, "update_node_status", _boom)

    assert router.update_node_online(5, False) is None


class _FakeClient:
    def __init__(self, status: Any) -> None:
        self._status = status
        self.cluster = SimpleNamespace(status=SimpleNamespace(get=self._get_status))
        self.version = SimpleNamespace(get=lambda: {"version": "8"})

    def _get_status(self) -> list[dict]:
        if isinstance(self._status, Exception):
            raise self._status
        return self._status

    def nodes(self, node: str) -> Any:
        return SimpleNamespace(
            storage=SimpleNamespace(get=lambda: [{"storage": f"{node}-local"}])
        )


def _patch_client(monkeypatch: pytest.MonkeyPatch, status: Any) -> list[dict]:
    calls: list[dict] = []

    def _open(host: str, **kwargs: Any) -> _FakeClient:
        calls.append({"host": host, **kwargs})
        return _FakeClient(status)

    monkeypatch.setattr(router, "open_client", _open)
    return calls


def test_fetch_cluster_nodes_without_node_items_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, [{"type": "cluster", "name": "lab"}])

    nodes = router.fetch_cluster_nodes("10.0.0.1", "u", "p", False, 10)

    assert nodes == [
        {"name": "10.0.0.1", "host": "10.0.0.1", "port": 8006, "is_primary": True}
    ]


def test_fetch_cluster_nodes_marks_first_primary_when_none_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        [
            {"type": "cluster", "name": "lab"},
            {"type": "node", "name": "pve1", "ip": "10.0.0.11"},
            {"type": "node", "name": "pve2"},
        ],
    )

    nodes = router.fetch_cluster_nodes("10.0.0.1", "u", "p", False, 10)

    assert nodes == [
        {"name": "pve1", "host": "10.0.0.11", "port": 8006, "is_primary": True},
        {"name": "pve2", "host": "pve2", "port": 8006, "is_primary": False},
    ]
