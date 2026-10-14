"""VMID 在多連線下的識別：找機器不能猜、配發不能撞到 DB 已登記的 VMID。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import operations


def _vm(vmid: int, node: str, kind: str = "qemu") -> dict:
    return {"vmid": vmid, "node": node, "type": kind}


def test_find_resource_returns_single_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        operations, "_pool_vms", lambda **_: [_vm(101, "pve-a"), _vm(102, "pve-b")]
    )
    assert operations.find_resource(102)["node"] == "pve-b"


def test_find_resource_refuses_vmid_on_multiple_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations, "_pool_vms", lambda **_: [_vm(101, "pve-a"), _vm(101, "pve-b")]
    )
    with pytest.raises(ProxmoxError):
        operations.find_resource(101)


def test_find_lxc_ignores_same_vmid_of_other_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        operations,
        "_pool_vms",
        lambda **_: [_vm(101, "pve-a", "qemu"), _vm(101, "pve-b", "lxc")],
    )
    assert operations.find_lxc(101)["node"] == "pve-b"
    with pytest.raises(NotFoundError):
        operations.find_lxc(999)


def _fake_api(nextid: int) -> SimpleNamespace:
    return SimpleNamespace(
        cluster=SimpleNamespace(nextid=SimpleNamespace(get=lambda: str(nextid)))
    )


def test_next_vmid_skips_db_claimed_single_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(operations, "_connection_keys", lambda: [None])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda _key: _fake_api(200))
    monkeypatch.setattr(operations, "_raw_vms", lambda **_: [])
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {200, 201})
    assert operations.next_vmid() == 202


def test_next_vmid_single_connection_does_not_step_onto_pve_vmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """nextid 回空號 102（DB 還殘留 102 的資源列），往上遞增不可踩到 PVE 的 103。"""
    monkeypatch.setattr(operations, "_connection_keys", lambda: [None])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda _key: _fake_api(102))
    monkeypatch.setattr(
        operations, "_raw_vms", lambda **_: [_vm(100, "a"), _vm(101, "a"), _vm(103, "a")]
    )
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {102})
    assert operations.next_vmid() == 104


def test_next_vmid_skips_pve_and_db_claimed_multi_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apis = {1: _fake_api(300), 2: _fake_api(305)}
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1, 2])
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: apis[key])
    monkeypatch.setattr(operations, "_raw_vms", lambda **_: [_vm(305, "pve-a")])
    monkeypatch.setattr(operations, "_db_claimed_vmids", lambda: {306})
    assert operations.next_vmid() == 307


# ── 某個連線連不上時：照常配號，但避開它上次列到的 VMID 與範本 VMID ─────────


class _Unreachable:
    @property
    def cluster(self):  # noqa: D401 - 模擬 proxmoxer 的屬性路徑
        raise ConnectionError("connection refused")


def _listing_api(nextid: int, vms: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        cluster=SimpleNamespace(
            nextid=SimpleNamespace(get=lambda: str(nextid)),
            resources=SimpleNamespace(get=lambda **_: [dict(vm) for vm in vms]),
        )
    )


@pytest.fixture
def two_clusters(monkeypatch: pytest.MonkeyPatch) -> dict:
    """連線 1 一直正常；連線 2 先列得到 410、411、412（412 是範本），之後斷線。"""
    state: dict = {
        2: _listing_api(400, [_vm(410, "pve-b"), _vm(411, "pve-b"), _vm(412, "pve-b")]),
        "templates": set(),
    }
    monkeypatch.setattr(operations, "_connection_keys", lambda: [1, 2])
    monkeypatch.setattr(
        operations,
        "get_proxmox_api",
        lambda key: _listing_api(410, [_vm(400, "pve-a")]) if key == 1 else state[2],
    )
    monkeypatch.setattr(operations, "_db_claimed_vmids", set)
    monkeypatch.setattr(operations, "_db_template_vmids", lambda: set(state["templates"]))
    monkeypatch.setattr(operations._LastSeenVmids, "by_connection", {})
    operations.invalidate_cluster_resources_cache()
    yield state
    operations.invalidate_cluster_resources_cache()


def test_next_vmid_avoids_last_seen_vmids_of_unreachable_connection(
    two_clusters: dict,
) -> None:
    # 兩個連線都在線：連線 2 的 410～412 直接從即時清單避開
    assert operations.next_vmid() == 413

    two_clusters[2] = _Unreachable()
    # 建機後會清掉清單快取；上次列到的 VMID 不能跟著被清掉
    operations.invalidate_cluster_resources_cache()

    # 連線 2 斷線後仍照常配號，但不能配到它上次列到的 410～412
    assert operations.next_vmid() == 413


def test_next_vmid_avoids_template_vmids_when_connection_never_listed(
    two_clusters: dict,
) -> None:
    """程序剛啟動、還沒列到過這個連線就斷線：至少要避開 DB 登記的範本 VMID。"""
    two_clusters[2] = _Unreachable()
    two_clusters["templates"] = {410, 411}

    assert operations.next_vmid() == 412


def test_next_vmid_reachable_connections_do_not_consult_templates(
    two_clusters: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_query() -> set[int]:
        raise AssertionError("templates only matter while a connection is unreachable")

    monkeypatch.setattr(operations, "_db_template_vmids", must_not_query)

    assert operations.next_vmid() == 413


def test_next_vmid_still_refuses_when_every_connection_is_unreachable(
    two_clusters: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: _Unreachable())

    with pytest.raises(ProxmoxError):
        operations.next_vmid()
