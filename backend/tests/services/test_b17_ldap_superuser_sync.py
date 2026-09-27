"""B17 回歸：LDAP 登入重算角色時，手動指定的超級使用者不被目錄群組降級。

user_repo.update_user 在只給 role 時會讓 is_superuser 跟著新角色走（管理頁
降級管理員需要這樣），所以 LDAP 同步必須自己擋下超級使用者的降級。
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


def _ldap_user(role: UserRole, *, is_superuser: bool) -> User:
    return User(
        email="boss@campus.edu",
        hashed_password="x",
        role=role,
        is_superuser=is_superuser,
        auth_source="ldap",
        token_version=5,
    )


@pytest.mark.parametrize("admin_group_dn", [ADMIN_DN, None])
@pytest.mark.parametrize("groups", [[], [TEACHER_DN]])
def test_manual_superuser_not_demoted_by_directory(
    admin_group_dn: str | None, groups: list[str]
) -> None:
    user = _ldap_user(UserRole.admin, is_superuser=True)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(admin_group_dn),
        info=_info(groups),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
    assert user.token_version == 5


def test_non_superuser_still_follows_directory() -> None:
    user = _ldap_user(UserRole.teacher, is_superuser=False)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(),
        info=_info([]),
    )

    assert user.role == UserRole.student
    assert user.is_superuser is False


def test_directory_admin_group_promotes() -> None:
    user = _ldap_user(UserRole.student, is_superuser=False)

    ldap_auth_service._sync_role_from_directory(
        session=_FakeSession(),  # type: ignore[arg-type]
        user=user,
        config=_config(),
        info=_info([ADMIN_DN]),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
