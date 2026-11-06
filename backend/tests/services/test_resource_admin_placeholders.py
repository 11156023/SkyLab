"""管理員資源清單（GET /resources）要列出「已核准、機器還沒建出來」的申請。

回歸背景：佔位列（已排程／開通中／建立失敗）只有「我的資源」會列，管理員核准申請後
在資源管理頁看不到它，要等機器出現在 PVE 才冒出來。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resource_markers import RESOURCE_DELETED_BY_USER_MARKER
from app.models import Resource, VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.resource import resource_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _EntitySession:
    """依查詢的主體 model 回傳對應的列（不看 where 條件）；其餘查詢一律空。"""

    def __init__(self, rows: dict[type, list[Any]]) -> None:
        self.rows = rows

    def exec(self, stmt: Any) -> _Result:
        descriptions = getattr(stmt, "column_descriptions", None) or []
        entity = descriptions[0].get("entity") if descriptions else None
        return _Result(list(self.rows.get(entity, [])))

    def get(self, model: Any, key: Any) -> Any:
        return None

    def rollback(self) -> None:
        pass


def _request(**overrides: object) -> VMRequest:
    values: dict[str, object] = dict(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        reason="course lab",
        resource_type="vm",
        hostname="lab-vm",
        password="pw",
        cores=2,
        memory=2048,
        status=VMRequestStatus.approved,
        start_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        provisioning_status=VMProvisioningStatus.running,
        assigned_node="pve1",
    )
    values.update(overrides)
    return VMRequest(**values)


def _vm(vmid: int) -> dict[str, Any]:
    return {"vmid": vmid, "type": "qemu", "node": "pve1", "name": f"vm{vmid}", "status": "running"}


@pytest.fixture
def pve(monkeypatch: pytest.MonkeyPatch):
    """PVE 與清單用到的批次查詢全部換成假的；回傳設定 PVE 清單與使用者名稱的函式。"""

    def install(machines: list[dict[str, Any]], names: dict[uuid.UUID, str] | None = None) -> None:
        monkeypatch.setattr(
            resource_service.proxmox_service, "list_all_resources", lambda: machines
        )
        monkeypatch.setattr(
            resource_service.proxmox_service, "platform_template_vmids", lambda: set()
        )
        monkeypatch.setattr(
            resource_service.proxmox_service, "list_booting_vmids", lambda nodes: set()
        )
        monkeypatch.setattr(
            resource_service.proxmox_service,
            "get_ip_address",
            lambda node, vmid, vm_type: None,
        )
        monkeypatch.setattr(
            resource_service.resource_repo,
            "get_resources_by_vmids",
            lambda *, session, vmids: {},
        )
        monkeypatch.setattr(
            resource_service.resource_repo,
            "sync_ip_cache_many",
            lambda *, session, live_ips: dict(live_ips),
        )
        monkeypatch.setattr(
            resource_service.vm_request_repo,
            "get_latest_approved_vm_requests_by_vmids",
            lambda *, session, vmids, skip_closed=False: {},
        )
        monkeypatch.setattr(
            resource_service.rp_repo, "list_rules_by_vmids", lambda session, vmids: []
        )
        monkeypatch.setattr(
            resource_service.resource_kind, "practice_request_ids", lambda session: set()
        )
        monkeypatch.setattr(
            resource_service.resource_kind,
            "user_display_names",
            lambda session, ids: {uid: (names or {}).get(uid) for uid in ids},
        )

    return install


def test_approved_request_without_machine_is_listed_first(pve) -> None:
    owner = uuid.uuid4()
    pve([_vm(100)], names={owner: "王小明"})
    req = _request(user_id=owner, hostname="web-01", vmid=None)
    session = _EntitySession({VMRequest: [req]})

    result = resource_service.list_all(session=session)  # type: ignore[arg-type]

    assert [item.vmid for item in result] == [None, 100]
    placeholder = result[0]
    assert placeholder.is_placeholder is True
    assert placeholder.status == "provisioning"
    assert placeholder.request_id == req.id
    assert placeholder.name == "web-01"
    assert placeholder.node == "pve1"
    assert placeholder.type == "qemu"
    assert placeholder.can_control is False
    assert placeholder.owner_name == "王小明"
    assert placeholder.machine_kind == "personal"


def test_placeholder_kind_comes_from_the_request(pve) -> None:
    pve([])
    session = _EntitySession(
        {
            VMRequest: [
                _request(hostname="practice", request_kind="quick_template"),
                _request(hostname="lab", request_kind="course"),
            ]
        }
    )

    result = resource_service.list_all(session=session)  # type: ignore[arg-type]

    assert {item.name: item.machine_kind for item in result} == {
        "practice": "quick_practice",
        "lab": "course",
    }


def test_placeholder_status_follows_the_request(pve) -> None:
    pve([])
    scheduled = _request(
        hostname="later", start_at=datetime.now(timezone.utc) + timedelta(days=1),
        provisioning_status=VMProvisioningStatus.idle,
    )
    failed = _request(
        hostname="broken", provisioning_status=VMProvisioningStatus.failed,
        provisioning_error="clone failed",
    )
    session = _EntitySession({VMRequest: [scheduled, failed]})

    result = resource_service.list_all(session=session)  # type: ignore[arg-type]

    assert {item.name: item.status for item in result} == {
        "later": "scheduled",
        "broken": "failed",
    }


def test_machine_already_in_pve_has_no_placeholder(pve) -> None:
    pve([_vm(500)])
    session = _EntitySession({VMRequest: [_request(vmid=500)]})

    result = resource_service.list_all(session=session)  # type: ignore[arg-type]

    assert [(item.vmid, item.is_placeholder) for item in result] == [(500, False)]


def test_built_machine_missing_from_pve_is_not_marked_provisioning(pve) -> None:
    # Resource 列已存在＝機器建過了；PVE 沒列出來（節點離線）不能變成「開通中」
    pve([])
    session = _EntitySession(
        {
            VMRequest: [_request(vmid=501, provisioning_status=VMProvisioningStatus.completed)],
            Resource: [SimpleNamespace(vmid=501, user_id=uuid.uuid4())],
        }
    )

    assert resource_service.list_all(session=session) == []  # type: ignore[arg-type]


def test_request_whose_machine_was_deleted_stays_hidden(pve) -> None:
    pve([])
    session = _EntitySession(
        {VMRequest: [_request(vmid=None, resource_warning=RESOURCE_DELETED_BY_USER_MARKER)]}
    )

    assert resource_service.list_all(session=session) == []  # type: ignore[arg-type]


def test_node_filter_applies_to_placeholders(pve) -> None:
    pve([])
    session = _EntitySession(
        {
            VMRequest: [
                _request(hostname="on-pve1", assigned_node="pve1"),
                _request(hostname="on-pve2", assigned_node="pve2"),
            ]
        }
    )

    result = resource_service.list_all(session=session, node="pve2")  # type: ignore[arg-type]

    assert [item.name for item in result] == ["on-pve2"]


def test_own_request_carries_no_owner_name(pve) -> None:
    viewer = uuid.uuid4()
    pve([], names={viewer: "管理員"})
    session = _EntitySession({VMRequest: [_request(user_id=viewer)]})

    result = resource_service.list_all(session=session, viewer_id=viewer)  # type: ignore[arg-type]

    assert [item.owner_name for item in result] == [None]
