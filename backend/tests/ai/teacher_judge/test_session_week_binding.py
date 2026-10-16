"""一週一個對話：建立、移動、複製都要指定還沒有對話的週次。"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.ai.teacher_judge.schemas import (
    TeacherJudgeScriptSetRunRequest,
    TeacherJudgeSessionForkRequest,
    TeacherJudgeSessionUpdateRequest,
)
from app.ai.teacher_judge.session_service import ensure_week_published
from app.api.routes import teacher_judge_sessions as routes
from app.models import TeachingClassWeek
from app.models.teacher_judge_session import TeacherJudgeSession
from tests.ai.teacher_judge.helpers import make_session, make_teacher_judge_file

USER = SimpleNamespace(id=uuid.uuid4())


@pytest.fixture
def ctx(monkeypatch: pytest.MonkeyPatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        weeks = []
        for number, title in [(1, "Linux 權限"), (2, ""), (3, "網站部署")]:
            week = TeachingClassWeek(
                class_id=class_id,
                week_number=number,
                session_date=date(2026, 9, number * 7),
                title=title,
            )
            db.add(week)
            weeks.append(week)
        db.flush()
        taken = TeacherJudgeSession(
            teaching_class_id=class_id, teaching_class_week_id=weeks[0].id, title="第一週"
        )
        db.add(taken)
        db.commit()
        monkeypatch.setattr(routes, "_access", lambda *args: None)
        monkeypatch.setattr(
            routes,
            "create_blank_file",
            lambda **kwargs: make_teacher_judge_file(db, class_id),
        )
        yield db, class_id, weeks, taken


def test_week_is_required(ctx) -> None:
    db, class_id, _, _ = ctx
    with pytest.raises(HTTPException) as exc:
        routes._require_free_week(db, class_id, None)
    assert exc.value.status_code == 400


def test_week_must_belong_to_the_class(ctx) -> None:
    db, _, weeks, _ = ctx
    with pytest.raises(HTTPException) as exc:
        routes._require_free_week(db, uuid.uuid4(), weeks[1].id)
    assert exc.value.status_code == 400


def test_a_week_with_a_conversation_is_taken(ctx) -> None:
    db, class_id, weeks, taken = ctx
    with pytest.raises(HTTPException) as exc:
        routes._require_free_week(db, class_id, weeks[0].id)
    assert exc.value.status_code == 409
    # 自己原本就在那一週不算衝突（例如改名時一起送週次）
    routes._require_free_week(db, class_id, weeks[0].id, exclude_session_id=taken.id)


def test_ensure_week_session_creates_once_and_then_returns_the_same(ctx) -> None:
    db, class_id, weeks, _ = ctx

    first = routes.ensure_week_session(class_id, weeks[2].id, db, USER)
    second = routes.ensure_week_session(class_id, weeks[2].id, db, USER)

    assert first.id == second.id
    assert first.title == "網站部署"
    assert first.teaching_class_week_id == str(weeks[2].id)
    rows = db.exec(
        select(TeacherJudgeSession).where(
            TeacherJudgeSession.teaching_class_week_id == weeks[2].id
        )
    ).all()
    assert len(rows) == 1


def test_ensure_week_session_names_untitled_weeks_by_number(ctx) -> None:
    db, class_id, weeks, _ = ctx
    created = routes.ensure_week_session(class_id, weeks[1].id, db, USER)
    assert "2" in created.title


def test_ensure_week_session_returns_the_existing_conversation(ctx) -> None:
    db, class_id, weeks, taken = ctx
    result = routes.ensure_week_session(class_id, weeks[0].id, db, USER)
    assert result.id == str(taken.id)


def test_moving_into_a_taken_week_is_rejected(ctx) -> None:
    db, class_id, weeks, _ = ctx
    other = TeacherJudgeSession(
        teaching_class_id=class_id, teaching_class_week_id=weeks[2].id, title="第三週"
    )
    db.add(other)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        routes.update_session(
            class_id,
            other.id,
            TeacherJudgeSessionUpdateRequest(teaching_class_week_id=weeks[0].id),
            db,
            USER,
        )
    assert exc.value.status_code == 409


def test_fork_must_target_an_empty_week(ctx) -> None:
    db, class_id, weeks, taken = ctx
    with pytest.raises(HTTPException) as exc:
        routes.fork_session(
            class_id,
            taken.id,
            TeacherJudgeSessionForkRequest(teaching_class_week_id=weeks[0].id),
            db,
            USER,
        )
    assert exc.value.status_code == 409

    copy = routes.fork_session(
        class_id,
        taken.id,
        TeacherJudgeSessionForkRequest(teaching_class_week_id=weeks[1].id),
        db,
        USER,
    )
    assert copy.teaching_class_week_id == str(weeks[1].id)


def test_checks_only_run_after_the_week_is_published(ctx) -> None:
    """作業跟著老師每週的內容發布：發布前不能檢查學生。"""
    db, class_id, weeks, taken = ctx
    with pytest.raises(HTTPException) as exc:
        routes.create_session_script_set_run(
            class_id, taken.id, uuid.uuid4(), TeacherJudgeScriptSetRunRequest(), db, USER
        )
    assert exc.value.status_code == 409

    weekless = TeacherJudgeSession(teaching_class_id=class_id, title="沒有週次")
    db.add(weekless)
    db.commit()
    with pytest.raises(HTTPException) as exc:
        ensure_week_published(db, weekless)
    assert exc.value.status_code == 409

    for status in ("published", "completed"):
        weeks[0].status = status
        db.add(weeks[0])
        db.commit()
        ensure_week_published(db, taken)
