"""LXC 範本克隆在建立時沒開機，產生的密碼要留到第一次受管開機補設。

2026-09-21：有排程的班級機器建立後不開機，隨機密碼直接被丟掉 —— 機器沿用
母範本的密碼（全班同一組），學生的憑證卡片顯示「未記錄」。

使用者自訂的密碼走同一套流程，但平台手上只有 SHA-512 crypt 雜湊：
補設與一鍵重置後的重寫都用 ``chpasswd -e``，而且永遠不會變成可顯示的密碼。
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
from app.utils.login_password import hash_login_password
from tests.utils.login_password import hash_matches


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


def _resource(
    db: Session,
    *,
    vmid: int,
    applied: str | None = None,
    pending: str | None = None,
    applied_hash: str | None = None,
    pending_hash: str | None = None,
):
    import uuid

    return resource_repo.create_resource(
        session=db,
        vmid=vmid,
        user_id=uuid.uuid4(),
        environment_type="class",
        login_password_encrypted=encrypt_value(applied) if applied else None,
        login_password_pending_encrypted=encrypt_value(pending) if pending else None,
        login_password_hash=applied_hash,
        login_password_pending_hash=pending_hash,
    )


def _capture_chpasswd(monkeypatch: pytest.MonkeyPatch, *, ok: bool = True):
    """記下每次寫密碼的 (node, vmid, 值, 是否為雜湊)。"""
    calls: list[tuple[str, int, str, bool]] = []

    def _fake(node: str, vmid: int, password: str, *, hashed: bool = False) -> bool:
        calls.append((node, vmid, password, hashed))
        return ok

    monkeypatch.setattr(clone_service, "set_lxc_root_password", _fake)
    return calls


def test_pending_password_is_applied_and_becomes_visible_on_first_start(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=501, applied=None, pending="Rand0mPass12")
    calls = _capture_chpasswd(monkeypatch)

    assert resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=501
    )

    assert calls == [("pve", 501, "Rand0mPass12", False)]
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
    assert calls == [("pve", 504, "AlreadyApplied1", False)]


# ─── 使用者自訂的密碼：平台只有雜湊 ──────────────────────────────────────────

_CUSTOM_HASH = hash_login_password("Typed12345")


def test_pending_custom_password_is_applied_as_a_hash_and_never_becomes_visible(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=505, pending_hash=_CUSTOM_HASH)
    calls = _capture_chpasswd(monkeypatch)

    assert resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=505
    )

    assert calls == [("pve", 505, _CUSTOM_HASH, True)]
    row = resource_repo.get_resource_by_vmid(session=db, vmid=505)
    assert row is not None
    assert row.login_password_hash == _CUSTOM_HASH
    assert row.login_password_pending_hash is None
    # 沒有任何可還原的副本可以拿去顯示
    assert row.login_password_encrypted is None
    assert row.login_password_pending_encrypted is None


def test_pending_custom_password_is_kept_when_the_guest_rejects_it(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=506, pending_hash=_CUSTOM_HASH)
    _capture_chpasswd(monkeypatch, ok=False)

    assert not resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=506
    )

    row = resource_repo.get_resource_by_vmid(session=db, vmid=506)
    assert row is not None
    assert row.login_password_hash is None
    assert row.login_password_pending_hash == _CUSTOM_HASH


def test_ordinary_start_never_reapplies_a_custom_password(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _resource(db, vmid=507, applied_hash=_CUSTOM_HASH)
    calls = _capture_chpasswd(monkeypatch)

    assert not resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=507
    )
    assert calls == []


def test_reset_reapplies_the_custom_password_hash_after_the_rollback(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rollback 會把 /etc/shadow 退回範本內建密碼；沒有明文也要補得回去。"""
    _resource(db, vmid=508, applied_hash=_CUSTOM_HASH)
    calls = _capture_chpasswd(monkeypatch)

    assert resource_service.ensure_lxc_login_password(
        session=db, node="pve", vmid=508, reapply_recorded=True
    )
    assert calls == [("pve", 508, _CUSTOM_HASH, True)]


