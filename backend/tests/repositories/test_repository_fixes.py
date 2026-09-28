"""回歸：使用者降級、LDAP 群組 DN 清空、殭屍任務訊息。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models import LdapConfig, TaskRecord, TaskRecordStatus, User, UserRole
from app.repositories import ldap_config as ldap_config_repo
from app.repositories import task_record as task_record_repo
from app.repositories import user as user_repo
from app.schemas import UserUpdate


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _FlushSession:
    def add(self, _obj: Any) -> None:
        """測試替身。"""

    def flush(self) -> None:
        """測試替身。"""


def _admin() -> User:
    return User(
        email="admin@example.com",
        hashed_password="x",
        role=UserRole.admin,
        token_version=3,
    )


# ─── 管理員可以被降級 ────────────────────────────────────────────────


@pytest.mark.parametrize("new_role", [UserRole.teacher, UserRole.student])
def test_update_user_role_only_demotes_admin(new_role: UserRole) -> None:
    user = _admin()

    user_repo.update_user(
        session=_FlushSession(),  # type: ignore[arg-type]
        db_user=user,
        user_in=UserUpdate(role=new_role),
    )

    assert user.role == new_role
    assert user.is_superuser is False
    assert user.token_version == 4


def test_update_user_without_role_keeps_admin() -> None:
    user = _admin()

    user_repo.update_user(
        session=_FlushSession(),  # type: ignore[arg-type]
        db_user=user,
        user_in=UserUpdate(full_name="x"),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
    assert user.token_version == 3
    assert user.full_name == "x"


def test_update_user_explicit_null_role_keeps_admin() -> None:
    user = _admin()

    user_repo.update_user(
        session=_FlushSession(),  # type: ignore[arg-type]
        db_user=user,
        user_in=UserUpdate(role=None),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
    assert user.token_version == 3


def test_update_user_promote_to_admin_sets_superuser() -> None:
    user = User(
        email="t@example.com", hashed_password="x", role=UserRole.teacher,
        token_version=0,
    )

    user_repo.update_user(
        session=_FlushSession(),  # type: ignore[arg-type]
        db_user=user,
        user_in=UserUpdate(role=UserRole.admin),
    )

    assert user.role == UserRole.admin
    assert user.is_superuser is True
    assert user.token_version == 0


# ─── LDAP 群組 DN 可以清空 ───────────────────────────────────────────


class _LdapSession:
    def __init__(self, config: LdapConfig) -> None:
        self.config = config

    def get(self, _model: Any, _pk: Any) -> LdapConfig:
        return self.config

    def add(self, _obj: Any) -> None:
        """測試替身。"""

    def commit(self) -> None:
        """測試替身。"""

    def refresh(self, _obj: Any) -> None:
        """測試替身。"""


def _ldap_config() -> LdapConfig:
    return LdapConfig(
        id=1,
        enabled=True,
        server_uri="ldaps://ldap.example.com",
        teacher_group_dn="cn=teachers,dc=example,dc=com",
        admin_group_dn="cn=admins,dc=example,dc=com",
    )


@pytest.mark.parametrize("cleared", [None, ""])
def test_update_ldap_config_clears_group_dn(cleared: str | None) -> None:
    config = _ldap_config()

    ldap_config_repo.update_ldap_config(
        session=_LdapSession(config),  # type: ignore[arg-type]
        data={"admin_group_dn": cleared},
    )

    assert config.admin_group_dn is None
    # 沒送的欄位維持原值
    assert config.teacher_group_dn == "cn=teachers,dc=example,dc=com"


def test_update_ldap_config_none_still_means_unchanged_for_other_fields() -> None:
    config = _ldap_config()

    ldap_config_repo.update_ldap_config(
        session=_LdapSession(config),  # type: ignore[arg-type]
        data={"server_uri": None, "teacher_group_dn": "cn=staff,dc=example,dc=com"},
    )

    assert config.server_uri == "ldaps://ldap.example.com"
    assert config.teacher_group_dn == "cn=staff,dc=example,dc=com"


# ─── 殭屍任務訊息寫出原本卡住的狀態 ───────────────────────────────────


def test_reap_stale_task_records_names_previous_state() -> None:
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    lost_running = TaskRecord(
        task_type="resource.reset", user_id=uuid.uuid4(), payload="{}",
        status=TaskRecordStatus.running, started_at=now - timedelta(hours=3),
    )
    # queued 但 started_at 有值（例如重新入列）：時數仍要依 queued 計
    lost_queued = TaskRecord(
        task_type="template.clone", user_id=uuid.uuid4(), payload="{}",
        status=TaskRecordStatus.queued, created_at=now - timedelta(days=2),
        started_at=now - timedelta(days=2),
    )

    class _Result:
        def all(self) -> list[TaskRecord]:
            return [lost_running, lost_queued]

    class _Session:
        def exec(self, _statement: Any) -> _Result:
            return _Result()

        def add(self, _obj: Any) -> None:
            """測試替身。"""

        def commit(self) -> None:
            """測試替身。"""

    reaped = task_record_repo.reap_stale_task_records(session=_Session(), now=now)  # type: ignore[arg-type]

    assert reaped == 2
    assert lost_running.status == TaskRecordStatus.failed
    assert lost_queued.status == TaskRecordStatus.failed
    assert "still running after 2h" in (lost_running.error or "")
    assert "still queued after 24h" in (lost_queued.error or "")
