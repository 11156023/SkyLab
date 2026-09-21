"""Split from tests/test_teacher_judge_sessions.py: session messages, attachments & script workflow.

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

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
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
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
    TeacherJudgeMessageType,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    requirement_focus,
    scripted_vllm,
    tool_call_message,
)


def test_reanalysis_workflow_message_keeps_actionable_ai_summary() -> None:
    workflow = session_service.reanalysis_workflow_message(
        [
            {
                "item_id": "architecture",
                "title": "程式架構品質",
                "status": "manual",
                "missing_information": [],
                "reason_code": "automatic_detection_unsupported",
                "detail": "目前沒有可安全讀取的檔案來源",
            }
        ],
        source_file_id=uuid.uuid4(),
        analysis_revision=4,
        assistant_reply="這項目目前缺少可讀取的程式來源，其他項目可以繼續核對。",
    )

    assert "缺少可讀取的程式來源" in workflow["content"]
    assert "目前沒有可安全讀取的檔案來源" in workflow["content"]
    assert workflow["metadata"]["item_results"][0]["detail"] == (
        "目前沒有可安全讀取的檔案來源"
    )
    assert workflow["metadata"]["conversation_focus"]["requirements"][0][
        "detail"
    ] == "目前沒有可安全讀取的檔案來源"


@pytest.mark.asyncio
async def test_refine_auto_teacher_typed_plan_is_script_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "teacher-evidence",
                "title": "檢查 main.py 內容",
                "checked": False,
                "detectable": "auto",
                "judgement_mode": "teacher",
                "detection_method": "讀取 main.py 內容供導師查看",
                "missing_information": [],
                "check_steps": [
                    {
                        "id": "teacher-evidence.read",
                        "title": "讀取 main.py",
                        "collector": {
                            "type": "file_text",
                            "path": "/srv/student/main.py",
                            "encoding": "utf-8",
                            "read_mode": "head",
                            "lines": 200,
                        },
                    }
                ],
            }
        ]
    }
    db.add(rubric_file)
    db.commit()
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Typed teacher evidence",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(*args, **kwargs):
        assert kwargs["is_refine"] is True
        return "這項目會收集結果，交由導師查看。", None, {}

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
        TeacherJudgeSessionMessageCreateRequest(
            content="請重新核對整張檢查表",
            is_refine=True,
        ),
        db,
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert result.assistant_message.metadata_json["status"] == "resolved"
    assert result.assistant_message.metadata_json["script_ready"] is True
    assert "可開始製作檢查腳本" in result.assistant_message.content


@pytest.mark.asyncio
async def test_message_without_rubric_is_saved_and_uses_general_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
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
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
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
    db = make_session()
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
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
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
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-1",
                "title": "程式可執行",
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
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
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

    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).all()
    assert len(outcome) == 1
    assert outcome[0].message_type == TeacherJudgeMessageType.chat
    assert outcome[0].metadata_json["status"] == "analysis_error"
    assert "沒有覆蓋" in outcome[0].content


@pytest.mark.asyncio
async def test_session_script_preflight_failure_is_saved_with_item_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-port",
                "title": "確認服務 Port",
                "checked": False,
                "detectable": "partial",
                "detection_method": "檢查服務",
                "missing_information": ["服務 Port"],
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
        title="Script preflight",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def blocked_artifact(**kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_script_not_ready",
                "items": [
                    {
                        "item_id": "item-port",
                        "title": "確認服務 Port",
                        "status": "missing_info",
                        "missing_information": ["服務 Port"],
                        "reason_code": "automatic_detection_information_missing",
                    }
                ],
            },
        )

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "create_artifact", blocked_artifact)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_session_script(
            class_id,
            item.id,
            db,
            SimpleNamespace(id=uuid.uuid4()),
            TeacherJudgeSessionScriptCreateRequest(
                analysis_revision=rubric_file.analysis_revision
            ),
        )

    assert exc_info.value.status_code == 422
    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).one()
    assert outcome.message_type == TeacherJudgeMessageType.chat
    assert outcome.metadata_json["status"] == "needs_information"
    assert outcome.metadata_json["stage"] == "script_preflight"
    assert (
        outcome.metadata_json["conversation_focus"]["requirements"][0]["target_item_id"]
        == "item-port"
    )
    assert "服務 Port" in outcome.content


@pytest.mark.asyncio
async def test_session_script_review_failed_saves_safe_chat_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-port",
                "title": "確認服務 Port",
                "checked": False,
                "detectable": "auto",
                "detection_method": "檢查 listening socket",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["ss", "-lnt"],
                            "timeout_seconds": 10,
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
                "fallback": None,
            }
        ]
    }
    db.add(rubric_file)
    db.commit()
    db.refresh(rubric_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Review failed",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    artifact_id = uuid.uuid4()

    async def fake_artifact(**kwargs):
        return SimpleNamespace(
            id=artifact_id,
            status="review_failed",
            policy_check_result_json={
                "safety_approved": True,
                "quality_approved": True,
                "coverage": {
                    "approved": False,
                    "issues": ["coverage 引用不存在的 check id：missing"],
                    "uncovered_items": ["item-port"],
                },
            },
            ai_review_result_json={"approved": False, "issues": ["coverage mismatch"]},
        )

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "create_artifact", fake_artifact)

    result = await teacher_judge_sessions.create_session_script(
        class_id,
        item.id,
        db,
        SimpleNamespace(id=uuid.uuid4()),
        TeacherJudgeSessionScriptCreateRequest(
            analysis_revision=rubric_file.analysis_revision
        ),
    )

    assert result.status == "review_failed"
    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).one()
    assert outcome.metadata_json["status"] == "analysis_error"
    assert outcome.metadata_json["stage"] == "script_review"
    assert outcome.metadata_json["artifact_id"] == str(artifact_id)
    assert "coverage mismatch" not in outcome.content
    assert "腳本未通過覆蓋檢查" in outcome.content
    assert "coverage 引用不存在的 check id：missing" in outcome.content


@pytest.mark.asyncio
async def test_session_script_set_revision_conflict_is_saved_to_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Script set stale revision",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_session_script_set(
            class_id,
            item.id,
            db,
            SimpleNamespace(id=uuid.uuid4()),
            TeacherJudgeSessionScriptCreateRequest(analysis_revision=99),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "teacher_judge_analysis_revision_conflict"
    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).one()
    assert outcome.metadata_json["stage"] == "persistence"
    assert outcome.metadata_json["reason_code"] == "analysis_revision_conflict"
    assert "沒有覆蓋" in outcome.content


@pytest.mark.asyncio
async def test_session_script_set_preflight_blocker_is_saved_to_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-port",
                "title": "確認服務 Port",
                "checked": False,
                "detectable": "partial",
                "detection_method": "檢查服務",
                "missing_information": ["服務 Port"],
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
        title="Script set preflight",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_session_script_set(
            class_id,
            item.id,
            db,
            SimpleNamespace(id=uuid.uuid4()),
            TeacherJudgeSessionScriptCreateRequest(
                analysis_revision=rubric_file.analysis_revision
            ),
        )

    assert exc_info.value.status_code == 422
    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).one()
    assert outcome.metadata_json["stage"] == "script_preflight"
    assert outcome.metadata_json["status"] == "needs_information"
    assert outcome.metadata_json["reason_code"] == "teacher_judge_script_not_ready"
    assert "服務 Port" in outcome.content


@pytest.mark.asyncio
async def test_session_script_set_compile_failure_is_saved_to_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Script set compile failure",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    user = SimpleNamespace(id=uuid.uuid4())

    async def failed_artifact_set(**kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_check_plan_invalid",
                "message": "完整 Check Plan 未通過契約驗證。",
                "issues": [{"message": "assertion 不完整"}],
            },
        )

    monkeypatch.setattr(
        teacher_judge_sessions,
        "_session_rubric_for_script_set",
        lambda **kwargs: (
            item,
            rubric_file,
            TeacherJudgeRubricAnalysis(items=[]),
        ),
    )
    monkeypatch.setattr(
        teacher_judge_sessions, "create_artifact_set", failed_artifact_set
    )

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_session_script_set(
            class_id,
            item.id,
            db,
            user,
            TeacherJudgeSessionScriptCreateRequest(
                analysis_revision=rubric_file.analysis_revision
            ),
        )

    assert exc_info.value.status_code == 422
    outcome = db.exec(
        select(TeacherJudgeSessionMessage).where(
            TeacherJudgeSessionMessage.role == TeacherJudgeMessageRole.assistant
        )
    ).one()
    assert outcome.metadata_json["stage"] == "script_generation"
    assert outcome.metadata_json["status"] == "analysis_error"
    assert outcome.metadata_json["reason_code"] == "teacher_judge_check_plan_invalid"


@pytest.mark.asyncio
async def test_message_can_send_parsed_attachment_without_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    db = make_session()
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
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
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
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
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
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
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
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
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
    assert item_results[1]["item_id"] == "attachment-item-2"
    focus = result.assistant_message.metadata_json["conversation_focus"]
    assert focus["source_file_id"] == str(rubric_file.id)
    assert focus["analysis_revision"] == original_revision
    assert focus["requirements"][0]["target_item_id"] == "attachment-item-2"
    assert focus["requirements"][0]["missing_information"] == ["連接埠"]
    assert "rubric_proposal" not in result.assistant_message.metadata_json
    assert result.assistant_message.message_type == "chat"
    db.refresh(rubric_file)
    assert rubric_file.analysis_revision == original_revision


@pytest.mark.asyncio
async def test_refine_message_uses_the_rubric_polish_prompt_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
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
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
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

    assert result.assistant_message.content.startswith("重新核對後")
    assert result.rubric_proposal == []
    assert result.user_message.metadata_json["ui_hidden"] is True
    assert result.assistant_message.message_type == "chat"
    assert "ui_hidden" not in result.assistant_message.metadata_json
    assert result.assistant_message.metadata_json["status"] == "needs_information"
    assert result.assistant_message.metadata_json["script_ready"] is False
    assert result.assistant_message.metadata_json["conversation_focus"][
        "source_file_id"
    ] == str(rubric_file.id)
    assert (
        result.assistant_message.metadata_json["conversation_focus"]["requirements"][0][
            "status"
        ]
        == "needs_information"
    )


@pytest.mark.asyncio
async def test_refine_message_uses_server_readiness_and_saves_resolved_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "item-port",
                "title": "確認服務 Port",
                "checked": False,
                "detectable": "auto",
                "detection_method": "檢查 listening socket",
                "missing_information": [],
                "check_steps": [
                    {
                        "id": "port.listen",
                        "title": "列出 listening socket",
                        "collector": {
                            "type": "command",
                            "argv": ["ss", "-lnt"],
                            "timeout_seconds": 10,
                        },
                        "assertion": {
                            "type": "text_contains",
                            "expected": "LISTEN",
                        },
                    }
                ],
                "fallback": None,
            }
        ]
    }
    db.add(rubric_file)
    db.commit()
    db.refresh(rubric_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Resolved refine",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fake_chat(messages, rubric_context, **kwargs):
        assert kwargs["is_refine"] is True
        return "模型回覆不作為安全閘門。", [], {}

    command = TeacherJudgeTemplateCommand(
        template_key="linux",
        command_key="system.run_command",
        command_label="執行唯讀命令",
        category="diagnostic",
        command_template="argv + timeout",
        description="受控唯讀命令",
        risk_level="read_only",
        requires_confirmation=False,
    )
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [command],
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

    assert result.rubric_proposal == []
    assert "可開始製作" in result.assistant_message.content
    assert result.assistant_message.metadata_json["status"] == "resolved"
    assert result.assistant_message.metadata_json["script_ready"] is True
    assert result.assistant_message.metadata_json["conversation_focus"][
        "requirements"
    ] == [
        {
            "focus_key": "workflow",
            "status": "none",
            "known_information": [],
            "missing_information": [],
            "reason_code": "",
        }
    ]


@pytest.mark.asyncio
async def test_refine_message_preserves_ready_proposal_rows_alongside_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    rubric_file.analysis_json = {
        "items": [
            {
                "id": "existing",
                "title": "既有服務",
                "checked": False,
                "detectable": "partial",
                "detection_method": "檢查服務",
                "missing_information": ["服務 Port"],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["systemctl", "is-active", "api"],
                            "timeout_seconds": 10,
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
                "fallback": None,
            }
        ]
    }
    db.add(rubric_file)
    db.commit()
    db.refresh(rubric_file)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Mixed refine",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    proposal = [
        {
            "id": "ready-new",
            "title": "確認 Python 版本",
            "operation": "add",
            "checked": False,
            "detectable": "auto",
            "detection_method": "執行 python --version",
            "missing_information": [],
            "check_steps": [
                {
                    "template_key": "linux",
                    "command_key": "system.run_command",
                    "parameters": {
                        "argv": ["python", "--version"],
                        "timeout_seconds": 10,
                        "success_criteria": "exit code 為 0",
                    },
                }
            ],
            "fallback": None,
        }
    ]

    async def fake_chat(*args, **kwargs):
        return "模型回覆不作為安全閘門。", proposal, {}

    command = TeacherJudgeTemplateCommand(
        template_key="linux",
        command_key="system.run_command",
        command_label="執行唯讀命令",
        category="diagnostic",
        command_template="argv + timeout",
        description="受控唯讀命令",
        risk_level="read_only",
        requires_confirmation=False,
    )
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fake_chat)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [command],
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

    assert result.rubric_proposal == proposal
    rows = result.assistant_message.metadata_json["item_results"]
    assert any(
        row["item_id"] == "ready-new" and row["status"] == "ready" for row in rows
    )
    assert any(row["status"] == "needs_information" for row in rows)
    assert result.assistant_message.metadata_json["script_ready"] is False


@pytest.mark.asyncio
async def test_message_failure_is_saved_as_visible_processing_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
    item = TeacherJudgeSession(
        teaching_class_id=class_id,
        title="Failure outcome",
        selected_file_id=rubric_file.id,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    async def fail_chat(*args, **kwargs):
        raise HTTPException(status_code=503, detail="upstream secret detail")

    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *args: None)
    monkeypatch.setattr(teacher_judge_sessions, "chat_with_rubric", fail_chat)
    monkeypatch.setattr(
        teacher_judge_sessions,
        "get_enabled_template_commands",
        lambda *args, **kwargs: [],
    )

    with pytest.raises(HTTPException) as exc_info:
        await teacher_judge_sessions.create_message(
            class_id,
            item.id,
            TeacherJudgeSessionMessageCreateRequest(content="請重新核對"),
            db,
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert exc_info.value.status_code == 503
    assert "upstream secret detail" not in str(exc_info.value.detail)
    rows = db.exec(
        select(TeacherJudgeSessionMessage).order_by(
            TeacherJudgeSessionMessage.created_at,
            TeacherJudgeSessionMessage.id,
        )
    ).all()
    assert rows[-1].role == TeacherJudgeMessageRole.assistant
    assert rows[-1].message_type == TeacherJudgeMessageType.chat
    assert rows[-1].metadata_json["status"] == "analysis_error"
    assert rows[-1].metadata_json["stage"] == "reanalysis"
    assert "upstream secret detail" not in rows[-1].content
    assert "這不是缺少你的資料" in rows[-1].content


@pytest.mark.asyncio
async def test_message_rejects_stale_rubric_revision_before_ai_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = make_session()
    class_id = uuid.uuid4()
    rubric_file = make_teacher_judge_file(db, class_id)
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
