"""寫入 cloud-init ciuser 前的 service 層再檢查：違規帳號在呼叫 PVE 前就擋下。

API schema 已驗過，這裡測的是繞過 schema 的路徑（DB 裡的舊申請單、
model_construct 的內部請求）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.exceptions import UsernamePolicyError
from app.schemas import VMCreateRequest
from app.services.proxmox import provisioning_service


@pytest.fixture
def pve(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """把 provisioning 用到的 PVE 介面整個換掉，任何呼叫都會被記下。"""
    mock = MagicMock()
    monkeypatch.setattr(provisioning_service, "proxmox_service", mock)
    monkeypatch.setattr(
        provisioning_service, "get_proxmox_settings_for_node", mock.settings_for_node
    )
    monkeypatch.setattr(provisioning_service, "allocate_free_vmid", mock.allocate)
    return mock


def test_create_vm_rejects_reserved_username_before_pve(pve: MagicMock) -> None:
    vm_data = VMCreateRequest.model_construct(
        hostname="lab-vm",
        template_id=9000,
        username="admin",
        password="Secret123!",
        cores=2,
        memory=2048,
        disk_size=20,
        storage="local-lvm",
        environment_type="test",
        start=True,
    )
    with pytest.raises(UsernamePolicyError) as exc:
        provisioning_service.create_vm(
            session=MagicMock(), vm_data=vm_data, user_id=MagicMock()
        )
    assert exc.value.status_code == 422
    assert exc.value.codes == ["LNX_RESERVED"]
    assert pve.mock_calls == []


def test_plan_provision_rejects_legacy_request_before_allocating(
    pve: MagicMock,
) -> None:
    legacy = SimpleNamespace(id="req-1", resource_type="vm", username="Admin")
    with pytest.raises(UsernamePolicyError) as exc:
        provisioning_service.plan_provision(session=MagicMock(), db_request=legacy)
    assert exc.value.codes == ["LNX_FORMAT"]
    assert pve.mock_calls == []


def test_execute_provision_rejects_before_clone(pve: MagicMock) -> None:
    plan = {
        "vmid": 901,
        "target_node": "pve1",
        "resource_type": "vm",
        "hostname": "lab-vm",
        "username": "systemd-journal",
    }
    with pytest.raises(UsernamePolicyError) as exc:
        provisioning_service.execute_provision(plan)
    assert exc.value.codes == ["LNX_RESERVED", "LNX_RESERVED_PREFIX"]
    assert pve.mock_calls == []


def test_ensure_ciuser_allowed_passes_valid_and_warning_names() -> None:
    provisioning_service.ensure_ciuser_allowed("student")
    provisioning_service.ensure_ciuser_allowed("ubuntu")  # warning 不擋
    # Windows 範本不帶帳號
    provisioning_service.ensure_ciuser_allowed(None)
    provisioning_service.ensure_ciuser_allowed("")


def test_lxc_plan_is_not_checked(pve: MagicMock) -> None:
    """LXC 沒有 cloud-init 帳號，不套命名政策（會往下走到 placement）。"""
    lxc = SimpleNamespace(id="req-2", resource_type="lxc", username="admin")
    pve.allocate.side_effect = RuntimeError("reached allocation")
    with pytest.raises(RuntimeError, match="reached allocation"):
        provisioning_service.plan_provision(session=MagicMock(), db_request=lxc)
