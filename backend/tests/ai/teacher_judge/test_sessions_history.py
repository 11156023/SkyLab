"""Split from tests/test_teacher_judge_sessions.py: bounded history, focus & summary workers.

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai.teacher_judge import session_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeSessionUpdateRequest,
)
from app.api.routes import teacher_judge_sessions
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
)


def test_bounded_history_keeps_latest_messages_in_stable_order() -> None:
    db = make_session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="History")
    db.add(item)
    db.commit()
    db.refresh(item)
    started_at = datetime(2026, 7, 31, tzinfo=timezone.utc)
    for index in range(25):
        db.add(
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=(
                    TeacherJudgeMessageRole.user
                    if index % 2 == 0
                    else TeacherJudgeMessageRole.assistant
                ),
                content=f"message-{index:02d}",
                created_at=started_at + timedelta(seconds=index),
            )
        )
    db.commit()

    history = session_service.bounded_history(db, item.id)

    assert len(history) == session_service.HISTORY_MESSAGE_LIMIT
    assert history[0].content == "message-05"
    assert history[-1].content == "message-24"


def test_bounded_history_includes_summary_before_newer_messages() -> None:
    db = make_session()
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(),
        title="History summary",
        summary="老師已決定只檢查 Python 執行結果。",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.user,
                content="請保留這個方向",
                created_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content="好的，會保留。",
                created_at=datetime(2026, 8, 2, 0, 0, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    db.commit()

    history = session_service.bounded_history(db, item.id, summary=item.summary)

    assert history[0].role == "assistant"
    assert "只檢查 Python 執行結果" in history[0].content
    assert history[-1].content == "好的，會保留。"


def test_bounded_history_injects_latest_focus_for_same_source_only() -> None:
    db = make_session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Focus")
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content="請補充範圍。",
                metadata_json={
                    "conversation_focus": {
                        "source_file_id": str(source_id),
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "resource-usage",
                                "status": "needs_information",
                                "known_information": ["整台 VM"],
                                "missing_information": ["目前或一段期間"],
                            }
                        ],
                    }
                },
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.user,
                content="看目前就好",
            ),
        ]
    )
    db.commit()

    same_source = session_service.bounded_history(db, item.id, source_file_id=source_id)
    other_source = session_service.bounded_history(
        db, item.id, source_file_id=uuid.uuid4()
    )

    assert any("resource-usage" in message.content for message in same_source)
    assert not any("resource-usage" in message.content for message in other_source)


def test_bounded_history_skips_resolved_requirements_in_focus() -> None:
    db = make_session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Focus resolved")
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="已建立提案。",
            metadata_json={
                "conversation_focus": {
                    "source_file_id": str(source_id),
                    "turn_kind": "requirement",
                    "requirements": [
                        {
                            "focus_key": "applied-item",
                            "status": "ready",
                            "known_information": ["檢查磁碟空間"],
                            "missing_information": [],
                        },
                        {
                            "focus_key": "pending-item",
                            "status": "needs_information",
                            "known_information": [],
                            "missing_information": ["服務名稱"],
                        },
                    ],
                }
            },
        )
    )
    db.commit()

    history = session_service.bounded_history(db, item.id, source_file_id=source_id)

    focus_messages = [
        message for message in history if "目前未解需求焦點" in message.content
    ]
    assert len(focus_messages) == 1
    assert "pending-item" in focus_messages[0].content
    assert "applied-item" not in focus_messages[0].content


def test_bounded_history_skips_focus_when_all_requirements_resolved() -> None:
    db = make_session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Focus all ready")
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="已建立提案。",
            metadata_json={
                "conversation_focus": {
                    "source_file_id": str(source_id),
                    "turn_kind": "requirement",
                    "requirements": [
                        {
                            "focus_key": "applied-item",
                            "status": "ready",
                            "known_information": ["檢查磁碟空間"],
                            "missing_information": [],
                        }
                    ],
                }
            },
        )
    )
    db.commit()

    history = session_service.bounded_history(db, item.id, source_file_id=source_id)

    assert not any("目前未解需求焦點" in message.content for message in history)


def test_bounded_history_latest_resolved_focus_does_not_revive_older_gap() -> None:
    db = make_session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(), title="Focus latest snapshot"
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    started_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content="舊缺口",
                created_at=started_at,
                metadata_json={
                    "conversation_focus": {
                        "source_file_id": str(source_id),
                        "analysis_revision": 4,
                        "requirements": [
                            {
                                "focus_key": "port",
                                "status": "needs_information",
                                "missing_information": ["Port"],
                            }
                        ],
                    }
                },
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content="已完成",
                created_at=started_at + timedelta(seconds=1),
                metadata_json={
                    "conversation_focus": {
                        "source_file_id": str(source_id),
                        "analysis_revision": 4,
                        "requirements": [
                            {
                                "focus_key": "workflow",
                                "status": "none",
                                "missing_information": [],
                            }
                        ],
                    }
                },
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.user,
                content="請繼續",
                created_at=started_at + timedelta(seconds=2),
            ),
        ]
    )
    db.commit()

    history = session_service.bounded_history(
        db,
        item.id,
        source_file_id=source_id,
        analysis_revision=4,
    )

    assert not any("目前未解需求焦點" in message.content for message in history)
    assert not any('"focus_key": "port"' in message.content for message in history)


def test_bounded_history_ignores_focus_from_another_revision() -> None:
    db = make_session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Focus revision")
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="舊版本缺口",
            metadata_json={
                "conversation_focus": {
                    "source_file_id": str(source_id),
                    "analysis_revision": "3",
                    "requirements": [
                        {
                            "focus_key": "old",
                            "status": "needs_information",
                            "missing_information": ["舊資料"],
                        }
                    ],
                }
            },
        )
    )
    db.commit()

    history = session_service.bounded_history(
        db,
        item.id,
        source_file_id=source_id,
        analysis_revision=4,
    )

    assert not any("目前未解需求焦點" in message.content for message in history)


def test_summary_persistence_is_monotonic_for_out_of_order_workers() -> None:
    db = make_session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Summary race")
    db.add(item)
    db.commit()
    db.refresh(item)
    started_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    messages: list[TeacherJudgeSessionMessage] = []
    for index in range(20):
        message = TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=f"assistant-{index}",
            created_at=started_at + timedelta(seconds=index),
        )
        messages.append(message)
        db.add(message)
    db.commit()
    for message in messages:
        db.refresh(message)

    older = session_service._prepare_summary_job(
        db,
        session_id=item.id,
        boundary_message_id=messages[9].id,
        assistant_count=10,
        selected_file_id=None,
        analysis_revision=None,
    )
    newer = session_service._prepare_summary_job(
        db,
        session_id=item.id,
        boundary_message_id=messages[19].id,
        assistant_count=20,
        selected_file_id=None,
        analysis_revision=None,
    )
    assert older is not None and newer is not None

    assert session_service._persist_summary_if_current(db, newer, "第 20 輪摘要")
    assert not session_service._persist_summary_if_current(db, older, "第 10 輪摘要")
    db.refresh(item)
    assert item.summary == "第 20 輪摘要"
    assert item.summary_through_message_id == messages[19].id
    assert item.summary_through_assistant_count == 20


def test_source_switch_clears_old_conversation_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    first_file = make_teacher_judge_file(db, class_id)
    second_file = TeacherJudgeFile(
        teaching_class_id=class_id,
        original_filename="rubric-second.pdf",
        file_hash="b" * 64,
        template_key="linux",
        analysis_json={"items": [], "summary": "second"},
    )
    db.add(second_file)
    db.commit()
    db.refresh(second_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Switch source",
        selected_file_id=first_file.id,
        summary="不要帶到新來源",
        summary_through_assistant_count=10,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="舊來源決定",
        )
    )
    db.commit()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    result = teacher_judge_sessions.update_session(
        class_id,
        item.id,
        TeacherJudgeSessionUpdateRequest(selected_file_id=second_file.id),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.selected_file_id == str(second_file.id)
    refreshed = db.get(TeacherJudgeSession, item.id)
    assert refreshed is not None
    assert refreshed.summary == ""
    assert refreshed.summary_through_message_id is None
    assert refreshed.summary_through_assistant_count == 0
    assert db.exec(select(TeacherJudgeSessionMessage)).all() == []


def test_schedule_summary_uses_stable_task_id_without_waiting_for_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Schedule")
    db.add(item)
    db.commit()
    db.refresh(item)
    messages = [
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=f"answer-{index}",
        )
        for index in range(10)
    ]
    db.add_all(messages)
    db.commit()
    for message in messages:
        db.refresh(message)
    captured: dict[str, object] = {}

    def fake_submit(coro, **kwargs):
        captured.update(kwargs)
        coro.close()
        return "summary-task"

    monkeypatch.setattr(session_service, "submit", fake_submit)

    task_id = session_service.schedule_summary(
        db, item, boundary_message_id=messages[-1].id
    )

    assert task_id == "summary-task"
    assert captured["name"] == "teacher-judge-summary"
    assert str(messages[-1].id) in str(captured["task_id"])


@pytest.mark.asyncio
async def test_summary_worker_uses_fresh_session_for_model_and_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(worker_engine)
    monkeypatch.setattr(session_service, "engine", worker_engine)
    with Session(worker_engine) as db:
        item = TeacherJudgeSession(
            teaching_class_id=uuid.uuid4(),
            title="Worker summary",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        messages = [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"worker-answer-{index}",
                created_at=datetime(2026, 8, 3, 0, 0, index, tzinfo=timezone.utc),
            )
            for index in range(10)
        ]
        db.add_all(messages)
        db.commit()
        for message in messages:
            db.refresh(message)
        session_id = item.id
        boundary_id = messages[-1].id

    async def fake_summary(messages, previous_summary=""):
        assert messages[-1].content == "worker-answer-9"
        return "背景摘要已保存", {}

    monkeypatch.setattr(session_service, "summarize_conversation", fake_summary)
    await session_service.run_summary_job(
        session_id,
        boundary_id,
        10,
        None,
        None,
    )

    with Session(worker_engine) as db:
        saved = db.get(TeacherJudgeSession, session_id)
        assert saved is not None
        assert saved.summary == "背景摘要已保存"
        assert saved.summary_through_message_id == boundary_id
        assert saved.summary_through_assistant_count == 10


def test_session_public_many_matches_single_session_contract() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    first = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="第一個檢查",
        selected_file_id=rubric_file.id,
    )
    second = TeacherJudgeSession(teaching_class_id=class_id, title="第二個檢查")
    db.add_all([first, second])
    db.commit()
    db.refresh(first)
    db.refresh(second)
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=first.id,
                role=TeacherJudgeMessageRole.user,
                content="請檢查",
            ),
            TeacherJudgeSessionMessage(
                session_id=first.id,
                role=TeacherJudgeMessageRole.assistant,
                content="已完成",
            ),
        ]
    )
    db.commit()
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=class_id,
        session_id=first.id,
        name="檢查腳本",
        template_key="linux",
        script_content="echo ok",
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    db.add(TeacherJudgeScriptRun(teaching_class_id=class_id, artifact_id=artifact.id))
    db.commit()

    batch = session_service.session_public_many(db, [first, second])
    singles = [session_service.session_public(db, item) for item in [first, second]]

    assert [row.model_dump() for row in batch] == [row.model_dump() for row in singles]
    assert batch[0].selected_file_name == singles[0].selected_file_name
    assert batch[0].message_count == 2
    assert batch[0].script_count == 1
    assert batch[0].run_count == 1
    assert batch[1].message_count == 0


def test_summary_runs_only_on_tenth_completed_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Summary",
        selected_file_id=rubric_file.id,
        summary="old",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    submits: list[dict[str, object]] = []

    def fake_submit(coro, **kwargs):
        submits.append(dict(kwargs))
        coro.close()
        return "summary-task"

    monkeypatch.setattr(session_service, "submit", fake_submit)

    for index in range(9):
        db.add(
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"assistant-{index}",
            )
        )
    db.commit()
    db.refresh(item)
    assert session_service.schedule_summary(db, item) == ""
    assert submits == []
    assert db.get(TeacherJudgeSession, item.id).summary == "old"

    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="assistant-10",
        )
    )
    db.commit()
    db.refresh(item)
    task_id = session_service.schedule_summary(db, item)

    assert task_id == "summary-task"
    assert len(submits) == 1
    assert submits[0]["name"] == "teacher-judge-summary"
    assert str(item.id) in str(submits[0]["task_id"])


@pytest.mark.asyncio
async def test_summary_failure_preserves_previous_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(worker_engine)
    monkeypatch.setattr(session_service, "engine", worker_engine)
    with Session(worker_engine) as db:
        class_id = uuid.uuid4()
        rubric_file = make_teacher_judge_file(db, class_id)
        item = TeacherJudgeSession(
            teaching_class_id=class_id,
            title="Summary failure",
            selected_file_id=rubric_file.id,
            summary="keep me",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        db.refresh(rubric_file)
        messages = [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"assistant-{index}",
            )
            for index in range(10)
        ]
        db.add_all(messages)
        db.commit()
        for message in messages:
            db.refresh(message)
        session_id = item.id
        boundary_id = messages[-1].id
        file_id = rubric_file.id
        file_revision = rubric_file.analysis_revision

    async def fail_summary(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(session_service, "summarize_conversation", fail_summary)
    await session_service.run_summary_job(
        session_id,
        boundary_id,
        10,
        file_id,
        file_revision,
    )

    with Session(worker_engine) as db:
        saved = db.get(TeacherJudgeSession, session_id)
        assert saved is not None
        assert saved.summary == "keep me"


def test_bounded_history_excludes_messages_covered_by_summary_boundary() -> None:
    """P3: summary + summarized originals must not both enter the model context."""
    db = make_session()
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(),
        title="Summary boundary",
        summary="已決定只檢查 Python 版本。",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    old = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.assistant,
        content="old-decision",
        created_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    boundary = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.assistant,
        content="boundary-decision",
        created_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
    )
    new = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.user,
        content="new-question",
        created_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
    )
    db.add_all([old, boundary, new])
    db.commit()
    for message in (old, boundary, new):
        db.refresh(message)

    history = session_service.bounded_history(
        db,
        item.id,
        summary=item.summary,
        summary_through_message_id=boundary.id,
    )
    contents = [message.content for message in history]

    assert any("只檢查 Python 版本" in content for content in contents)
    assert not any("old-decision" in content for content in contents)
    assert not any("boundary-decision" in content for content in contents)
    assert any("new-question" in content for content in contents)


def test_bounded_history_compacts_older_attachments_but_keeps_latest_full() -> None:
    """P3: past attachment full text is replaced by a compact placeholder."""
    from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment

    db = make_session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Compact")
    db.add(item)
    db.commit()
    db.refresh(item)
    first = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.user,
        content="first with file",
        created_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
    )
    second = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.user,
        content="second with file",
        created_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    db.add_all([first, second])
    db.commit()
    db.refresh(first)
    db.refresh(second)
    db.add_all(
        [
            TeacherJudgeSessionAttachment(
                session_id=item.id,
                message_id=first.id,
                original_filename="old.md",
                extracted_text="OLD-FULL-TEXT-12345",
                storage_key="old.md",
                size_bytes=10,
                file_hash="c" * 64,
            ),
            TeacherJudgeSessionAttachment(
                session_id=item.id,
                message_id=second.id,
                original_filename="new.md",
                extracted_text="NEW-FULL-TEXT-67890",
                storage_key="new.md",
                size_bytes=10,
                file_hash="d" * 64,
            ),
        ]
    )
    db.commit()

    history = session_service.bounded_history(db, item.id)

    assert len(history) == 2
    assert "OLD-FULL-TEXT-12345" not in history[0].content
    assert "old.md" in history[0].content
    assert "已收斂" in history[0].content
    assert "NEW-FULL-TEXT-67890" in history[1].content
