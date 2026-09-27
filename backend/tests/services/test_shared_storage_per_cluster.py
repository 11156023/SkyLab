"""共享儲存以叢集為單位：不同連線同名的共享儲存不可合併容量。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.vm import placement_support


def _storage(node: str, name: str, *, shared: bool, avail: float) -> SimpleNamespace:
    return SimpleNamespace(
        node_name=node,
        storage=name,
        is_shared=shared,
        total_gb=avail * 2,
        avail_gb=avail,
        can_vm=True,
        can_lxc=True,
        active=True,
        enabled=True,
        speed_tier="ssd",
        user_priority=5,
    )


def test_same_named_shared_storage_is_one_pool_per_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storages = [
        _storage("a1", "ceph", shared=True, avail=100),
        _storage("a2", "ceph", shared=True, avail=100),
        _storage("b1", "ceph", shared=True, avail=500),
    ]
    monkeypatch.setattr(
        placement_support.proxmox_storage_repo,
        "get_all_storages",
        lambda _session: storages,
    )
    monkeypatch.setattr(
        placement_support.proxmox_node_repo,
        "get_node_connection_map",
        lambda _session: {
            "a1": (1, "cluster-a"),
            "a2": (1, "cluster-a"),
            "b1": (2, "cluster-b"),
        },
    )

    by_node, managed = placement_support.build_storage_pool_state(
        session=None, node_names=["a1", "a2", "b1"]
    )

    assert managed is True
    # 同一叢集的節點共用同一個池物件（扣容量才會一起扣）
    assert by_node["a1"][0] is by_node["a2"][0]
    # 另一個叢集的同名共享儲存是獨立的池
    assert by_node["b1"][0] is not by_node["a1"][0]
    assert by_node["b1"][0].avail_gb == 500
