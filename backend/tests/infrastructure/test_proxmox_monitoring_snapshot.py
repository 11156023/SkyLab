"""PVE monitoring snapshot collector tests."""

from types import SimpleNamespace

import pytest

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import operations


class _Endpoint:
    def __init__(self, values):
        self.values = values
        self.calls = 0

    def get(self, **_kwargs):
        self.calls += 1
        return self.values


class _Client:
    def __init__(self, nodes, resources):
        self.nodes = _Endpoint(nodes)
        self.cluster = SimpleNamespace(resources=_Endpoint(resources))


def test_collect_monitoring_snapshot_fetches_each_connection_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(
        [{"node": "pve1", "status": "online"}],
        [
            {"vmid": 100, "pool": "campus", "type": "qemu"},
            {"vmid": 101, "pool": "other", "type": "qemu"},
        ],
    )
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda _key: client)
    monkeypatch.setattr(
        operations,
        "get_proxmox_settings",
        lambda _key: SimpleNamespace(pool_name="campus"),
    )

    snapshot = operations.collect_monitoring_snapshot()

    assert snapshot.nodes == [{"node": "pve1", "status": "online"}]
    assert snapshot.resources == [{"vmid": 100, "pool": "campus", "type": "qemu"}]
    assert snapshot.failed_connections == 0
    assert snapshot.total_connections == 1
    assert client.nodes.calls == 1
    assert client.cluster.resources.calls == 1


def test_collect_monitoring_snapshot_preserves_partial_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client([{"node": "pve1", "status": "online"}], [])
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1, 2])

    def get_client(key):
        if key == 2:
            raise RuntimeError("connection unavailable")
        return client

    monkeypatch.setattr(operations, "get_proxmox_api", get_client)
    monkeypatch.setattr(
        operations,
        "get_proxmox_settings",
        lambda _key: SimpleNamespace(pool_name="campus"),
    )

    snapshot = operations.collect_monitoring_snapshot()

    assert snapshot.failed_connections == 1
    assert snapshot.total_connections == 2
    assert snapshot.nodes == [{"node": "pve1", "status": "online"}]


def test_collect_monitoring_snapshot_raises_when_all_connections_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1, 2])
    monkeypatch.setattr(
        operations,
        "get_proxmox_api",
        lambda _key: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    with pytest.raises(ProxmoxError, match="All Proxmox connections are unavailable"):
        operations.collect_monitoring_snapshot()
