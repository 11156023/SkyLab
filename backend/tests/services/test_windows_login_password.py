"""Windows 機器登入密碼的複雜度（2026-10-08）。

Windows 預設啟用密碼複雜度，cloudbase-init 寫入不合規的密碼會被系統拒絕，
機器開得起來卻登不進去。所以：

- 系統代發的密碼產生時就保證大寫、小寫、數字三類都有
- 要寫進 Windows 的自訂密碼（申請、克隆、重設、直接建機）在入口就擋下
- Linux 機器的自訂密碼不受影響
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.exceptions import BadRequestError
from app.schemas import VMCreateRequest
from app.services.proxmox import provisioning_service
from app.services.resource import credentials_service
from app.services.template import password_policy
from app.utils.login_password import (
    generate_login_password,
    windows_password_issues,
)

# ---------------------------------------------------------------------------
# 規則本身
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    ["Passw0rd", "student123!", "STUDENT123!", "Student!!", "學生Pass12", "abc 123 XYZ"],
)
def test_three_categories_pass(password: str) -> None:
    assert windows_password_issues(password) == []


@pytest.mark.parametrize("password", ["password1", "PASSWORD", "12345678", "abcdefgh!"])
def test_fewer_than_three_categories_fail(password: str) -> None:
    assert windows_password_issues(password) == ["categories"]


def test_password_must_not_contain_account_name() -> None:
    assert windows_password_issues("MyAdmin#2026") == ["username"]
    assert windows_password_issues("xxADMINxx9!") == ["username"]
    assert windows_password_issues("password", username="Admin") == ["categories"]
    # 帳號名稱不到 3 字時 Windows 不檢查
    assert windows_password_issues("Ab#ab#1234", username="ab") == []


def test_generated_passwords_always_meet_windows_complexity() -> None:
    for _ in range(2000):
        password = generate_login_password()
        assert windows_password_issues(password) == [], password
        assert any(ch.isupper() for ch in password)
        assert any(ch.islower() for ch in password)
        assert any(ch.isdigit() for ch in password)


# ---------------------------------------------------------------------------
# 申請／克隆：seal_custom_password
# ---------------------------------------------------------------------------


def test_seal_rejects_weak_windows_password() -> None:
    with pytest.raises(BadRequestError):
        password_policy.seal_custom_password("student123", windows=True)


def test_seal_accepts_complex_windows_password() -> None:
    sealed = password_policy.seal_custom_password("Student123", windows=True)
    assert sealed.encrypted and sealed.crypt_hash is None


def test_seal_does_not_apply_windows_rule_to_linux() -> None:
    sealed = password_policy.seal_custom_password("student123", windows=False)
    assert sealed.crypt_hash and sealed.encrypted is None


# ---------------------------------------------------------------------------
# 重設密碼
# ---------------------------------------------------------------------------


@pytest.fixture
def reset_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    db_resource = SimpleNamespace(
        login_password_encrypted=None,
        login_password_pending_encrypted=None,
        login_password_hash=None,
        login_password_pending_hash=None,
        ssh_public_key=None,
    )
    config: dict = {"ostype": "win11"}
    update_config = Mock()
    monkeypatch.setattr(
        credentials_service, "_get_db_resource", lambda _session, _vmid: db_resource
    )
    monkeypatch.setattr(
        credentials_service.proxmox_service, "get_config", lambda *a, **k: config
    )
    monkeypatch.setattr(
        credentials_service.proxmox_service, "update_config", update_config
    )
    monkeypatch.setattr(credentials_service.proxmox_service, "control", Mock())
    monkeypatch.setattr(credentials_service.audit_service, "log_action", Mock())
    return SimpleNamespace(config=config, update_config=update_config)


def _reset(password: str | None):
    return credentials_service.reset_password(
        session=Mock(),
        vmid=101,
        resource_info={"node": "pve1", "type": "qemu", "status": "stopped"},
        user_id=uuid.uuid4(),
        password=password,
    )


def test_reset_rejects_weak_password_on_windows_vm(reset_env) -> None:
    with pytest.raises(BadRequestError):
        _reset("student123")
    reset_env.update_config.assert_not_called()


def test_reset_accepts_complex_password_on_windows_vm(reset_env) -> None:
    _reset("Student123")
    reset_env.update_config.assert_called_once()


def test_reset_generated_password_on_windows_vm(reset_env) -> None:
    res = _reset(None)
    assert windows_password_issues(res.password) == []


def test_reset_keeps_linux_rule_unchanged(reset_env) -> None:
    reset_env.config["ostype"] = "l26"
    _reset("student123")
    reset_env.update_config.assert_called_once()


def test_credentials_report_windows(reset_env) -> None:
    info = credentials_service.get_credentials(
        session=Mock(),
        vmid=101,
        resource_info={"node": "pve1", "type": "qemu", "status": "stopped"},
    )
    assert info.is_windows is True


# ---------------------------------------------------------------------------
# 直接建機（POST /vm/create、批次建置）
# ---------------------------------------------------------------------------


def _vm_request(password: str) -> VMCreateRequest:
    return VMCreateRequest(
        hostname="win-1",
        template_id=9000,
        username="student",
        password=password,
        cores=2,
        memory=4096,
        environment_type="Custom",
    )


def test_create_vm_rejects_weak_password_for_windows_template(monkeypatch) -> None:
    monkeypatch.setattr(provisioning_service, "template_is_windows", lambda _id: True)
    target = Mock(side_effect=AssertionError("should not reach placement"))
    monkeypatch.setattr(provisioning_service, "get_vm_target_node", target)

    with pytest.raises(BadRequestError):
        provisioning_service.create_vm(
            session=Mock(), vm_data=_vm_request("student123"), user_id=uuid.uuid4()
        )
    target.assert_not_called()
