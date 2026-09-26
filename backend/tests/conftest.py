import os
from collections.abc import Generator

# Disable background jobs that reach external systems (Proxmox API, etc.)
# before any app module is imported — otherwise settings caches the default.
# CI runners cannot reach Proxmox, so scheduler ticks would time out and
# block TestClient startup.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

# Manual smoke scripts that use a `test_` prefix for CLI ergonomics but are not
# pytest tests. Excluding them prevents pytest from collecting their top-level
# functions (which take positional CLI args, not fixtures).
collect_ignore = ["test_ai_api.py"]

from app.core.config import settings

# 本機 .env 可能填了正式的 SENTRY_DSN：測試會刻意製造例外（排程任務失敗、
# PVE 連不上…），不能送進真的 Sentry 專案。必須在 import app.main（會呼叫
# init_sentry）之前關掉；env_ignore_empty=True 讓空字串環境變數蓋不掉 .env。
settings.SENTRY_DSN = None

from app.core.db import engine, init_db
from app.main import app
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    FirewallLayout,
    Resource,
    SpecChangeRequest,
    User,
    VMRequest,
)
from tests.utils.user import authentication_token_from_email
from tests.utils.utils import get_superuser_token_headers


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _assert_safe_pytest_database_target() -> None:
    """
    Guard rail: refuse running DB-backed tests against non-test-like databases.

    Override only when you explicitly know what you are doing:
    PYTEST_ALLOW_NON_TEST_DB=1
    """
    if _is_truthy_env(os.getenv("PYTEST_ALLOW_NON_TEST_DB")):
        return

    host = settings.POSTGRES_SERVER.strip().lower()
    db_name = settings.POSTGRES_DB.strip().lower()

    # docker compose test stack hosts (isolated service network); backend
    # containers reach Postgres through PgBouncer.
    if host in {"db", "pgbouncer"}:
        return

    # local hosts are only allowed when DB name clearly indicates test usage
    if host in {"localhost", "127.0.0.1", "::1"} and any(
        token in db_name for token in ("test", "pytest", "ci")
    ):
        return

    raise RuntimeError(
        "Refusing to run pytest DB fixture on a non-test database target. "
        "Set PYTEST_ALLOW_NON_TEST_DB=1 to override explicitly."
    )


def _is_test_user_email(email: str) -> bool:
    lowered = email.strip().lower()
    if lowered == str(settings.EMAIL_TEST_USER).strip().lower():
        return True

    # Reserved test domains and common prefixes used across this test suite.
    if lowered.endswith(("@example.com", "@example.org", "@example.net")):
        return True
    return lowered.startswith(("test-", "pytest-", "ai-api-", "user-", "admin-"))


@pytest.fixture(scope="session")
def db() -> Generator[Session, None, None]:
    """
    Session-scoped DB fixture that connects to the real PostgreSQL.

    IMPORTANT: autouse is intentionally set to False (default) so this
    fixture only activates for tests that explicitly declare `db` as a
    parameter. This prevents the teardown logic from running automatically
    and wiping the remote database on every pytest invocation.

    Safety strategy:
    - Hard guard to prevent DB-backed tests from accidentally running
      against non-test-like DB targets.
    - Cleanup is opt-in via PYTEST_ENABLE_DB_CLEANUP=1.
    - FIRST_SUPERUSER account is always preserved.
    """
    with Session(engine) as session:
        _assert_safe_pytest_database_target()
        init_db(session)
        yield session
        session.rollback()
        if _is_truthy_env(os.getenv("PYTEST_ENABLE_DB_CLEANUP")):
            _cleanup_test_data(session)


def _cleanup_test_data(session: Session) -> None:
    """
    Remove only the data that was created during the test run.

    Rules:
    - Never delete the FIRST_SUPERUSER account.
    - Delete non-superuser test users and their owned rows only.
    - AuditLog is NOT manually deleted: its user_id FK is ondelete=SET NULL,
      so the DB handles nullification automatically when the user is deleted.
      Audit history is preserved intentionally.
    - Only rows whose user_id is explicitly in test_user_ids are deleted,
      avoiding accidental removal of rows with NULL user_id.
    """
    from sqlmodel import col

    # Collect identifiable test-created users while preserving all superusers.
    candidate_users = session.exec(
        select(User).where(User.email != settings.FIRST_SUPERUSER)
    ).all()
    test_users = [
        user
        for user in candidate_users
        if (not user.is_superuser) and _is_test_user_email(str(user.email))
    ]
    test_user_ids = list({u.id for u in test_users})

    if not test_user_ids:
        return  # Nothing to clean up

    # Models with user_id FK (NOT NULL) — delete rows owned by test users only.
    # AuditLog is excluded: ondelete=SET NULL means DB nullifies user_id automatically.
    user_owned_models: list[type] = [
        FirewallLayout,   # user_id NOT NULL, FK → user.id
        AIAPICredential,  # user_id NOT NULL
        AIAPIRequest,     # user_id NOT NULL
        SpecChangeRequest,
        VMRequest,
        Resource,
    ]
    for model in user_owned_models:
        rows = session.exec(  # type: ignore[call-overload]
            select(model).where(col(model.user_id).in_(test_user_ids))  # type: ignore[attr-defined]
        ).all()
        for row in rows:
            session.delete(row)
    session.flush()

    # Delete the test users themselves (AuditLog.user_id becomes NULL via DB cascade)
    for user in test_users:
        session.delete(user)

    session.commit()


@pytest.fixture(scope="session", autouse=True)
def _seed_first_superuser() -> None:
    """Ensure FIRST_SUPERUSER exists and its password matches settings.

    Runs once per session regardless of whether a test uses the `db` fixture.
    If the superuser already exists but the stored password doesn't verify
    against FIRST_SUPERUSER_PASSWORD (e.g. a shared dev DB), the hash is
    refreshed so auth-dependent tests can log in deterministically.
    """
    from app.core.security import get_password_hash, verify_password

    with Session(engine) as session:
        init_db(session)
        user = session.exec(
            select(User).where(User.email == settings.FIRST_SUPERUSER)
        ).first()
        if user is not None:
            pw_ok, _ = verify_password(
                settings.FIRST_SUPERUSER_PASSWORD, user.hashed_password
            )
            if not pw_ok:
                user.hashed_password = get_password_hash(
                    settings.FIRST_SUPERUSER_PASSWORD
                )
                session.add(user)
                session.commit()


@pytest.fixture(scope="module")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def superuser_token_headers(client: TestClient) -> dict[str, str]:
    return get_superuser_token_headers(client)


@pytest.fixture(scope="module")
def normal_user_token_headers(client: TestClient, db: Session) -> dict[str, str]:
    return authentication_token_from_email(
        client=client, email=settings.EMAIL_TEST_USER, db=db
    )
