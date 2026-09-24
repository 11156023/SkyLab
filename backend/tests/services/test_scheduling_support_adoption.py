"""接手既有機器（adopt）的比對條件測試（mock DB / PVE）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.scheduling import support as scheduling_support


class _FakeSession:
    """只要能回「已被其他申請單認領的 vmid 清單」即可。"""

    def __init__(self, claimed: list[int] | None = None) -> None:
        self.claimed = claimed or []

    def exec(self, statement: Any) -> Any:
        rows = [SimpleNamespace(vmid=vmid) for vmid in self.claimed]
        return SimpleNamespace(all=lambda: rows)


def _request(**overrides: Any) -> SimpleNamespace:
    values: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "hostname": "lab-01",
        "resource_type": "vm",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    resources: list[dict] = [
        {"vmid": 301, "type": "qemu", "name": "lab-01", "node": "pve1"},
    ]
    monkeypatch.setattr(
        scheduling_support.proxmox_service,
        "list_all_resources",
        lambda: resources,
    )
    return resources


def _patch_owner(
    monkeypatch: pytest.MonkeyPatch, owner_id: uuid.UUID | None
) -> None:
    monkeypatch.setattr(
        scheduling_support.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: (
            None if owner_id is None else SimpleNamespace(user_id=owner_id)
        ),
    )


def test_adopts_unowned_machine_with_same_name(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    _patch_owner(monkeypatch, None)
    found = scheduling_support.find_existing_resource_for_request(
        session=_FakeSession(), request=_request()
    )
    assert found is not None and found["vmid"] == 301


def test_adopts_machine_already_owned_by_same_user(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    request = _request()
    _patch_owner(monkeypatch, request.user_id)
    found = scheduling_support.find_existing_resource_for_request(
        session=_FakeSession(), request=request
    )
    assert found is not None and found["vmid"] == 301


def test_refuses_machine_owned_by_someone_else(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    """同名機器已經有主人時絕不認領 —— 否則等於把別人的機器連同資料換主。"""
    _patch_owner(monkeypatch, uuid.uuid4())
    assert (
        scheduling_support.find_existing_resource_for_request(
            session=_FakeSession(), request=_request()
        )
        is None
    )


def test_hostname_must_match_exactly(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    _patch_owner(monkeypatch, None)
    assert (
        scheduling_support.find_existing_resource_for_request(
            session=_FakeSession(), request=_request(hostname="lab-01-old")
        )
        is None
    )


def test_resource_type_must_match(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    _patch_owner(monkeypatch, None)
    assert (
        scheduling_support.find_existing_resource_for_request(
            session=_FakeSession(), request=_request(resource_type="lxc")
        )
        is None
    )


def test_blank_hostname_never_adopts(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    _patch_owner(monkeypatch, None)
    assert (
        scheduling_support.find_existing_resource_for_request(
            session=_FakeSession(), request=_request(hostname="")
        )
        is None
    )


def test_machine_claimed_by_another_request_is_skipped(
    monkeypatch: pytest.MonkeyPatch, pve: list[dict]
) -> None:
    _patch_owner(monkeypatch, None)
    assert (
        scheduling_support.find_existing_resource_for_request(
            session=_FakeSession(claimed=[301]), request=_request()
        )
        is None
    )
