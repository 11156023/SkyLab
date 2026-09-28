"""嚴格的機器查詢：有連線列不出清單時不能把「找不到」當成「機器已刪除」。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import operations


def _vm(vmid: object, node: str, pool: str = "skylab") -> dict:
    return {"vmid": vmid, "node": node, "type": "qemu", "pool": pool}


def _api(vms: list[dict] | Exception) -> SimpleNamespace:
    def _get(type: str) -> list[dict]:  # noqa: A002 - mirrors proxmoxer kwarg
        assert type == "vm"
        if isinstance(vms, Exception):
            raise vms
        return vms

    return SimpleNamespace(cluster=SimpleNamespace(resources=SimpleNamespace(get=_get)))


@pytest.fixture
def pool_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        operations,
        "get_proxmox_settings",
        lambda _key: SimpleNamespace(pool_name="skylab"),
    )


def _connections(
    monkeypatch: pytest.MonkeyPatch, clients: dict[int | None, list[dict] | Exception]
) -> None:
    monkeypatch.setattr(operations, "_connection_keys", lambda: list(clients))
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: _api(clients[key]))


# --- list_connection_vms / find_vmid_on_connections --------------------------


def test_list_connection_vms_returns_raw_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vms = [_vm(100, "a", pool="other")]
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: _api(vms))
    assert operations.list_connection_vms(3) == vms


def test_find_vmid_on_connections_ignores_pool_and_skips_bad_vmids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = {
        1: [_vm(None, "a"), _vm("oops", "a"), _vm(101, "a")],
        2: [_vm("200", "b", pool="elsewhere")],
    }
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: _api(clients[key]))

    hit = operations.find_vmid_on_connections(200, [1, 2])
    assert hit is not None
    assert hit[0] == 2
    assert hit[1]["node"] == "b"
    assert operations.find_vmid_on_connections(999, [1, 2]) is None


def test_find_vmid_on_connections_raises_when_any_connection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: dict[int, list[dict] | Exception] = {
        1: [_vm(101, "a")],
        2: RuntimeError("connection refused"),
    }
    monkeypatch.setattr(operations, "get_proxmox_api", lambda key: _api(clients[key]))

    with pytest.raises(ProxmoxError) as exc_info:
        operations.find_vmid_on_connections(999, [1, 2])
    assert "2" in exc_info.value.message


# --- find_resource(strict=True) ----------------------------------------------


def test_find_resource_strict_finds_vm_in_own_pool(
    monkeypatch: pytest.MonkeyPatch, pool_settings: None
) -> None:
    _connections(
        monkeypatch,
        {1: [_vm(480, "a")], 2: [_vm(481, "b", pool="other")]},
    )
    assert operations.find_resource(480, strict=True)["node"] == "a"
    # 在 pool 外的機器不算數：每個連線都列得到，所以是 NotFound。
    with pytest.raises(NotFoundError):
        operations.find_resource(481, strict=True)


def test_find_resource_strict_refuses_notfound_when_a_connection_is_down(
    monkeypatch: pytest.MonkeyPatch, pool_settings: None
) -> None:
    _connections(
        monkeypatch,
        {1: [_vm(480, "a")], 2: RuntimeError("timeout")},
    )
    # 找得到就照常回傳
    assert operations.find_resource(480, strict=True)["node"] == "a"
    # 找不到但連線 2 列不出來：不能判定為已刪除
    with pytest.raises(operations.ProxmoxConnectionUnavailableError) as exc_info:
        operations.find_resource(999, strict=True)
    assert "[2]" in exc_info.value.message
    # 非嚴格模式維持原本行為
    with pytest.raises(NotFoundError):
        operations.find_resource(999)


def test_find_resource_strict_all_connections_down(
    monkeypatch: pytest.MonkeyPatch, pool_settings: None
) -> None:
    _connections(monkeypatch, {1: RuntimeError("down"), 2: RuntimeError("down")})
    with pytest.raises(operations.ProxmoxConnectionUnavailableError):
        operations.find_resource(480, strict=True)


def test_find_resource_strict_refuses_duplicate_vmid(
    monkeypatch: pytest.MonkeyPatch, pool_settings: None
) -> None:
    _connections(monkeypatch, {1: [_vm(480, "a")], 2: [_vm(480, "b")]})
    with pytest.raises(ProxmoxError) as exc_info:
        operations.find_resource(480, strict=True)
    # VMID 重複不是連線問題：排程器不能把它當成「略過這一輪」
    assert not isinstance(
        exc_info.value, operations.ProxmoxConnectionUnavailableError
    )


def test_connection_unavailable_error_is_a_proxmox_error() -> None:
    assert issubclass(operations.ProxmoxConnectionUnavailableError, ProxmoxError)
