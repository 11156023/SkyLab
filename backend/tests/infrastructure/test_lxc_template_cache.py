"""LXC 範本（vztmpl）清單的節點快取。

範本是管理員在 PVE 上手動放的，很少變動：範本清單與 node map 共用一份快取，
不再各自逐節點打 PVE；某節點暫時連不到時沿用上一輪結果，不讓它從清單上消失。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.infrastructure.proxmox import client as proxmox_client
from app.infrastructure.proxmox import operations as ops
from app.services.proxmox import provisioning_service

DEBIAN = {"volid": "ISO:vztmpl/debian-12.tar.zst", "content": "vztmpl", "size": 1}
UBUNTU = {"volid": "ISO:vztmpl/ubuntu-24.tar.zst", "content": "vztmpl", "size": 2}
ISO = {"volid": "ISO:iso/win.iso", "content": "iso", "size": 3}


class _Storage:
    """假的逐節點 storage content：記錄呼叫次數，可指定哪些節點連不上。"""

    def __init__(self) -> None:
        self.items: dict[str, list[dict[str, Any]]] = {
            "pve1": [DEBIAN, ISO],
            "pve2": [DEBIAN, UBUNTU],
        }
        self.down: set[str] = set()
        self.calls: list[str] = []

    def __call__(self, node: str) -> list[dict[str, Any]]:
        self.calls.append(node)
        if node in self.down:
            raise ConnectionError(f"{node} unreachable")
        return [dict(item) for item in self.items[node]]


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> _Storage:
    proxmox_client.invalidate_proxmox_client()
    fake = _Storage()
    monkeypatch.setattr(ops, "get_lxc_templates", fake)
    monkeypatch.setattr(
        ops, "get_available_nodes", lambda: [{"node": "pve1"}, {"node": "pve2"}]
    )
    yield fake
    proxmox_client.invalidate_proxmox_client()


def test_listing_and_node_map_share_one_fetch_per_node(storage: _Storage) -> None:
    templates = provisioning_service.get_lxc_templates()

    assert sorted(storage.calls) == ["pve1", "pve2"]
    assert {t.volid: t.nodes for t in templates} == {
        DEBIAN["volid"]: ["pve1", "pve2"],
        UBUNTU["volid"]: ["pve2"],
    }

    for _ in range(5):
        provisioning_service.get_lxc_templates()
        ops.get_lxc_template_node_map()
    assert len(storage.calls) == 2


def test_unreachable_node_keeps_previous_templates(
    storage: _Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops.get_lxc_template_node_map()
    monkeypatch.setattr(ops, "_LXC_TEMPLATE_CACHE_TTL_SECONDS", 0.0)
    storage.down.add("pve2")

    node_map = ops.get_lxc_template_node_map()

    assert node_map[UBUNTU["volid"]] == {"pve2"}
    assert node_map[DEBIAN["volid"]] == {"pve1", "pve2"}


def test_never_listed_node_has_no_templates(storage: _Storage) -> None:
    storage.down.add("pve2")

    node_map = ops.get_lxc_template_node_map()

    assert node_map == {DEBIAN["volid"]: {"pve1"}}


def test_connection_settings_change_refetches(storage: _Storage) -> None:
    ops.get_lxc_template_node_map()
    storage.items["pve1"] = [UBUNTU]

    proxmox_client.invalidate_proxmox_client()
    node_map = ops.get_lxc_template_node_map()

    assert len(storage.calls) == 4
    assert node_map[UBUNTU["volid"]] == {"pve1", "pve2"}
    assert DEBIAN["volid"] in node_map  # pve2 仍有 debian


def test_settings_change_drops_previous_templates_of_unreachable_node(
    storage: _Storage,
) -> None:
    ops.get_lxc_template_node_map()
    storage.down.add("pve2")

    # 設定換了（例如 iso_storage）之後，舊設定下的結果不能再沿用
    proxmox_client.invalidate_proxmox_client()
    node_map = ops.get_lxc_template_node_map()

    assert node_map == {DEBIAN["volid"]: {"pve1"}}


def test_callers_cannot_mutate_cache(storage: _Storage) -> None:
    ops.list_lxc_templates()[0]["volid"] = "tampered"
    ops.get_lxc_template_node_map()[DEBIAN["volid"]].add("pve9")

    assert "tampered" not in {t["volid"] for t in ops.list_lxc_templates()}
    assert ops.get_lxc_template_node_map()[DEBIAN["volid"]] == {"pve1", "pve2"}
