"""回歸：LDAP 登入重算角色。

沒設定 admin 群組時，目錄無法表達管理員，手動指定的管理員不被降級；有設定
admin 群組時角色完全以目錄為準。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.infrastructure.ldap import LdapUserInfo
from app.models import User, UserRole
from app.services.user import ldap_auth_service

TEACHER_DN = "CN=Teachers,OU=Groups,DC=campus,DC=edu"
ADMIN_DN = "CN=Admins,OU=Groups,DC=campus,DC=edu"


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FakeSession:
    def add(self, _obj: Any) -> None:
        """測試替身。"""

    def flush(self) -> None:
        """測試替身。"""

    def commit(self) -> None:
        """測試替身。"""

    def refresh(self, _obj: Any) -> None:
        """測試替身。"""


def _config(admin_group_dn: str | None = ADMIN_DN) -> SimpleNamespace:
    return SimpleNamespace(teacher_group_dn=TEACHER_DN, admin_group_dn=admin_group_dn)


def _info(groups: list[str]) -> LdapUserInfo:
    return LdapUserInfo(
        dn="uid=boss,ou=people,dc=campus,dc=edu",
        email="boss@campus.edu",
        full_name="Boss",
        groups=groups,
    )


def _ldap_user(role: UserRole) -> User:
    return User(
        email="boss@campus.edu",
        hashed_password="x",
        role=role,
        auth_source="ldap",
        token_version=5,
    )


@pytest.mark.parametrize("groups", [[], [TEACHER_DN]])
def test_manual_admin_kept_without_admin_group(groups: list[str]) -> None:
    user = _ldap_user(UserRole.admin)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(None),
        info=_info(groups),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
    assert user.token_version == 5


@pytest.mark.parametrize(
    ("groups", "expected"),
    [([], UserRole.student), ([TEACHER_DN], UserRole.teacher)],
)
def test_admin_group_configured_directory_decides(
    groups: list[str], expected: UserRole
) -> None:
    user = _ldap_user(UserRole.admin)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(ADMIN_DN),
        info=_info(groups),
    )

    assert user.role == expected
    assert user.is_superuser is False


def test_non_superuser_still_follows_directory() -> None:
    user = _ldap_user(UserRole.teacher)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(),
        info=_info([]),
    )

    assert user.role == UserRole.student
    assert user.is_superuser is False


def test_directory_admin_group_promotes() -> None:
    user = _ldap_user(UserRole.student)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(),
        info=_info([ADMIN_DN]),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
