"""deletion_service.request_deletion 與 credentials_service.get_ssh_key。

DELETE /resources/{vmid} 與 GET /resources/{vmid}/ssh-key 的業務邏輯搬到
service 之後，孤兒清理必須仍然逐一詢問所有 PVE 連線才動 DB，且 ssh-key 對
DB 沒有紀錄的機器維持回 ProxmoxError。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError, NotFoundError, ProxmoxError
from app.models import DeletionRequestStatus
from app.services.resource import credentials_service, deletion_service


class _FakePve:
    def __init__(self, vms: list[dict[str, Any]] | Exception) -> None:
        self._vms = vms
        self.cluster = SimpleNamespace(resources=SimpleNamespace(get=self._get))

    def _get(self, **_kwargs: Any) -> list[dict[str, Any]]:
        if isinstance(self._vms, Exception):
            raise self._vms
        return self._vms


_USER = SimpleNamespace(id=uuid.uuid4(), email="student@example.com")


@pytest.fixture()
def orphan_state(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"orphan_calls": [], "clients": {}, "management": []}
    monkeypatch.setattr(
        deletion_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kw: SimpleNamespace(vmid=205, user_id=_USER.id),
    )
    monkeypatch.setattr(
        deletion_service, "can_bypass_resource_ownership", lambda _u: False
    )
    monkeypatch.setattr(
        deletion_service,
        "require_resource_management",
        lambda **kw: state["management"].append(kw["vmid"]),
    )

    def _not_found(vmid: int) -> dict[str, Any]:
        raise NotFoundError(f"Resource {vmid} not found")

    monkeypatch.setattr(deletion_service.proxmox_service, "find_resource", _not_found)
    monkeypatch.setattr(
        deletion_service.proxmox_connection_repo,
        "get_all_connections",
        lambda _session: [
            SimpleNamespace(id=1, enabled=True),
            SimpleNamespace(id=2, enabled=False),
        ],
    )
    # find_vmid_on_connections → list_connection_vms → get_proxmox_api
    monkeypatch.setattr(
        deletion_service.proxmox_service,
        "get_proxmox_api",
        lambda cid: _FakePve(state["clients"][cid]),
    )
    monkeypatch.setattr(
        deletion_service.resource_service,
        "delete_orphan_db_record",
        lambda **kw: state["orphan_calls"].append(kw),
    )
    return state


def _request() -> Any:
    return deletion_service.request_deletion(
        session=object(), user=_USER, vmid=205  # type: ignore[arg-type]
    )


def test_unreachable_connection_keeps_the_db_record(
    orphan_state: dict[str, Any],
) -> None:
    orphan_state["clients"] = {1: [], 2: RuntimeError("connection refused")}

    with pytest.raises(ProxmoxError):
        _request()

    assert orphan_state["management"] == [205]
    assert orphan_state["orphan_calls"] == []


def test_vm_on_disabled_connection_is_a_conflict(
    orphan_state: dict[str, Any],
) -> None:
    orphan_state["clients"] = {1: [], 2: [{"vmid": 205, "node": "n2"}]}

    with pytest.raises(ConflictError):
        _request()

    assert orphan_state["orphan_calls"] == []


def test_orphan_is_cleaned_when_every_connection_confirms_absence(
    orphan_state: dict[str, Any],
) -> None:
    orphan_state["clients"] = {1: [{"vmid": 101}], 2: []}

    result = _request()

    assert result.status == DeletionRequestStatus.completed
    assert len(orphan_state["orphan_calls"]) == 1


def test_non_admin_without_db_record_gets_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deletion_service.resource_repo, "get_resource_by_vmid", lambda **_kw: None
    )
    monkeypatch.setattr(
        deletion_service, "can_bypass_resource_ownership", lambda _u: False
    )

    with pytest.raises(NotFoundError):
        _request()


def test_ssh_key_for_unknown_resource_stays_a_proxmox_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        credentials_service.resource_repo, "get_resource_by_vmid", lambda **_kw: None
    )

    with pytest.raises(ProxmoxError):
        credentials_service.get_ssh_key(session=object(), vmid=205)  # type: ignore[arg-type]
