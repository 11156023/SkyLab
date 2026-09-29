"""LDAP 回傳的信箱屬性不像信箱時，不可建立或比對任何本地帳號。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError
from app.infrastructure.ldap import LdapUserInfo
from app.services.user import ldap_auth_service


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def refresh(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    recorded: dict[str, list[Any]] = {"audit": [], "lookups": []}
    monkeypatch.setattr(
        ldap_auth_service,
        "get_ldap_config",
        lambda *, session: SimpleNamespace(
            enabled=True,
            auto_create_users=True,
            teacher_group_dn=None,
            admin_group_dn=None,
        ),
    )

    def _lookup(*, session: Any, email: str) -> None:
        recorded["lookups"].append(email)
        return None

    monkeypatch.setattr(ldap_auth_service.user_repo, "get_user_by_email", _lookup)
    monkeypatch.setattr(
        ldap_auth_service.audit_service,
        "log_action",
        lambda **kwargs: recorded["audit"].append(kwargs),
    )
    monkeypatch.setattr(
        ldap_auth_service,
        "create_token_pair",
        lambda user: SimpleNamespace(access_token="a", refresh_token="r"),
    )
    return recorded


def _returning(email: str):
    def _authenticate(config: Any, username: str, password: str) -> LdapUserInfo:
        return LdapUserInfo(dn="uid=jdoe,dc=school,dc=local", email=email, full_name="J", groups=[])

    return _authenticate


@pytest.mark.parametrize("email", ["jdoe", "@school.local", "jdoe@", "a@b@c", "j doe@school.local"])
def test_non_email_attribute_is_rejected_before_account_lookup(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]], email: str
) -> None:
    monkeypatch.setattr(ldap_auth_service.ldap_client, "authenticate_user", _returning(email))
    session = _FakeSession()

    with pytest.raises(BadRequestError):
        ldap_auth_service.login_ldap(session=session, username="jdoe", password="pw")  # type: ignore[arg-type]

    assert calls["lookups"] == []
    assert session.added == []
    assert any("invalid email attribute" in a["details"] for a in calls["audit"])


def test_local_ad_domain_is_accepted_and_creates_user(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[Any]]
) -> None:
    monkeypatch.setattr(
        ldap_auth_service.ldap_client, "authenticate_user", _returning("alice@school.local")
    )
    session = _FakeSession()

    ldap_auth_service.login_ldap(session=session, username="alice", password="pw")  # type: ignore[arg-type]

    assert calls["lookups"] == ["alice@school.local"]
    created = [obj for obj in session.added if getattr(obj, "email", None) == "alice@school.local"]
    assert created, "應自動建立 alice@school.local 帳號"
