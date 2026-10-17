"""app/api/routes/resource_settings.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import resource_settings as rs

SESSION = object()
USER = SimpleNamespace(id=uuid.uuid4())
RESOURCE = {"node": "pve1", "type": "qemu"}
VMID = 101


class _Recorder:
    """把任何 service 函式呼叫記下來並回傳 (名稱, kwargs)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        def _fn(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return (name, kwargs)

        return _fn


@pytest.fixture()
def services(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Recorder]:
    recorders = {
        "settings_service": _Recorder(),
        "credentials_service": _Recorder(),
        "sharing_service": _Recorder(),
    }
    for name, recorder in recorders.items():
        monkeypatch.setattr(rs, name, recorder)
    return recorders


@pytest.fixture()
def managed(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    checked: list[int] = []
    monkeypatch.setattr(
        rs, "require_resource_management", lambda **kw: checked.append(kw["vmid"])
    )
    return checked


def test_read_only_settings_routes(services: dict[str, _Recorder]) -> None:
    name, kwargs = rs.get_specs(vmid=VMID, resource_info=RESOURCE)
    assert (name, kwargs) == ("get_specs", {"vmid": VMID, "resource_info": RESOURCE})
    name, _ = rs.get_boot_options(vmid=VMID, resource_info=RESOURCE)
    assert name == "get_boot_options"
    name, kwargs = rs.list_iso_images(vmid=VMID, resource_info=RESOURCE)
    assert (name, kwargs) == ("list_iso_images", {"resource_info": RESOURCE})


def test_update_boot_options(
    services: dict[str, _Recorder], managed: list[int]
) -> None:
    body = object()
    name, kwargs = rs.update_boot_options(
        vmid=VMID, body=body, resource_info=RESOURCE, session=SESSION, current_user=USER
    )
    assert name == "update_boot_options"
    assert kwargs["data"] is body
    assert kwargs["user_id"] == USER.id
    assert managed == [VMID]


def test_credentials_routes(services: dict[str, _Recorder], managed: list[int]) -> None:
    common = {
        "vmid": VMID,
        "resource_info": RESOURCE,
        "session": SESSION,
        "current_user": USER,
    }
    assert rs.get_credentials(**common)[0] == "get_credentials"

    name, kwargs = rs.reset_password(
        body=SimpleNamespace(password="Secret123!"), **common
    )
    assert name == "reset_password"
    assert kwargs["password"] == "Secret123!"

    name, kwargs = rs.regenerate_ssh_key(**common)
    assert name == "regenerate_ssh_key"
    assert kwargs["user_id"] == USER.id

    key = SimpleNamespace(public_key="ssh-ed25519 AAAA test")
    name, kwargs = rs.add_authorized_key(body=key, **common)
    assert (name, kwargs["public_key"]) == ("add_authorized_key", key.public_key)
    name, kwargs = rs.remove_authorized_key(body=key, **common)
    assert (name, kwargs["public_key"]) == ("remove_authorized_key", key.public_key)
    assert managed == [VMID] * 5


def test_sharing_routes(services: dict[str, _Recorder], managed: list[int]) -> None:
    common = {
        "vmid": VMID,
        "_resource_info": RESOURCE,
        "session": SESSION,
        "current_user": USER,
    }
    assert rs.list_shares(**common) == (
        "list_shares",
        {"session": SESSION, "vmid": VMID},
    )

    name, kwargs = rs.add_share(body=SimpleNamespace(email="a@example.com"), **common)
    assert name == "add_share"
    assert kwargs["actor"] is USER
    assert kwargs["email"] == "a@example.com"

    share_id = uuid.uuid4()
    result = rs.remove_share(share_id=share_id, **common)
    assert result.message == "Share removed"
    assert services["sharing_service"].calls[-1] == (
        "remove_share",
        {"session": SESSION, "vmid": VMID, "share_id": share_id, "actor": USER},
    )

    name, kwargs = rs.transfer_ownership(
        body=SimpleNamespace(email="b@example.com", keep_access=True), **common
    )
    assert name == "transfer_ownership"
    assert kwargs["keep_access"] is True
    assert kwargs["resource_info"] is RESOURCE
    assert managed == [VMID] * 4
