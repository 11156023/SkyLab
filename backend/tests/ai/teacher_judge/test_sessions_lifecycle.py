"""Split from tests/test_teacher_judge_sessions.py: session lifecycle (create/fork/delete/ownership).

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.ai.teacher_judge import file_service, session_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionMessageCreateRequest,
)
from app.api.routes import teacher_judge_sessions
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
)


def test_selected_file_must_belong_to_same_teaching_class() -> None:
    db = make_session()
    foreign_file = make_teacher_judge_file(db, uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        session_service.validate_selected_file(db, uuid.uuid4(), foreign_file.id)

    assert exc_info.value.status_code == 400


def test_archived_session_is_read_only() -> None:
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(),
        title="Archived",
        status=TeacherJudgeSessionStatus.archived,
    )

    with pytest.raises(HTTPException) as exc_info:
        session_service.ensure_active(item)

    assert exc_info.value.status_code == 409


def test_clear_messages_keeps_session(monkeypatch: pytest.MonkeyPatch) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Clear chat",
        summary="過時摘要",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    db.add_all(
        [
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.user,
                content="問題",
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content="回答",
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    result = teacher_judge_sessions.clear_messages(
        class_id,
        item.id,
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.id == str(item.id)
    assert result.message_count == 0
    assert result.title == "Clear chat"
    refreshed = db.get(TeacherJudgeSession, item.id)
    assert refreshed is not None
    assert refreshed.summary == ""
    assert db.exec(select(TeacherJudgeSessionMessage)).all() == []


def test_session_creation_mode_contract_is_explicit() -> None:
    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(
            title="Blank without rubric",
            creation_mode="blank",
            environment_keys=["linux"],
        )

    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(
            title="Existing without file",
            creation_mode="existing",
        )

    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(
            title="Existing with blank fields",
            creation_mode="existing",
            selected_file_id=uuid.uuid4(),
            rubric_name="should not be sent",
        )


def test_chat_can_start_without_selected_file() -> None:
    db = make_session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Chat first")
    db.add(item)
    db.commit()
    db.refresh(item)

    assert session_service.selected_file_for_chat(db, item) is None


def test_delete_session_data_removes_owned_records_and_private_file() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Delete me",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=class_id,
        session_id=item.id,
        name="Delete script",
        template_key="linux",
        script_content="print('ok')",
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    db.add_all(
        [
            TeacherJudgeScriptRun(
                teaching_class_id=class_id,
                artifact_id=artifact.id,
            ),
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.user,
                content="remove this",
            ),
        ]
    )
    db.commit()

    session_service.delete_session_data(db, item)

    assert db.get(TeacherJudgeSession, item.id) is None
    assert db.get(TeacherJudgeScriptArtifact, artifact.id) is None
    assert not db.exec(select(TeacherJudgeScriptRun)).all()
    assert not db.exec(select(TeacherJudgeSessionMessage)).all()
    assert db.get(TeacherJudgeFile, rubric_file.id) is None


def test_selected_file_cannot_be_claimed_by_another_session() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    owner = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Owner",
        selected_file_id=rubric_file.id,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)

    with pytest.raises(HTTPException) as exc_info:
        session_service.ensure_selected_file_available(db, rubric_file.id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_file_in_use"
    assert "重構" in exc_info.value.detail["message"]


def test_create_session_rejects_a_source_owned_by_another_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    db.add(
        TeacherJudgeSession(
            teaching_class_id=class_id,
            title="Owner",
            selected_file_id=rubric_file.id,
        )
    )
    db.commit()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as exc_info:
        teacher_judge_sessions.create_session(
            class_id,
            TeacherJudgeSessionCreateRequest(
                title="Should fail",
                creation_mode="existing",
                selected_file_id=rubric_file.id,
            ),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert "重構" in exc_info.value.detail["message"]
    assert len(db.exec(select(TeacherJudgeSession)).all()) == 1


def test_selected_file_unique_index_allows_only_one_session_owner() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    db.add(
        TeacherJudgeSession(
            teaching_class_id=class_id,
            title="First",
            selected_file_id=rubric_file.id,
        )
    )
    db.commit()
    db.add(
        TeacherJudgeSession(
            teaching_class_id=class_id,
            title="Second",
            selected_file_id=rubric_file.id,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_fork_created_session_clones_rubric_without_history() -> None:
    db = make_session()
    class_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    rubric_file = file_service.create_blank_file(
        session=db,
        teaching_class_id=class_id,
        created_by=owner_id,
        display_name="原始檢查表",
        environment_keys=["python"],
    )
    db.commit()
    source = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="原始檢查",
        selected_file_id=rubric_file.id,
        summary="不要複製這段摘要",
        status=TeacherJudgeSessionStatus.archived,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    db.add(
        TeacherJudgeSessionMessage(
            session_id=source.id,
            role=TeacherJudgeMessageRole.user,
            content="歷史對話",
        )
    )
    db.commit()

    clone = session_service.fork_session_data(
        db,
        source,
        title=None,
        created_by=uuid.uuid4(),
    )

    assert clone.id != source.id
    assert clone.title == "原始檢查（副本）"
    assert clone.status == TeacherJudgeSessionStatus.active
    assert clone.summary == ""
    assert clone.selected_file_id != source.selected_file_id
    cloned_file = db.get(TeacherJudgeFile, clone.selected_file_id)
    source_file = db.get(TeacherJudgeFile, source.selected_file_id)
    assert cloned_file is not None and source_file is not None
    assert cloned_file.id != source_file.id
    assert cloned_file.source_type == "created"
    assert cloned_file.analysis_json == source_file.analysis_json
    assert session_service.session_public(db, clone).message_count == 0
    assert session_service.session_public(db, clone).script_count == 0
    assert session_service.session_public(db, clone).run_count == 0

    file_service.update_file_analysis(
        session=db,
        teaching_class_id=class_id,
        file_id=cloned_file.id,
        analysis=TeacherJudgeRubricAnalysis(
            items=[],
            total_items=0,
            summary="只改副本",
        ),
        expected_revision=1,
    )
    source_file_after = db.get(TeacherJudgeFile, source_file.id)
    assert source_file_after is not None
    assert source_file_after.analysis_json["summary"] == ""


@pytest.mark.asyncio
async def test_chat_does_not_save_old_answer_after_source_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    first_file = make_teacher_judge_file(db, class_id)
    second_file = TeacherJudgeFile(
        teaching_class_id=class_id,
        original_filename="new-rubric.pdf",
        file_hash="c" * 64,
        template_key="linux",
        analysis_json={"items": []},
    )
    db.add(second_file)
    db.commit()
    db.refresh(second_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Concurrent source",
        selected_file_id=first_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
    )

    async def fake_chat(*args, **kwargs):
        session_service.clear_session_messages(db, item)
        item.selected_file_id = second_file.id
        db.add(item)
        db.commit()
        return "不應保存的舊回答", None, {}

    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(content="舊來源問題"),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_context_changed"
    assert db.exec(select(TeacherJudgeSessionMessage)).all() == []
