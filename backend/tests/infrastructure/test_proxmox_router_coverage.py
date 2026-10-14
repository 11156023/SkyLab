"""app.infrastructure.proxmox.router 的分支覆蓋：HA 節點讀取、節點狀態更新與叢集節點探測。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.proxmox import router
from app.infrastructure.proxmox.settings import ProxmoxSettings
from app.repositories import proxmox_node as node_repo


def test_get_nodes_for_ha_reads_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _get_all_nodes(_session: Any, *, connection_id: int | None = None) -> list:
        seen["connection_id"] = connection_id
        return ["pve1", "pve2"]

    monkeypatch.setattr(node_repo, "get_all_nodes", _get_all_nodes)

    assert router.get_nodes_for_ha(7) == ["pve1", "pve2"]
    assert seen["connection_id"] == 7


def test_get_nodes_for_ha_returns_empty_list_when_db_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_session: Any, *, connection_id: int | None = None) -> list:
        raise RuntimeError("db down")

    monkeypatch.setattr(node_repo, "get_all_nodes", _boom)

    assert router.get_nodes_for_ha() == []


def test_update_node_online_writes_status(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        node_repo,
        "update_node_status",
        lambda _session, node_id, is_online: calls.append((node_id, is_online)),
    )

    router.update_node_online(5, True)

    assert calls == [(5, True)]


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


def test_fetch_cluster_nodes_falls_back_when_status_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(monkeypatch, RuntimeError("403"))

    nodes = router.fetch_cluster_nodes("10.0.0.1", "u", "p", False, 10, port=443)

    assert nodes == [
        {"name": "10.0.0.1", "host": "10.0.0.1", "port": 443, "is_primary": True}
    ]


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


def test_fetch_cluster_nodes_keeps_local_node_as_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        [
            {"type": "node", "name": "pve1", "ip": "10.0.0.11"},
            {"type": "node", "name": "pve2", "ip": "10.0.0.12", "local": 1},
        ],
    )

    nodes = router.fetch_cluster_nodes("10.0.0.1", "u", "p", False, 10)

    assert [n["is_primary"] for n in nodes] == [False, True]


def test_open_client_passes_connection_options(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _api(host: str, **kwargs: Any) -> str:
        seen.update(host=host, **kwargs)
        return "client"

    monkeypatch.setattr(router, "ProxmoxAPI", _api)

    result = router.open_client(
        "10.0.0.1", port=8006, user="u", password="p", verify_ssl=False, timeout=9
    )

    assert result == "client"
    assert seen == {
        "host": "10.0.0.1",
        "port": 8006,
        "user": "u",
        "password": "p",
        "verify_ssl": False,
        "timeout": 9,
    }


def test_try_connect_validates_client(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_client(monkeypatch, [])
    monkeypatch.setattr(router, "resolve_verify", lambda *_a, **_k: "/ca.pem")
    cfg = ProxmoxSettings(
        host="h",
        user="root@pam",
        password="pw",
        verify_ssl=True,
        iso_storage="local",
        data_storage="local-lvm",
        api_timeout=12,
        task_check_interval=2,
        pool_name="SkyLab",
        port=443,
    )

    client = router.try_connect("10.0.0.5", cfg)

    assert isinstance(client, _FakeClient)
    assert calls[0]["verify_ssl"] == "/ca.pem"
    assert calls[0]["port"] == 443
    assert router.list_node_storages(client, "pve1") == [{"storage": "pve1-local"}]
