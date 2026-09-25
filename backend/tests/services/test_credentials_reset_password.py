"""重設登入密碼：QEMU 寫入 cipassword 後，執行中的 VM 自動重新開機套用。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from app.core.security import decrypt_value
from app.services.resource import credentials_service


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    db_resource = SimpleNamespace(login_password_encrypted=None)
    update_config = Mock()
    control = Mock()
    exec_lxc = Mock(return_value=(0, "", ""))
    monkeypatch.setattr(
        credentials_service, "_get_db_resource", lambda _session, _vmid: db_resource
    )
    monkeypatch.setattr(
        credentials_service.proxmox_service, "update_config", update_config
    )
    monkeypatch.setattr(credentials_service.proxmox_service, "control", control)
    monkeypatch.setattr(credentials_service.guest, "exec_lxc", exec_lxc)
    monkeypatch.setattr(credentials_service.audit_service, "log_action", Mock())
    return SimpleNamespace(
        db_resource=db_resource,
        update_config=update_config,
        control=control,
        exec_lxc=exec_lxc,
    )


def _reset(resource_info: dict[str, Any], password: str | None = "NewPass123"):
    return credentials_service.reset_password(
        session=Mock(),
        vmid=101,
        resource_info=resource_info,
        user_id=uuid.uuid4(),
        password=password,
    )


def test_running_vm_reboots_after_password_reset(env: SimpleNamespace) -> None:
    res = _reset({"node": "pve1", "type": "qemu", "status": "running"})

    env.update_config.assert_called_once_with(
        "pve1", 101, "qemu", cipassword="NewPass123"
    )
    env.control.assert_called_once_with("pve1", 101, "qemu", "reboot")
    assert res.rebooting is True
    assert res.applied_immediately is False
    assert decrypt_value(env.db_resource.login_password_encrypted) == "NewPass123"


def test_stopped_vm_is_not_powered_on(env: SimpleNamespace) -> None:
    res = _reset({"node": "pve1", "type": "qemu", "status": "stopped"})

    env.update_config.assert_called_once()
    env.control.assert_not_called()
    assert res.rebooting is False
    assert env.db_resource.login_password_encrypted is not None


def test_reboot_failure_still_saves_password(env: SimpleNamespace) -> None:
    env.control.side_effect = RuntimeError("VM is locked (backup)")

    res = _reset({"node": "pve1", "type": "qemu", "status": "running"})

    assert res.rebooting is False
    assert "VM is locked (backup)" in res.message
    assert decrypt_value(env.db_resource.login_password_encrypted) == "NewPass123"


def test_lxc_changes_password_in_place_without_reboot(env: SimpleNamespace) -> None:
    res = _reset({"node": "pve1", "type": "lxc", "status": "running"})

    env.exec_lxc.assert_called_once()
    env.control.assert_not_called()
    assert res.applied_immediately is True
    assert res.rebooting is False
