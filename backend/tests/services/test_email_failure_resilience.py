"""寄信失敗不能讓已完成的帳號操作變成 500。

2026-11-06 ntub 正式站實測：SMTP 送信失敗時，管理員「新增使用者」回 500 但帳號其實已建立
（再按一次變 409），「忘記密碼」也回 500。寄不出信只能記 ERROR，不能把請求整個弄壞。
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models import UserRole
from app.schemas.user import UserCreate
from app.services.user import auth_service, user_service


def _smtp_down(**_kwargs) -> None:
    raise ConnectionRefusedError("smtp down")


@pytest.fixture
def smtp_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "noreply@example.test")


def _error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_create_user_returns_created_user_when_welcome_mail_fails(
    smtp_on, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    created = SimpleNamespace(
        id=uuid.uuid4(), email="qa.student@example.com", role=UserRole.student
    )
    monkeypatch.setattr(
        user_service.user_repo, "get_user_by_email", lambda *, session, email: None
    )
    monkeypatch.setattr(
        user_service.user_repo,
        "create_user",
        lambda *, session, user_create: created,
    )
    monkeypatch.setattr(user_service.audit_service, "log_action", lambda **kw: None)
    monkeypatch.setattr(user_service, "_commit_and_refresh", lambda session, user: user)
    monkeypatch.setattr(user_service, "send_email", _smtp_down)

    with caplog.at_level(logging.ERROR):
        user = user_service.create_user(
            session=object(),  # type: ignore[arg-type]
            user_in=UserCreate(
                email="qa.student@example.com",
                password="Valid!Pass123",
                role=UserRole.student,
            ),
            current_user_id=uuid.uuid4(),
        )

    assert user is created
    errors = _error_records(caplog)
    assert errors, "寄信失敗必須記 ERROR，不能無聲吞掉"
    assert any("qa.student@example.com" in r.getMessage() for r in errors)


def test_recover_password_logs_instead_of_raising_when_mail_fails(
    smtp_on, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email="qa.student@example.com",
        auth_source="local",
        token_version=1,
        deleted_at=None,
    )
    monkeypatch.setattr(
        auth_service.user_repo, "get_user_by_email", lambda *, session, email: user
    )
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kw: None)
    monkeypatch.setattr(
        auth_service, "generate_password_reset_token", lambda **kw: "reset-token"
    )
    monkeypatch.setattr(
        auth_service,
        "generate_reset_password_email",
        lambda **kw: SimpleNamespace(subject="reset", html_content="<p>reset</p>"),
    )
    monkeypatch.setattr(auth_service, "send_email", _smtp_down)

    with caplog.at_level(logging.ERROR):
        auth_service.recover_password(
            session=object(),  # type: ignore[arg-type]
            email="qa.student@example.com",
        )

    errors = _error_records(caplog)
    assert errors, "寄信失敗必須記 ERROR，不能無聲吞掉"
    assert any("qa.student@example.com" in r.getMessage() for r in errors)
