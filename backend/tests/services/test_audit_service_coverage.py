"""app.services.user.audit_service 的查詢與統計函式補測（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AuditAction, User
from app.services.user import audit_service as svc


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _user(db: Session, email: str, full_name: str | None = None) -> User:
    user = User(email=email, hashed_password="x", full_name=full_name)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_get_stats_wraps_repository_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_stats(**kwargs: Any) -> dict[str, int]:
        seen.append(kwargs)
        return {"total": 5, "danger": 1, "login_failed": 2, "active_users": 3}

    monkeypatch.setattr(svc.audit_repo, "get_audit_stats", fake_stats)
    stats = svc.get_stats(session=None)
    assert (stats.total, stats.danger, stats.login_failed, stats.active_users) == (
        5,
        1,
        2,
        3,
    )
    assert seen == [{"session": None, "start_time": None, "end_time": None}]


def test_list_action_metas_covers_every_action() -> None:
    metas = {meta.value: meta.category for meta in svc.list_action_metas()}
    assert set(metas) == {action.value for action in AuditAction}
    assert metas[AuditAction.login_success.value] == "auth"
    uncategorised = [a for a in AuditAction if a not in svc.ACTION_CATEGORY]
    if uncategorised:
        assert metas[uncategorised[0].value] == "other"


def test_list_audit_users_and_get_all(db: Session) -> None:
    actor = _user(db, "b-actor@example.com", "Actor")
    _user(db, "a-silent@example.com")
    svc.log_action(
        session=db,
        user_id=actor.id,
        action=AuditAction.user_update,
        details="changed something",
        ip_address="10.0.0.1",
        user_agent="pytest",
    )
    svc.log_action(
        session=db,
        user_id=None,
        action=AuditAction.login_failed,
        details="anonymous failure",
        ip_address="10.0.0.2",
        user_agent="pytest",
    )

    options = svc.list_audit_users(session=db)
    assert [(o.id, o.email, o.full_name) for o in options] == [
        (actor.id, actor.email, "Actor")
    ]

    logs = svc.get_all(session=db)
    assert logs.count == 2
    by_details = {log.details: log for log in logs.data}
    assert by_details["changed something"].user_email == actor.email
    assert by_details["anonymous failure"].user_email is None
    assert isinstance(by_details["anonymous failure"].id, uuid.UUID)