def test_hashed_password_is_written_with_chpasswd_e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str | None]] = []

    def _fake_exec(node, vmid, command, *, stdin=None, what):
        seen.append((command, stdin))
        return True

    monkeypatch.setattr(clone_service, "_exec_lxc_with_retry", _fake_exec)

    assert clone_service.set_lxc_root_password("pve", 1, _CUSTOM_HASH, hashed=True)
    assert clone_service.set_lxc_root_password("pve", 1, "PlainPass123")

    assert seen == [
        ("chpasswd -e", f"root:{_CUSTOM_HASH}\n"),
        ("chpasswd", "root:PlainPass123\n"),
    ]


# ─── plan → 資源欄位 ─────────────────────────────────────────────────────────


def test_plan_helpers_split_applied_and_pending_passwords() -> None:
    applied = {"password": "Secret123456", "login_password_applied": True}
    unapplied = {"password": "Secret123456", "login_password_applied": False}
    course_lab = {"password": None, "login_password_applied": False}

    assert provisioning_service.applied_login_password_encrypted(applied)
    assert provisioning_service.pending_login_password_encrypted(applied) is None

    assert provisioning_service.applied_login_password_encrypted(unapplied) is None
    pending = provisioning_service.pending_login_password_encrypted(unapplied)
    assert pending is not None and decrypt_value(pending) == "Secret123456"

    # 系統代發的密碼不會變成雜湊
    assert provisioning_service.applied_login_password_hash(applied) is None
    assert provisioning_service.pending_login_password_hash(unapplied) is None

    # Course Lab 沿用範本憑證：四個欄位都不該有值
    for helper in (
        provisioning_service.applied_login_password_encrypted,
        provisioning_service.pending_login_password_encrypted,
        provisioning_service.applied_login_password_hash,
        provisioning_service.pending_login_password_hash,
    ):
        assert helper(course_lab) is None


def test_plan_helpers_never_keep_a_custom_password_reversibly() -> None:
    applied = {
        "password_hash": _CUSTOM_HASH,
        "password_custom": True,
        "login_password_applied": True,
    }
    unapplied = {**applied, "login_password_applied": False}

    assert provisioning_service.applied_login_password_encrypted(applied) is None
    assert provisioning_service.pending_login_password_encrypted(unapplied) is None

    assert provisioning_service.applied_login_password_hash(applied) == _CUSTOM_HASH
    assert provisioning_service.pending_login_password_hash(applied) is None
    assert provisioning_service.applied_login_password_hash(unapplied) is None
    assert provisioning_service.pending_login_password_hash(unapplied) == _CUSTOM_HASH


def test_plan_helpers_hash_a_custom_plaintext_password() -> None:
    """Windows 的自訂密碼建機時是明文，建完也只留雜湊。"""
    windows = {
        "password": "Typed12345",
        "password_custom": True,
        "login_password_applied": True,
    }

    assert provisioning_service.applied_login_password_encrypted(windows) is None
    assert hash_matches(
        "Typed12345", provisioning_service.applied_login_password_hash(windows)
    )


def test_request_password_plan_marks_only_personal_requests_as_custom() -> None:
    from types import SimpleNamespace

    personal = SimpleNamespace(
        password=None, password_hash=_CUSTOM_HASH, request_kind="research"
    )
    practice = SimpleNamespace(
        password=encrypt_value("Rand0mPass12"),
        password_hash=None,
        request_kind="quick_template",
    )

    assert provisioning_service.request_password_plan(personal) == {
        "password": None,
        "password_hash": _CUSTOM_HASH,
        "password_custom": True,
    }
    assert provisioning_service.request_password_plan(practice) == {
        "password": "Rand0mPass12",
        "password_hash": None,
        "password_custom": False,
    }
