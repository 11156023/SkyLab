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

# 本機 .env 填了 Cloudflare Turnstile 金鑰時，登入／註冊端點會要求機器人驗證
# token，直接打這些端點的測試會一律拿到 400（CI 沒有 .env 所以不受影響）。
# 測試預設關閉；要測驗證本身的案例自己用 monkeypatch 開（test_turnstile_service）。
settings.TURNSTILE_SITE_KEY = None
settings.TURNSTILE_SECRET_KEY = None

from app.core.db import engine, ensure_first_superuser, init_db
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


@pytest.fixture(autouse=True)
def _clear_proxmox_caches() -> Generator[None, None, None]:
    """PVE 設定、叢集清單、即時 IP 與近期任務是行程內 TTL 快取：每個測試各自 mock，不能吃到上一個測試的結果。"""
    from app.infrastructure.proxmox.operations import invalidate_cluster_resources_cache
    from app.infrastructure.proxmox.settings import invalidate_proxmox_settings_cache
    from app.services.jobs.jobs_service import clear_recent_jobs_cache
    from app.services.resource.live_ip import clear_live_ip_cache

    invalidate_proxmox_settings_cache()
    invalidate_cluster_resources_cache()
    clear_recent_jobs_cache()
    clear_live_ip_cache()
    yield


@pytest.fixture(autouse=True)
def _isolate_system_ai_adherence_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """既有業務單元測試不額外消耗第二組模型回覆。

    檢查器本身與各服務的 block/fail-closed 整合由 focused tests 覆蓋；個別測試仍可
    在此 fixture 之後覆寫模組內的 ``check_adherence``。
    """
    from app.ai.contextual_help import service as contextual_help_service
    from app.ai.pve_log import chat as pve_chat
    from app.ai.role_contracts import (
        AdherenceReason,
        AdherenceResult,
        AdherenceVerdict,
    )
    from app.ai.teacher_judge import service as teacher_judge_service
    from app.api.routes import ai_template_recommendation

    async def allow(*_args, **_kwargs) -> AdherenceResult:
        return AdherenceResult(AdherenceVerdict.ALLOW, AdherenceReason.NONE)

    monkeypatch.setattr(contextual_help_service, "check_adherence", allow)
    monkeypatch.setattr(pve_chat, "check_adherence", allow)
    if hasattr(teacher_judge_service, "check_adherence"):
        monkeypatch.setattr(teacher_judge_service, "check_adherence", allow)
    monkeypatch.setattr(ai_template_recommendation, "check_adherence", allow)


@pytest.fixture(autouse=True)
def _no_backup_purge_on_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """resource_service.delete 成功後會順手清掉機器的備份（要查連線設定、打 PVE）。

    一般測試只 mock 刪除本身，不該因此多連一次資料庫或 PVE；要測這個掛鉤的案例
    自己再換一個替身（見 test_delete_running_resource）。
    """
    from app.services.resource import resource_service

    monkeypatch.setattr(
        resource_service, "_purge_backups_best_effort", lambda **kwargs: None
    )


@pytest.fixture(autouse=True)
def _no_qemu_ssh_login_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """QEMU 開機後的 sshd 設定在背景執行緒等 guest agent（會打 PVE）。

    一般測試只 mock 開機本身；背景工作換成什麼都不做，test_guest_ssh_login 自己測它。
    """
    from app.services.resource import guest_ssh_login

    monkeypatch.setattr(
        guest_ssh_login,
        "_run_qemu",
        lambda node, vmid: guest_ssh_login._pending.discard((node, vmid)),
    )


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


def _uses_application_db(item) -> bool:
    definitions = getattr(
        getattr(item, "_fixtureinfo", None), "name2fixturedefs", {}
    ).get("db", ())
    return bool(definitions and definitions[-1].func is db.__wrapped__)


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
    _assert_safe_pytest_database_target()
    with Session(engine) as session:
        init_db(session)
        ensure_first_superuser(session)
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
def _seed_first_superuser(request: pytest.FixtureRequest) -> None:
    """Ensure FIRST_SUPERUSER exists and its password matches settings.

    Runs only when a collected test resolves the guarded application DB fixture.
    If the superuser already exists but the stored password doesn't verify
    against FIRST_SUPERUSER_PASSWORD (e.g. a shared dev DB), the hash is
    refreshed so auth-dependent tests can log in deterministically.
    """
    # Pure unit suites must never seed the configured application database.
    # Check the target before the first Session, including this autouse path.
    if not any(_uses_application_db(item) for item in request.session.items):
        return
    _assert_safe_pytest_database_target()
    from app.core.security import get_password_hash, verify_password

    with Session(engine) as session:
        init_db(session)
        ensure_first_superuser(session)
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


@pytest.fixture(autouse=True)
def _block_unrequested_application_db(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    if _uses_application_db(request.node):
        yield
        return

    def blocked_connect(*_args, **_kwargs):
        raise RuntimeError(
            "Unit tests must use an isolated engine; application DB access requires the guarded db fixture"
        )

    monkeypatch.setattr(engine, "connect", blocked_connect)
    from app.api import ai_capacity
    from app.services.llm_gateway import usage_writer

    writer = usage_writer.UsageWriter()
    monkeypatch.setattr(usage_writer, "_writer", writer)
    monkeypatch.setattr(ai_capacity, "_executor", None)
    monkeypatch.setattr(ai_capacity, "_pending", set())
    monkeypatch.setattr(ai_capacity, "_closed", False)
    try:
        yield
    finally:
        # Drain before monkeypatch restores engine.connect or mocked recorders.
        # No detached accounting work may escape a unit test's DB guard.
        current = usage_writer.get_usage_writer()
        current.executor.shutdown(wait=True, cancel_futures=True)
        if current is not writer:
            writer.executor.shutdown(wait=True, cancel_futures=True)
        if ai_capacity._executor is not None:
            ai_capacity._executor.shutdown(wait=True, cancel_futures=True)


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
