"""PVE 設定與 /cluster/resources 的行程內快取。

逐台迴圈（清單頁、拓撲、排程 tick）原本每次 find_resource 都重抓整份叢集清單，
還要為每個連線開 DB session、解一次密碼。快取之後不能犧牲的是：
剛建立的機器要找得到、「找不到」要以最新清單判斷、呼叫端改條目不汙染快取。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.exceptions import NotFoundError
from app.infrastructure.proxmox import operations as ops
from app.infrastructure.proxmox import settings as pve_settings


class _Listing:
    """假的 /cluster/resources：記錄被抓了幾次，內容可在測試中途改變。"""

    def __init__(self, vms: list[dict[str, Any]]) -> None:
        self.vms = vms
        self.calls = 0

    def __call__(self, fetch: Any, *, what: str) -> list[tuple[int | None, list[dict]]]:
        self.calls += 1
        return [(1, [dict(vm) for vm in self.vms])]


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> _Listing:
    fake = _Listing([{"vmid": 100, "node": "pve1", "type": "qemu", "pool": "SkyLab"}])
    monkeypatch.setattr(ops, "_gather_per_connection", fake)
    monkeypatch.setattr(ops, "_connection_keys", lambda: [1])
    monkeypatch.setattr(
        ops, "get_proxmox_settings", lambda key=None: _settings_stub()
    )
    return fake


def _settings_stub() -> Any:
    return pve_settings.ProxmoxSettings(
        host="h",
        user="u",
        password="p",
        verify_ssl=False,
        iso_storage="local",
        data_storage="local-lvm",
        api_timeout=30,
        task_check_interval=2,
        pool_name="SkyLab",
    )


def test_repeated_lookups_share_one_cluster_listing(listing: _Listing) -> None:
    for _ in range(50):
        assert ops.find_resource(100)["node"] == "pve1"
    assert listing.calls == 1


def test_newly_created_vm_is_found_despite_cache(listing: _Listing) -> None:
    ops.find_resource(100)
    listing.vms.append({"vmid": 101, "node": "pve2", "type": "lxc", "pool": "SkyLab"})

    assert ops.find_resource(101)["node"] == "pve2"
    assert ops.find_lxc(101)["node"] == "pve2"
    assert listing.calls == 2


def test_not_found_is_decided_on_a_fresh_listing(listing: _Listing) -> None:
    ops.find_resource(100)
    with pytest.raises(NotFoundError):
        ops.find_resource(999, strict=True)
    assert listing.calls == 2


def test_callers_cannot_mutate_the_cached_entries(listing: _Listing) -> None:
    ops.find_resource(100)["node"] = "tampered"
    ops.list_all_resources()[0]["status"] = "tampered"

    entry = ops.find_resource(100)
    assert entry["node"] == "pve1"
    assert "status" not in entry


def test_cache_expires_after_ttl(
    listing: _Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops.list_all_resources()
    monkeypatch.setattr(ops, "_CLUSTER_RESOURCES_TTL_SECONDS", 0.0)
    ops.list_all_resources()
    assert listing.calls == 2


def test_clone_invalidates_the_listing(
    listing: _Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Clone:
        def post(self, **_: Any) -> str:
            return "UPID:x"

    class _Api:
        def nodes(self, _node: str) -> Any:
            return self

        def qemu(self, _vmid: int) -> Any:
            return self

        clone = _Clone()

    monkeypatch.setattr(ops, "get_proxmox_api_for_node", lambda node: _Api())
    monkeypatch.setattr(ops, "basic_blocking_task_status", lambda node, task: None)

    ops.list_all_resources()
    ops.clone_vm("pve1", 9000, newid=102)
    ops.list_all_resources()
    assert listing.calls == 2


def test_next_vmid_always_reads_a_fresh_listing(
    listing: _Listing, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Api:
        class cluster:  # noqa: N801 - 模擬 proxmoxer 的屬性路徑
            class nextid:  # noqa: N801
                @staticmethod
                def get() -> int:
                    return 100

    monkeypatch.setattr(ops, "get_proxmox_api", lambda key=None: _Api())
    monkeypatch.setattr(ops, "_db_claimed_vmids", lambda: set())

    ops.list_all_resources()
    listing.vms.append({"vmid": 101, "node": "pve1", "type": "qemu", "pool": "SkyLab"})

    assert ops.next_vmid() == 102
    assert listing.calls == 2


# ─── 設定快取 ────────────────────────────────────────────────────────────────


def test_settings_are_loaded_once_and_returned_as_copies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int | None] = []

    def fake_load(connection_id: int | None) -> pve_settings.ProxmoxSettings:
        loads.append(connection_id)
        return _settings_stub()

    monkeypatch.setattr(pve_settings, "_load_proxmox_settings", fake_load)

    first = pve_settings.get_proxmox_settings(1)
    first.pool_name = "tampered"
    second = pve_settings.get_proxmox_settings(1)

    assert loads == [1]
    assert second.pool_name == "SkyLab"


def test_invalidate_proxmox_client_clears_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.proxmox.client import invalidate_proxmox_client

    loads: list[int | None] = []

    def fake_load(connection_id: int | None) -> pve_settings.ProxmoxSettings:
        loads.append(connection_id)
        return _settings_stub()

    monkeypatch.setattr(pve_settings, "_load_proxmox_settings", fake_load)

    pve_settings.get_proxmox_settings(1)
    invalidate_proxmox_client()
    pve_settings.get_proxmox_settings(1)

    assert loads == [1, 1]
