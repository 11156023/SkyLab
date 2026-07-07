"""LDAP 登入業務邏輯測試（mock 目錄層與 DB 存取，不連真實 LDAP）。"""

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import (
    AuthenticationError,
    BadRequestError,
    UpstreamServiceError,
)
from app.infrastructure.ldap import LdapUserInfo
from app.models import UserRole
from app.services.user import ldap_auth_service
from app.services.user.ldap_auth_service import _role_from_groups

TEACHER_DN = "CN=Teachers,OU=Groups,DC=campus,DC=edu"
ADMIN_DN = "CN=Admins,OU=Groups,DC=campus,DC=edu"


def _config(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "enabled": True,
        "auto_create_users": True,
        "teacher_group_dn": TEACHER_DN,
        "admin_group_dn": ADMIN_DN,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _info(**overrides: object) -> LdapUserInfo:
    values: dict = {
        "dn": "uid=stu1,ou=people,dc=campus,dc=edu",
        "email": "stu1@campus.edu",
        "full_name": "學生一",
        "groups": [],
    }
    values.update(overrides)
    return LdapUserInfo(**values)  # type: ignore[arg-type]


class _FakeSession:
    """login_ldap 只用 session 傳遞給被 mock 掉的函式，本身不需行為。"""

    def add(self, obj: object) -> None:
        self.added = obj

    def commit(self) -> None:
        pass

    def refresh(self, obj: object) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()  # type: ignore[attr-defined]


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> dict:
    """預設樁：enabled 設定、無本地帳號、audit 記到 list。"""
    calls: dict = {"audit": []}
    monkeypatch.setattr(
        ldap_auth_service, "get_ldap_config", lambda *, session: _config()
    )
    monkeypatch.setattr(
        ldap_auth_service.user_repo,
        "get_user_by_email",
        lambda *, session, email: None,
    )
    monkeypatch.setattr(
        ldap_auth_service.audit_service,
        "log_action",
        lambda **kwargs: calls["audit"].append(kwargs),
    )
    monkeypatch.setattr(
        ldap_auth_service,
        "_create_token_pair",
        lambda user: SimpleNamespace(access_token="a", refresh_token="r"),
    )
    return calls


def test_disabled_config_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ldap_auth_service,
        "get_ldap_config",
        lambda *, session: _config(enabled=False),
    )
    with pytest.raises(BadRequestError):
        ldap_auth_service.login_ldap(
            session=_FakeSession(), username="stu1", password="pw"  # type: ignore[arg-type]
        )


def test_auto_create_student(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "authenticate_user",
        lambda config, username, password: _info(),
    )
    session = _FakeSession()
    token = ldap_auth_service.login_ldap(
        session=session, username="stu1", password="pw"  # type: ignore[arg-type]
    )
    assert token.access_token == "a"
    created = session.added
    assert created.email == "stu1@campus.edu"
    assert created.role == UserRole.student
    actions = [c["action"] for c in patched["audit"]]
    assert any("success" in str(a) for a in actions)


def test_admin_group_maps_role(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "authenticate_user",
        lambda config, username, password: _info(groups=[ADMIN_DN.lower()]),
    )
    session = _FakeSession()
    ldap_auth_service.login_ldap(
        session=session, username="boss", password="pw"  # type: ignore[arg-type]
    )
    assert session.added.role == UserRole.admin


def test_no_auto_create_rejects(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    monkeypatch.setattr(
        ldap_auth_service,
        "get_ldap_config",
        lambda *, session: _config(auto_create_users=False),
    )
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "authenticate_user",
        lambda config, username, password: _info(),
    )
    with pytest.raises(BadRequestError):
        ldap_auth_service.login_ldap(
            session=_FakeSession(), username="stu1", password="pw"  # type: ignore[arg-type]
        )
    actions = [c["action"] for c in patched["audit"]]
    assert any("failed" in str(a) for a in actions)


def test_invalid_credentials_propagates(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    def _raise(config, username, password):  # noqa: ANN001, ANN202
        raise AuthenticationError("帳號或密碼錯誤")

    monkeypatch.setattr(
        ldap_auth_service.ldap_client, "authenticate_user", _raise
    )
    with pytest.raises(AuthenticationError):
        ldap_auth_service.login_ldap(
            session=_FakeSession(), username="stu1", password="bad"  # type: ignore[arg-type]
        )
    actions = [c["action"] for c in patched["audit"]]
    assert any("failed" in str(a) for a in actions)


def test_server_error_propagates(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    def _raise(config, username, password):  # noqa: ANN001, ANN202
        raise UpstreamServiceError("無法連線 LDAP 伺服器")

    monkeypatch.setattr(
        ldap_auth_service.ldap_client, "authenticate_user", _raise
    )
    with pytest.raises(UpstreamServiceError):
        ldap_auth_service.login_ldap(
            session=_FakeSession(), username="stu1", password="pw"  # type: ignore[arg-type]
        )


def test_inactive_existing_user_rejected(
    monkeypatch: pytest.MonkeyPatch, patched: dict
) -> None:
    existing = SimpleNamespace(
        id=uuid.uuid4(), email="stu1@campus.edu", is_active=False
    )
    monkeypatch.setattr(
        ldap_auth_service.user_repo,
        "get_user_by_email",
        lambda *, session, email: existing,
    )
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "authenticate_user",
        lambda config, username, password: _info(),
    )
    with pytest.raises(BadRequestError):
        ldap_auth_service.login_ldap(
            session=_FakeSession(), username="stu1", password="pw"  # type: ignore[arg-type]
        )


def test_role_from_groups_case_insensitive() -> None:
    assert (
        _role_from_groups(
            [TEACHER_DN.upper()],
            teacher_group_dn=TEACHER_DN,
            admin_group_dn=ADMIN_DN,
        )
        == UserRole.teacher
    )
    assert (
        _role_from_groups([], teacher_group_dn=TEACHER_DN, admin_group_dn=ADMIN_DN)
        == UserRole.student
    )
