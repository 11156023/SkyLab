"""一次性清理指令：只動「沒有週次」與「同週多個對話」的 AI導師檢查對話。"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app import cleanup_judge_sessions as cleanup_module
from app.models import (
    TeachingClass,
    TeachingClassStatus,
    TeachingClassWeek,
    User,
    UserRole,
)
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_session import TeacherJudgeSession


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    # 清理對象是升級前的舊資料：那時還沒有「一週一個對話」的唯一鍵
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_teacher_judge_sessions_week")
    monkeypatch.setattr(cleanup_module, "engine", engine)
    return engine


def _seed(engine) -> dict[str, object]:
    with Session(engine) as session:
        teacher = User(
            email="teacher@example.edu", role=UserRole.teacher, hashed_password="x"
        )
        teaching_class = TeachingClass(
            name="Linux 實務",
            code="linux-a",
            term="2026-1",
            location="A",
            owner_id=teacher.id,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 31),
            weekday=1,
            start_time=time(9),
            end_time=time(11),
            status=TeachingClassStatus.active,
        )
        session.add(teacher)
        session.add(teaching_class)
        session.flush()
        week_one = TeachingClassWeek(
            class_id=teaching_class.id, week_number=1, session_date=date(2026, 9, 1)
        )
        week_two = TeachingClassWeek(
            class_id=teaching_class.id, week_number=2, session_date=date(2026, 9, 8)
        )
        session.add(week_one)
        session.add(week_two)
        session.flush()

        def judge(title: str, week: TeachingClassWeek | None) -> TeacherJudgeSession:
            row = TeacherJudgeSession(
                teaching_class_id=teaching_class.id,
                teaching_class_week_id=week.id if week else None,
                title=title,
            )
            session.add(row)
            session.flush()
            return row

        keep = judge("第一週", week_one)
        orphan = judge("沒有週次", None)
        dup_a = judge("第二週 A", week_two)
        dup_b = judge("第二週 B", week_two)
        session.add(
            TeacherJudgeScriptArtifact(
                teaching_class_id=teaching_class.id,
                session_id=dup_a.id,
                name="第二週 A",
                template_key="linux",
                rubric_snapshot_json={"items": []},
                script_content="print('{}')",
                status=TeacherJudgeScriptStatus.approved,
            )
        )
        session.commit()
        return {
            "class_id": teaching_class.id,
            "keep": keep.id,
            "orphan": orphan.id,
            "dup_a": dup_a.id,
            "dup_b": dup_b.id,
        }


def test_finds_sessions_without_week_and_every_session_of_a_duplicated_week(engine) -> None:
    ids = _seed(engine)
    with Session(engine) as session:
        targets = cleanup_module.find_targets(session)

    by_id = {target.session_id: target for target in targets}
    assert set(by_id) == {ids["orphan"], ids["dup_a"], ids["dup_b"]}
    assert by_id[ids["orphan"]].reason == cleanup_module.REASON_NO_WEEK
    assert by_id[ids["dup_a"]].reason == cleanup_module.REASON_DUPLICATE_WEEK
    # 乾跑清單要讓人看得出刪掉會影響已核准的腳本
    assert by_id[ids["dup_a"]].approved_scripts == 1
    assert by_id[ids["dup_a"]].week_number == 2


def test_dry_run_deletes_nothing(engine) -> None:
    ids = _seed(engine)
    assert cleanup_module.main([]) == 0
    with Session(engine) as session:
        remaining = set(session.exec(select(TeacherJudgeSession.id)).all())
    assert remaining == {ids["keep"], ids["orphan"], ids["dup_a"], ids["dup_b"]}


def test_apply_deletes_only_violating_sessions(engine) -> None:
    ids = _seed(engine)
    assert cleanup_module.main(["--apply"]) == 0
    with Session(engine) as session:
        remaining = set(session.exec(select(TeacherJudgeSession.id)).all())
        scripts = session.exec(select(TeacherJudgeScriptArtifact)).all()
    assert remaining == {ids["keep"]}
    assert scripts == []
    # 再跑一次沒有東西可清
    with Session(engine) as session:
        assert cleanup_module.find_targets(session) == []


def test_class_filter_limits_scope(engine) -> None:
    _seed(engine)
    other = "00000000-0000-0000-0000-000000000001"
    assert cleanup_module.main(["--class", other, "--apply"]) == 0
    with Session(engine) as session:
        assert len(session.exec(select(TeacherJudgeSession)).all()) == 4
