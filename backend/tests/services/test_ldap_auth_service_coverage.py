"""app.services.user.ldap_auth_service 剩餘分支補測（mock 目錄層，不連真實 LDAP）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import AppError
from app.infrastructure.ldap import LdapUserInfo
from app.models import UserRole
from app.services.user import ldap_auth_service as svc


def _config(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "enabled": True,
        "auto_create_users": True,
        "teacher_group_dn": "CN=Teachers,DC=campus,DC=edu",
        "admin_group_dn": "CN=Admins,DC=campus,DC=edu",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _session_config(*, session: Any) -> SimpleNamespace:
    return _config()


def test_test_config_reports_bind_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "get_ldap_config", _session_config)

    def failing_bind(_config: Any) -> None:
        raise AppError(status_code=502, message="LDAP server unreachable")

    monkeypatch.setattr(svc.ldap_client, "test_bind", failing_bind)

    result = svc.test_config(None)

    assert result.ok is False
    assert result.message == "LDAP server unreachable"


def test_role_sync_ignores_non_ldap_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc.user_repo,
        "update_user",
        lambda **_k: pytest.fail("local accounts must not be re-roled"),
    )
    user = SimpleNamespace(auth_source="local", role=UserRole.student)
    svc._sync_role_from_directory(
        session=None,
        user=user,
        config=_config(),
        info=SimpleNamespace(groups=["CN=Admins,DC=campus,DC=edu"]),
    )
    assert user.role == UserRole.student


def test_login_with_totp_issues_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="stu1@campus.edu",
        auth_source="ldap",
        role=UserRole.student,
        is_active=True,
        totp_enabled=True,
    )
    monkeypatch.setattr(svc, "get_ldap_config", _session_config)
    monkeypatch.setattr(
        svc.ldap_client,
        "authenticate_user",
        lambda _c, _u, _p: LdapUserInfo(
            dn="uid=stu1,dc=campus,dc=edu",
            email="stu1@campus.edu",
            full_name="Student",
            groups=[],
        ),
    )
    monkeypatch.setattr(
        svc.user_repo, "get_user_by_email", lambda *, session, email: user
    )
    monkeypatch.setattr(
        svc.totp_service,
        "issue_challenge",
        lambda u, *, method: ("challenge", u.id, method),
    )

    result = svc.login_ldap(session=None, username="stu1", password="pw")

    assert result == ("challenge", user.id, "ldap")
