"""auth_service 的同步 DB 步驟不可在 event loop 上執行，以及 logout 撤銷邏輯。

refresh_access_token／google_login 是 async，直接呼叫同步 Session 會佔住
event loop，DB 連線池一緊就整個 worker 卡死（deps.get_current_user 也是用
run_in_threadpool 避開）。這裡記錄同步步驟執行時的 thread，確認都不在跑測試
coroutine 的 event loop thread 上。
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

import app.infrastructure.redis as redis_module
from app.core import security
from app.exceptions import BadRequestError
from app.schemas import Token
from app.services.user import auth_service

_USER_ID = uuid.uuid4()


def _stub_user(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "id": _USER_ID,
        "email": "offloop@example.com",
        "is_active": True,
        "token_version": 0,
        "totp_enabled": False,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _RecordingSession:
    def __init__(self, user: Any, log: dict[str, int]) -> None:
        self._user = user
        self._log = log

    def get(self, model: Any, key: Any) -> Any:
        self._log["session.get"] = threading.get_ident()
        return self._user


def _patch_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_redis() -> None:
        return None

    async def fake_is_jti_revoked(redis: Any, jti: str) -> bool:
        return False

    async def fake_mark_refresh_token_used(redis: Any, jti: str, exp: int) -> bool:
        return True

    monkeypatch.setattr(redis_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(redis_module, "is_jti_revoked", fake_is_jti_revoked)
    monkeypatch.setattr(
        redis_module, "mark_refresh_token_used", fake_mark_refresh_token_used
    )


async def test_refresh_access_token_reads_user_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_redis(monkeypatch)
    log: dict[str, int] = {}
    session = _RecordingSession(_stub_user(), log)
    refresh_token = security.create_refresh_token(
        _USER_ID, expires_delta=timedelta(days=1), token_version=0
    )

    token = await auth_service.refresh_access_token(
        session=session,  # type: ignore[arg-type]
        refresh_token=refresh_token,
    )

    assert isinstance(token, Token)
    assert log["session.get"] != threading.get_ident()


@pytest.fixture
def google_configured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    log: dict[str, Any] = {"audit": []}
    monkeypatch.setattr(auth_service.settings, "GOOGLE_CLIENT_ID", "google-client")

    async def fake_fetch(id_token: str) -> dict[str, str]:
        return {
            "aud": "google-client",
            "email": "offloop@example.com",
            "email_verified": "true",
        }

    def fake_log_action(**kwargs: Any) -> None:
        log["audit"].append((kwargs["action"], threading.get_ident()))

    monkeypatch.setattr(auth_service, "fetch_id_token_info", fake_fetch)
    monkeypatch.setattr(auth_service.audit_service, "log_action", fake_log_action)
    return log


async def test_google_login_db_work_runs_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, google_configured: dict[str, Any]
) -> None:
    log = google_configured

    def fake_get_user_by_email(*, session: Any, email: str) -> Any:
        log["get_user_by_email"] = threading.get_ident()
        return _stub_user()

    monkeypatch.setattr(
        auth_service.user_repo, "get_user_by_email", fake_get_user_by_email
    )

    result = await auth_service.google_login(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        id_token="valid-google-token",
    )

    loop_thread = threading.get_ident()
    assert isinstance(result, Token)
    assert log["get_user_by_email"] != loop_thread
    assert [a for a, _ in log["audit"]] == [
        auth_service.AuditAction.login_google_success
    ]
    assert all(tid != loop_thread for _, tid in log["audit"])


async def test_google_login_failure_audit_runs_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, google_configured: dict[str, Any]
) -> None:
    log = google_configured

    async def fake_fetch(id_token: str) -> dict[str, str]:
        return {"aud": "someone-else", "email": "x@example.com"}

    monkeypatch.setattr(auth_service, "fetch_id_token_info", fake_fetch)

    with pytest.raises(BadRequestError):
        await auth_service.google_login(
            session=SimpleNamespace(),  # type: ignore[arg-type]
            id_token="foreign-token",
        )

    loop_thread = threading.get_ident()
    assert [a for a, _ in log["audit"]] == [
        auth_service.AuditAction.login_google_failed
    ]
    assert all(tid != loop_thread for _, tid in log["audit"])


async def test_logout_revokes_access_and_refresh_jti(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revoked: list[str] = []

    async def fake_get_redis() -> None:
        return None

    async def fake_revoke_jti(redis: Any, jti: str, exp: int) -> bool:
        revoked.append(jti)
        return True

    monkeypatch.setattr(redis_module, "get_redis", fake_get_redis)
    monkeypatch.setattr(redis_module, "revoke_jti", fake_revoke_jti)

    # 已過期的 token 也要能登出（不驗效期），亂碼 token 直接略過
    access = security.create_access_token(
        _USER_ID, expires_delta=timedelta(minutes=-5)
    )
    refresh = security.create_refresh_token(_USER_ID, expires_delta=timedelta(days=1))

    await auth_service.logout(access, refresh)
    assert len(revoked) == 2

    revoked.clear()
    await auth_service.logout(access, "not-a-jwt")
    assert len(revoked) == 1

    revoked.clear()
    await auth_service.logout("not-a-jwt", None)
    assert revoked == []
