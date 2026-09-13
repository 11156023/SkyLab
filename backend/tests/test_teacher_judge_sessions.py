from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.ai.teacher_judge import attachment_service, file_service, session_service
from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionMessageCreateRequest,
    TeacherJudgeSessionScriptCreateRequest,
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
    TeacherJudgeSessionStatus,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _file(db: Session, class_id: uuid.UUID) -> TeacherJudgeFile:
    item = TeacherJudgeFile(
        teaching_class_id=class_id,
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        analysis_json={"items": [], "summary": "rubric"},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_selected_file_must_belong_to_same_teaching_class() -> None:
    db = _session()
    foreign_file = _file(db, uuid.uuid4())

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
    db = _session()
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
    db = _session()
    item = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Chat first")
    db.add(item)
    db.commit()
    db.refresh(item)

    assert session_service.selected_file_for_chat(db, item) is None


def test_delete_session_data_removes_owned_records_and_private_file() -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
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
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
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
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
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
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
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
    db = _session()
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
async def test_message_without_rubric_is_saved_and_uses_general_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Chat first",
        summary="先前已確認要保留 Python 檢查。",
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(messages, rubric_context, **kwargs):
        assert "保留 Python 檢查" in messages[0].content
        assert messages[-1].content == "先討論檢查需求"
        assert rubric_context == "{}"
        assert kwargs["is_refine"] is False
        assert kwargs["template_key"] == "linux"
        return "可以，先描述目標環境。", None, {}

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *args, **kwargs: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(content="先討論檢查需求"),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.user_message.content == "先討論檢查需求"
    assert result.assistant_message.content == "可以，先描述目標環境。"
    assert result.rubric_proposal is None
    assert len(db.exec(select(TeacherJudgeSessionMessage)).all()) == 2


@pytest.mark.asyncio
async def test_message_without_rubric_does_not_claim_proposal_was_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=class_id, title="Chat first")
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(*args, **kwargs):
        return (
            "檔案格式檢查：Ready，已放入提案。",
            [
                {
                    "id": "item-file-format",
                    "title": "檔案格式檢查",
                    "operation": "add",
                }
            ],
            {},
        )

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *args, **kwargs: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(content="檢查檔案格式"),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.rubric_proposal is None
    assert "尚未選擇檢查表來源" in result.assistant_message.content
    assert "已放入提案" not in result.assistant_message.content


@pytest.mark.asyncio
async def test_message_does_not_enable_script_creation_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-1",
                "title": "程式可執行",
                "description": "",
                "checked": False,
                "detectable": "auto",
                "detection_method": "exit code",
                "check_steps": [],
                "fallback": None,
            }
        ]
    }
    db.add(rubric_file)
    db.commit()
    db.refresh(rubric_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Create script from chat",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(messages, rubric_context, **kwargs):
        assert "enable_workflow_tools" not in kwargs
        assert "ready_proposals_only" not in kwargs
        return (
            "請使用檢查表右下角的儲存並製作按鈕。",
            None,
            {},
        )

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(content="可以幫我製作檢查腳本嗎"),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.assistant_message.content == "請使用檢查表右下角的儲存並製作按鈕。"
    assert "workflow_action" not in result.assistant_message.metadata_json


@pytest.mark.asyncio
async def test_session_script_rejects_stale_analysis_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Stale script revision",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_session_script(
            class_id,
            item.id,
            db,
            SimpleNamespace(id=uuid.uuid4()),
            TeacherJudgeSessionScriptCreateRequest(analysis_revision=99),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_analysis_revision_conflict"


@pytest.mark.asyncio
async def test_message_can_send_parsed_attachment_without_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    item = TeacherJudgeSession(teaching_class_id=class_id, title="Attachment chat")
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path)
    attachment = attachment_service.create_attachment(
        db,
        session_id=item.id,
        uploaded_by=None,
        filename="requirements.md",
        media_type="text/markdown",
        file_bytes=b"# Requirements\nExpose port 8080.",
    )

    async def fake_itemwise(**kwargs):
        assert "requirements.md" in kwargs["attachment_context"]
        assert "port 8080" in kwargs["attachment_context"]
        return teacher_judge_service.TeacherJudgeItemwiseResult(
            reply="這份附件中沒有辨識出可核查的評分列。",
            proposal=None,
            metrics={"total_tokens": 1},
            item_results=[],
        )

    monkeypatch.setattr(
        teacher_judge_sessions, "analyze_attachments_itemwise", fake_itemwise
    )
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *args, **kwargs: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(attachment_ids=[attachment.id]),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.user_message.content == ""
    assert [row.original_filename for row in result.user_message.attachments] == [
        "requirements.md"
    ]
    db.refresh(attachment)
    assert attachment.message_id == uuid.UUID(result.user_message.id)


