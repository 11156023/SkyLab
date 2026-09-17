"""機器登入密碼的產生與保存規則（2026-09-17 統一）。

- 系統代發的密碼一律走 ``app.utils.login_password.generate_login_password``
  （12 碼、排除 0O1lI），批次建立 / 快速練習不再用 ``secrets.token_urlsafe``
- Course Lab 申請單不帶密碼（None），provision 不覆寫範本內烘焙的憑證
- provision 完成後密碼存進 ``resources.login_password_encrypted``，
  申請單上的可逆副本清空；沒真的套用（未啟動 / None）就不存
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from app.core.security import decrypt_value
from app.models import (
    CourseEnvironment,
    CourseEnvironmentNode,
    CourseRoom,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
)
from app.services import quick_practice
from app.services.course import deployment_service
from app.services.proxmox import provisioning_service
from app.services.scheduling import coordinator
from app.services.template import clone_service
from app.services.vm import batch_provision_service, vm_request_service
from app.utils.login_password import (
    PASSWORD_ALPHABET,
    PASSWORD_LENGTH,
    generate_login_password,
)


def _is_generated(password: str | None) -> bool:
    return (
        isinstance(password, str)
        and len(password) == PASSWORD_LENGTH
        and all(ch in PASSWORD_ALPHABET for ch in password)
    )


# ---------------------------------------------------------------------------
# 產生器
# ---------------------------------------------------------------------------


def test_generate_login_password_is_console_typeable() -> None:
    seen = {generate_login_password() for _ in range(50)}
    assert all(_is_generated(pw) for pw in seen)
    assert len(seen) > 1
    for confusing in "0O1lI":
        assert confusing not in PASSWORD_ALPHABET


def test_clone_service_reexports_shared_generator() -> None:
    # 既有呼叫端 / 測試仍從 clone_service 拿，必須是同一個實作
    assert clone_service.generate_login_password is generate_login_password
    assert clone_service._PASSWORD_ALPHABET == PASSWORD_ALPHABET


# ---------------------------------------------------------------------------
# 各入口的密碼來源
# ---------------------------------------------------------------------------


def test_quick_practice_machine_gets_typeable_password() -> None:
    now = datetime.now(UTC)
    node = CourseEnvironmentNode(
        version_id=uuid.uuid4(),
        node_key="web",
        source_type="custom",
        custom_image_ref="local:vztmpl/debian.tar.zst",
        name="Web",
        role="前端",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
        sort_order=0,
    )
    environment = CourseEnvironment(
        owner_id=uuid.uuid4(), name="練習", usage_scope="quick_practice"
    )

    request = quick_practice._machine_request(
        session=Mock(),
        node=node,
        environment=environment,
        practice_session_id=uuid.uuid4(),
        now=now,
        expires_at=now + timedelta(hours=1),
    )

    assert _is_generated(request.password)


def test_course_lab_request_carries_no_password() -> None:
    room = CourseRoom(id=uuid.uuid4(), path_id=uuid.uuid4(), title="Lab 1")
    template = VMTemplate(
        pve_vmid=9000,
        node="pve1",
        name="ubuntu-lab",
        resource_type="qemu",
        default_cores=2,
        default_memory=2048,
        default_disk=20,
        storage="local-lvm",
    )
    now = datetime.now(UTC)

    request = deployment_service._build_request(
        room=room,
        template=template,
        user_id=uuid.uuid4(),
        now=now,
        ttl_hours=4,
    )

    # None → provision 不帶 cipassword / 不跑 chpasswd，沿用範本憑證
    assert request.password is None
    assert request.template_id == 9000


def test_batch_provision_generates_typeable_password_when_blank(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        batch_provision_service.quota_service, "check_quota", lambda *a, **k: None
    )

    def fake_create_lxc(*, session, lxc_data, **kwargs):
        captured["lxc_data"] = lxc_data
        return SimpleNamespace(vmid=101)

    monkeypatch.setattr(
        batch_provision_service.provisioning_service, "create_lxc", fake_create_lxc
    )

    vmid = batch_provision_service._provision_one(
        session=Mock(),
        resource_type="lxc",
        hostname="batch-1",
        user_id=uuid.uuid4(),
        params={
            "ostemplate": "local:vztmpl/debian.tar.zst",
            "cores": 1,
            "memory": 512,
        },
    )

    assert vmid == 101
    assert _is_generated(captured["lxc_data"].password)


def test_batch_provision_keeps_admin_supplied_password(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        batch_provision_service.quota_service, "check_quota", lambda *a, **k: None
    )

    def fake_create_vm(*, session, vm_data, **kwargs):
        captured["vm_data"] = vm_data
        return SimpleNamespace(vmid=102)

    monkeypatch.setattr(
        batch_provision_service.provisioning_service, "create_vm", fake_create_vm
    )

    batch_provision_service._provision_one(
        session=Mock(),
        resource_type="vm",
        hostname="batch-2",
        user_id=uuid.uuid4(),
        params={
            "template_id": 9000,
            "cores": 2,
            "memory": 2048,
            "password": "Admin1234",
        },
    )

    assert captured["vm_data"].password == "Admin1234"


def test_encrypt_login_password_keeps_none() -> None:
    assert vm_request_service._encrypt_login_password(None) is None
    encrypted = vm_request_service._encrypt_login_password("Secret123")
    assert encrypted and decrypt_value(encrypted) == "Secret123"


# ---------------------------------------------------------------------------
# provision 結果 → resources 憑證
# ---------------------------------------------------------------------------


def test_applied_login_password_encrypted_requires_actual_application() -> None:
    assert provisioning_service.applied_login_password_encrypted({}) is None
    assert (
        provisioning_service.applied_login_password_encrypted(
            {"password": "Abc23456789x", "login_password_applied": False}
        )
        is None
    )
    assert (
        provisioning_service.applied_login_password_encrypted(
            {"password": None, "login_password_applied": True}
        )
        is None
    )
    encrypted = provisioning_service.applied_login_password_encrypted(
        {"password": "Abc23456789x", "login_password_applied": True}
    )
    assert encrypted and decrypt_value(encrypted) == "Abc23456789x"


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _drive_provision(monkeypatch, *, applied: bool) -> tuple[VMRequest, dict]:
    """跑 coordinator._provision_new_resource 三個階段，回傳申請單與 create_resource 參數。"""
    req = VMRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        hostname="vm-pw",
        resource_type="vm",
        status=VMRequestStatus.approved,
        vmid=None,
        cores=2,
        memory=2048,
        password="encrypted-copy",
        environment_type="Custom",
    )
    plan = {
        "vmid": 480,
        "placement_strategy": "balanced",
        "password": "Abc23456789x",
        "ssh_private_key_encrypted": "enc-priv",
        "ssh_public_key": "ssh-ed25519 AAA",
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr(provisioning_service, "plan_provision", lambda **kw: plan)

    def fake_execute(p):
        p["login_password_applied"] = applied
        return 480, "pve1"

    monkeypatch.setattr(provisioning_service, "execute_provision", fake_execute)
    monkeypatch.setattr(coordinator, "Session", lambda engine: _FakeSession())
    monkeypatch.setattr(
        coordinator.vm_request_repo,
        "get_vm_request_by_id",
        lambda **kw: req,
    )

    def fake_update(**kw):
        kw["db_request"].vmid = kw["vmid"]
        kw["db_request"].provisioning_status = kw["provisioning_status"]
        return kw["db_request"]

    monkeypatch.setattr(
        coordinator.vm_request_repo, "update_vm_request_provisioning", fake_update
    )
    monkeypatch.setattr(
        coordinator.resource_repo,
        "create_resource",
        lambda **kw: captured.update(kw),
    )
    monkeypatch.setattr(coordinator.audit_service, "log_action", lambda **kw: None)
    from app.services.resource import reset_service

    monkeypatch.setattr(reset_service, "ensure_init_snapshot", lambda vmid: True)

    coordinator._provision_new_resource(session=_FakeSession(), request=req)
    return req, captured


def test_provision_stores_applied_password_and_clears_request_copy(
    monkeypatch,
) -> None:
    req, captured = _drive_provision(monkeypatch, applied=True)

    assert captured["vmid"] == 480
    assert decrypt_value(captured["login_password_encrypted"]) == "Abc23456789x"
    # 申請單不再保留可逆副本
    assert req.password is None
    assert req.provisioning_status == VMProvisioningStatus.completed


def test_provision_without_applied_password_stores_nothing(monkeypatch) -> None:
    req, captured = _drive_provision(monkeypatch, applied=False)

    assert captured["login_password_encrypted"] is None
    assert req.password is None
