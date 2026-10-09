"""登入密碼規則（依範本的密碼平台設不設得了）：每個建立入口的行為都要照同一張表。

====================  ====================  ========================
入口                   平台設不了            平台設得了 / 一般映像
====================  ====================  ========================
學生申請               沿用範本（None）      申請人自訂（必填）
快速練習               沿用範本（None）      隨機
班級機器               沿用範本（None）      隨機
====================  ====================  ========================

自訂的密碼只以雜湊落 DB；Windows 例外（cloudbase-init 只收明文）。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.security import decrypt_value
from app.exceptions import BadRequestError
from app.models import VMTemplate, VMTemplateStatus
from app.services.template import password_policy
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


def _template(*, settable: bool, **overrides: object) -> SimpleNamespace:
    base = dict(
        password_settable=settable,
        resource_type="lxc",
        pve_vmid=481,
        name="n8n",
        default_cores=2,
        default_memory=2048,
        default_disk=13,
        storage="data-ssd-2",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ─── 規則本體 ────────────────────────────────────────────────────────────────


def test_unsettable_template_keeps_its_own_password_whatever_the_caller_sends() -> None:
    kept = _template(settable=False)

    assert password_policy.resolve_login_password(template=kept) is None
    assert password_policy.resolve_login_password(template=kept, custom="Typed12345") is None
    assert (
        password_policy.resolve_login_password(
            template=kept, custom=None, require_custom=True
        )
        is None
    )


def test_settable_template_gets_a_random_password_when_nothing_is_typed() -> None:
    first = password_policy.resolve_login_password(template=_template(settable=True))
    second = password_policy.resolve_login_password(template=_template(settable=True))

    assert first and second and first != second
    assert len(first) == 12


def test_generic_image_is_treated_like_a_settable_template() -> None:
    assert password_policy.resolve_login_password(template=None)
    assert (
        password_policy.resolve_login_password(template=None, custom="Typed12345")
        == "Typed12345"
    )


def test_student_request_must_type_a_password_unless_the_template_keeps_its_own() -> None:
    with pytest.raises(BadRequestError):
        password_policy.resolve_login_password(
            template=_template(settable=True), custom=None, require_custom=True
        )
    with pytest.raises(BadRequestError):
        password_policy.resolve_login_password(
            template=None, custom="", require_custom=True
        )
    assert (
        password_policy.resolve_login_password(
            template=_template(settable=True), custom="Typed12345", require_custom=True
        )
        == "Typed12345"
    )


# ─── 自訂密碼的保存形式 ──────────────────────────────────────────────────────


def test_custom_password_is_sealed_as_a_hash() -> None:
    sealed = password_policy.seal_custom_password("Typed12345", windows=False)

    assert sealed.encrypted is None
    assert hash_matches("Typed12345", sealed.crypt_hash)
    assert "Typed12345" not in (sealed.crypt_hash or "")


def test_same_password_seals_to_a_different_hash_each_time() -> None:
    first = password_policy.seal_custom_password("Typed12345", windows=False)
    second = password_policy.seal_custom_password("Typed12345", windows=False)

    assert first.crypt_hash != second.crypt_hash


def test_windows_password_is_kept_encrypted_until_provisioning() -> None:
    """cloudbase-init 只收明文，所以 Windows 是唯一暫存可還原密碼的情況。"""
    sealed = password_policy.seal_custom_password("Typed12345", windows=True)

    assert sealed.crypt_hash is None
    assert sealed.encrypted is not None and sealed.encrypted != "Typed12345"
    assert decrypt_value(sealed.encrypted) == "Typed12345"


@pytest.mark.parametrize("windows", [False, True])
def test_no_password_seals_to_nothing(windows: bool) -> None:
    assert password_policy.seal_custom_password(
        None, windows=windows
    ) == password_policy.SealedPassword()


# ─── 範本查找 ────────────────────────────────────────────────────────────────


def _db_template(
    db: Session, *, pve_vmid: int, settable: bool, status: VMTemplateStatus
) -> VMTemplate:
    row = VMTemplate(
        name=f"tpl-{uuid.uuid4().hex[:6]}",
        pve_vmid=pve_vmid,
        node="pve",
        resource_type="lxc",
        owner_id=uuid.uuid4(),
        status=status,
        password_settable=settable,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_lookup_ignores_a_deleted_template(db: Session) -> None:
    """範本列刪除後仍留在表裡（pve_vmid 唯一），而 VMID 會被一般機器回收再用：
    舊範本的「設不了」不能套到後來用同一個 VMID 的來源上。"""
    _db_template(db, pve_vmid=900, settable=False, status=VMTemplateStatus.deleted)
    live = _db_template(db, pve_vmid=901, settable=False, status=VMTemplateStatus.ready)

    assert password_policy.find_template(db, pve_vmid=900) is None
    found = password_policy.find_template(db, pve_vmid=901)
    assert found is not None and found.id == live.id
    assert password_policy.keeps_template_credentials(found)
    assert password_policy.find_template(db, template_id=live.id) is not None


def test_unregistered_source_is_not_a_template(db: Session) -> None:
    assert password_policy.find_template(db, pve_vmid=12345) is None
    assert password_policy.find_template(db, pve_vmid=None) is None
    assert not password_policy.keeps_template_credentials(None)