@pytest.mark.asyncio
async def test_attachment_proposal_is_ephemeral_until_explicit_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Attachment proposal",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    original_analysis = dict(rubric_file.analysis_json)
    original_revision = rubric_file.analysis_revision
    proposal = [
        {
            "op": "add",
            "item": {
                "id": "item-1",
                "title": "檢查 Port 8080",
                "description": "確認服務監聽 8080",
                "checked": False,
                "detectable": "auto",
                "detection_method": "檢查 listening socket",
                "missing_information": [],
                "check_steps": [],
                "fallback": None,
            },
        }
    ]
    captured_chat_kwargs = {}

    async def fake_chat(*_args, **kwargs):
        captured_chat_kwargs.update(kwargs)
        return "已建立一項可確認的提案。", proposal, {"total_tokens": 1}

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *args, **kwargs: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(
            content="請從附件加入 Port 8080 檢查",
            analysis_revision=original_revision,
        ),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.rubric_proposal == proposal
    assert result.base_revision == original_revision
    assert captured_chat_kwargs["analysis_revision"] == original_revision
    assert captured_chat_kwargs["rubric_available"] is True
    assert "rubric_proposal" not in result.assistant_message.metadata_json
    assert "base_revision" not in result.assistant_message.metadata_json
    assert result.assistant_message.message_type == "chat"
    db.refresh(rubric_file)
    assert rubric_file.analysis_revision == original_revision
    assert rubric_file.analysis_json == original_analysis


@pytest.mark.asyncio
async def test_attachment_message_runs_itemwise_analysis_and_records_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Itemwise attachment",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path)
    attachment = attachment_service.create_attachment(
        db,
        session_id=item.id,
        uploaded_by=None,
        filename="rubric.md",
        media_type="text/markdown",
        file_bytes="| 審查重點 |\n| 確認 Python 版本 |".encode(),
    )
    captured_kwargs = {}
    original_revision = rubric_file.analysis_revision
    ready_operation = {
        "id": "item-attachment-1",
        "operation": "add",
        "title": "確認 Python 版本",
        "description": "",
        "checked": False,
        "detectable": "auto",
        "judgement_mode": "ai",
        "detection_method": "執行 python --version。",
        "missing_information": [],
        "check_steps": [],
        "fallback": None,
    }

    async def fake_itemwise(**kwargs):
        captured_kwargs.update(kwargs)
        return teacher_judge_service.TeacherJudgeItemwiseResult(
            reply="已逐項核查附件中的 2 個項目。",
            proposal=[ready_operation],
            metrics={"total_tokens": 1},
            item_results=[
                {
                    "source_index": 1,
                    "source_label": "第 1 列",
                    "title": "確認 Python 版本",
                    "description": "",
                    "status": "ready",
                    "operation": ready_operation,
                    "missing_information": [],
                    "detail": "",
                },
                {
                    "source_index": 2,
                    "source_label": "第 2 列",
                    "title": "Port 8080",
                    "description": "",
                    "status": "needs_information",
                    "operation": None,
                    "missing_information": ["連接埠"],
                    "detail": "請補充 Port",
                },
            ],
        )

    monkeypatch.setattr(
        teacher_judge_sessions, "analyze_attachments_itemwise", fake_itemwise
    )
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *a, **k: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(
            content="幫我增加這些項目",
            attachment_ids=[attachment.id],
        ),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert captured_kwargs["rubric_available"] is True
    assert captured_kwargs["analysis_revision"] == rubric_file.analysis_revision
    assert result.rubric_proposal == [ready_operation]
    item_results = result.assistant_message.metadata_json["item_results"]
    assert [row["status"] for row in item_results] == ["ready", "needs_information"]
    assert "rubric_proposal" not in result.assistant_message.metadata_json
    assert result.assistant_message.message_type == "chat"
    db.refresh(rubric_file)
    assert rubric_file.analysis_revision == original_revision


