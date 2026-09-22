"""LXC 範本克隆在建立時沒開機，產生的密碼要留到第一次受管開機補設。

2026-09-21：有排程的班級機器建立後不開機，隨機密碼直接被丟掉 —— 機器沿用
母範本的密碼（全班同一組），學生的憑證卡片顯示「未記錄」。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.security import decrypt_value, encrypt_value
from app.repositories import resource as resource_repo
from app.services.proxmox import provisioning_service
from app.services.resource import resource_service
from app.services.template import clone_service


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _resource(db: Session, *, vmid: int, applied: str | None, pending: str | None):
    import uuid

    return resource_repo.create_resource(
        session=db,
        vmid=vmid,
        user_id=uuid.uuid4(),
        environment_type="class",
        login_password_encrypted=encrypt_value(applied) if applied else None,
        login_password_pending_encrypted=encrypt_value(pending) if pending else None,
    )


def _capture_chpasswd(monkeypatch: pytest.MonkeyPatch, *, ok: bool = True):
    calls: list[tuple[str, int, str]] = []
    monkeypatch.setattr(
        clone_service,
        "set_lxc_root_password",
        lambda node, vmid, password: calls.append((node, vmid, password)) or ok,
    )
    return calls


def test_pending_password_is_applied_and_becomes_visible_on_first_start(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=501, applied=None, pending="Rand0mPass12")
    calls = _capture_chpasswd(monkeypatch)

    assert resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=501
    )

    assert calls == [("pve", 501, "Rand0mPass12")]
    row = resource_repo.get_resource_by_vmid(session=db, vmid=501)
    assert row is not None
    assert decrypt_value(row.login_password_encrypted) == "Rand0mPass12"
    assert row.login_password_pending_encrypted is None


def test_pending_password_is_kept_for_the_next_start_when_the_guest_rejects_it(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=502, applied=None, pending="Rand0mPass12")
    _capture_chpasswd(monkeypatch, ok=False)

    assert not resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=502
    )

    row = resource_repo.get_resource_by_vmid(session=db, vmid=502)
    assert row is not None
    # 沒寫進去就不能顯示，也不能弄丟
    assert row.login_password_encrypted is None
    assert decrypt_value(row.login_password_pending_encrypted) == "Rand0mPass12"


def test_ordinary_start_never_overwrites_a_password_the_user_may_have_changed(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=503, applied="AlreadyApplied1", pending=None)
    calls = _capture_chpasswd(monkeypatch)

    assert not resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=503
    )
    assert calls == []


def test_reset_reapplies_the_recorded_password_after_the_rollback(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照 rollback 會把 /etc/shadow 還原，顯示的密碼必須重新寫回去。"""
    _resource(db, vmid=504, applied="AlreadyApplied1", pending=None)
    calls = _capture_chpasswd(monkeypatch)

    assert resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=504, reapply_recorded=True
    )
    assert calls == [("pve", 504, "AlreadyApplied1")]


def test_plan_helpers_split_applied_and_pending_passwords() -> None:
    applied = {"password": "Secret123456", "login_password_applied": True}
    unapplied = {"password": "Secret123456", "login_password_applied": False}
    course_lab = {"password": None, "login_password_applied": False}

    assert provisioning_service.applied_login_password_encrypted(applied)
    assert provisioning_service.pending_login_password_encrypted(applied) is None

    assert provisioning_service.applied_login_password_encrypted(unapplied) is None
    pending = provisioning_service.pending_login_password_encrypted(unapplied)
    assert pending is not None and decrypt_value(pending) == "Secret123456"

    # Course Lab 沿用範本憑證：兩邊都不該有值
    assert provisioning_service.applied_login_password_encrypted(course_lab) is None
    assert provisioning_service.pending_login_password_encrypted(course_lab) is None