@pytest.mark.asyncio
async def test_refine_message_uses_the_rubric_polish_prompt_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Polish rubric",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(messages, rubric_context, **kwargs):
        assert messages[-1].content == "請審核並潤飾目前的檢查表"
        assert '"items": []' in rubric_context
        assert kwargs["is_refine"] is True
        assert "ready_proposals_only" not in kwargs
        return "檢查完畢，檢查表目前狀態良好。", None, {}

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions, "get_enabled_template_commands", lambda *args, **kwargs: []
    )

    result = await teacher_judge_sessions.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(
            content="請審核並潤飾目前的檢查表",
            is_refine=True,
        ),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.assistant_message.content == "檢查完畢，檢查表目前狀態良好。"
    assert result.rubric_proposal is None
    assert result.user_message.metadata_json["ui_hidden"] is True
    assert result.assistant_message.metadata_json["ui_hidden"] is True


@pytest.mark.asyncio
async def test_message_rejects_stale_rubric_revision_before_ai_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Revision guard",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    async def should_not_call_ai(*args, **kwargs):
        raise AssertionError("stale requests must fail before AI call")

    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", should_not_call_ai)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(
                content="請更新檢查表",
                analysis_revision=99,
            ),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_analysis_revision_conflict"
    assert db.exec(select(TeacherJudgeSessionMessage)).all() == []


@pytest.mark.asyncio
async def test_chat_does_not_save_old_answer_after_source_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    first_file = _file(db, class_id)
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


def test_message_content_redacts_common_secrets() -> None:
    content = session_service.redact_message_content(
        "token=abc123 password: hunter2\n"
        "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    )

    assert "abc123" not in content
    assert "hunter2" not in content
    assert "\nsecret\n" not in content
    assert content.count("[REDACTED]") == 2


def test_message_public_normalizes_only_whitespace_entities() -> None:
    item = TeacherJudgeSessionMessage(
        session_id=uuid.uuid4(),
        role=TeacherJudgeMessageRole.assistant,
        content="CPU&#x20;資訊&#32;&nbsp;<b>純文字</b>",
    )

    public = session_service.message_public(item)

    assert public.content == "CPU 資訊  <b>純文字</b>"


def test_bounded_history_keeps_latest_messages_in_stable_order() -> None:
    db = _session()
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
    db = _session()
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
    db = _session()
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

    same_source = session_service.bounded_history(
        db, item.id, source_file_id=source_id
    )
    other_source = session_service.bounded_history(
        db, item.id, source_file_id=uuid.uuid4()
    )

    assert any("resource-usage" in message.content for message in same_source)
    assert not any("resource-usage" in message.content for message in other_source)


def test_bounded_history_skips_resolved_requirements_in_focus() -> None:
    db = _session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(), title="Focus resolved"
    )
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
    db = _session()
    source_id = uuid.uuid4()
    item = TeacherJudgeSession(
        teaching_class_id=uuid.uuid4(), title="Focus all ready"
    )
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


def test_summary_persistence_is_monotonic_for_out_of_order_workers() -> None:
    db = _session()
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
    db = _session()
    class_id = uuid.uuid4()
    first_file = _file(db, class_id)
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
    db = _session()
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
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
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

    assert [row.model_dump() for row in batch] == [
        row.model_dump() for row in singles
    ]
    assert batch[0].selected_file_name == singles[0].selected_file_name
    assert batch[0].message_count == 2
    assert batch[0].script_count == 1
    assert batch[0].run_count == 1
    assert batch[1].message_count == 0


@pytest.mark.asyncio
async def test_summary_runs_only_on_tenth_completed_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Summary",
        selected_file_id=rubric_file.id,
        summary="old",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    calls = 0

    async def fake_summary(*args, **kwargs):
        nonlocal calls
        calls += 1
        return "new summary", {}

    monkeypatch.setattr(session_service, "summarize_conversation", fake_summary)

    for index in range(9):
        db.add(
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"assistant-{index}",
            )
        )
    db.commit()
    await session_service.maybe_summarize(db, item, rubric_file)
    assert calls == 0
    assert item.summary == "old"

    db.add(
        TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content="assistant-10",
        )
    )
    db.commit()
    await session_service.maybe_summarize(db, item, rubric_file)

    assert calls == 1
    assert item.summary == "new summary"


@pytest.mark.asyncio
async def test_summary_failure_preserves_previous_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _session()
    class_id = uuid.uuid4()
    rubric_file = _file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Summary failure",
        selected_file_id=rubric_file.id,
        summary="keep me",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    for index in range(10):
        db.add(
            TeacherJudgeSessionMessage(
                session_id=item.id,
                role=TeacherJudgeMessageRole.assistant,
                content=f"assistant-{index}",
            )
        )
    db.commit()

    async def fail_summary(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(session_service, "summarize_conversation", fail_summary)
    await session_service.maybe_summarize(db, item, rubric_file)

    assert item.summary == "keep me"
