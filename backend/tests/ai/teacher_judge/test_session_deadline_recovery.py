"""Teacher-facing request deadlines and durable proposal recovery."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.ai.teacher_judge import service
from app.ai.teacher_judge.schemas import TeacherJudgeSessionMessageCreateRequest
from app.api.routes import teacher_judge_sessions as routes
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from tests.ai.teacher_judge.helpers import make_session, make_teacher_judge_file


@pytest.fixture
def context(monkeypatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        item = TeacherJudgeSession(
            teaching_class_id=class_id, title="Recovery", selected_file_id=file.id
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        monkeypatch.setattr(routes, "_access", lambda *args: None)
        monkeypatch.setattr(routes, "schedule_summary", lambda *args, **kwargs: None)
        monkeypatch.setattr(routes, "record_ai_template_call", lambda **kwargs: None)
        monkeypatch.setattr(
            routes,
            "teacher_judge_settings",
            SimpleNamespace(REQUEST_TIMEOUT_SECONDS=5, VLLM_MODEL_NAME="test"),
        )
        yield db, class_id, item, file, SimpleNamespace(id=uuid.uuid4())


async def send(context, **kwargs):
    db, class_id, item, file, user = context
    return await routes.create_message(
        class_id,
        item.id,
        TeacherJudgeSessionMessageCreateRequest(
            content="核對版本", analysis_revision=file.analysis_revision, **kwargs
        ),
        db,
        user,
    )


@pytest.mark.asyncio
async def test_total_deadline_cancels_all_children_and_persists_timeout(
    context, monkeypatch
):
    routes.teacher_judge_settings.REQUEST_TIMEOUT_SECONDS = 0.15
    cancelled = []

    async def child(index):
        try:
            await asyncio.sleep(0.5)
        finally:
            cancelled.append(index)

    async def hanging_chat(*args, **kwargs):
        await asyncio.gather(child(1), child(2))
        return "不應完成", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", hanging_chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 504
    assert sorted(cancelled) == [1, 2]
    db, *_ = context
    rows = db.exec(select(TeacherJudgeSessionMessage)).all()
    assert len(rows) == 2
    failure = next(row for row in rows if row.role == TeacherJudgeMessageRole.assistant)
    assert failure.metadata_json["reason_code"] == "teacher_judge_workflow_timeout"
    assert failure.metadata_json["script_ready"] is False
    assert "已停止" in failure.content
    assert "rubric_proposal" not in failure.metadata_json
    assert (
        next(
            row for row in rows if row.role == TeacherJudgeMessageRole.user
        ).metadata_json["processing"]
        is False
    )
    await asyncio.sleep(0.04)
    assert len(db.exec(select(TeacherJudgeSessionMessage)).all()) == 2


@pytest.mark.asyncio
async def test_success_saves_recoverable_proposal_with_source_and_revision(
    context, monkeypatch
):
    proposal = [
        {"id": "version", "title": "版本", "operation": "add", "detectable": "manual"}
    ]

    async def chat(*args, **kwargs):
        return "請確認提案", proposal, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    result = await send(context)
    db, _, _, file, _ = context
    db.expire_all()
    saved = db.get(TeacherJudgeSessionMessage, uuid.UUID(result.assistant_message.id))
    assert saved.metadata_json["rubric_proposal"] == proposal
    assert saved.metadata_json["source_file_id"] == str(file.id)
    assert saved.metadata_json["analysis_revision"] == file.analysis_revision
    assert saved.metadata_json["is_refine"] is False
    assert result.user_message.metadata_json["processing"] is False


@pytest.mark.asyncio
async def test_clearing_chat_during_ai_does_not_resurrect_answer(context, monkeypatch):
    db, _, item, _, _ = context

    async def chat(*args, **kwargs):
        routes.clear_session_messages(db, item)
        return "晚到回覆", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 409
    assert not db.exec(select(TeacherJudgeSessionMessage)).all()


@pytest.mark.asyncio
async def test_real_itemwise_pipeline_cancels_active_and_waiting_items(
    context, monkeypatch
):
    started, cancelled = [], []
    routes.teacher_judge_settings.REQUEST_TIMEOUT_SECONDS = 0.15
    monkeypatch.setattr(service, "settings", SimpleNamespace(VLLM_MODEL_NAME="test"))

    async def extract(*args, **kwargs):
        return [{"source_index": i} for i in range(5)], None, {}

    async def analyze(*, source, **kwargs):
        started.append(source["source_index"])
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            cancelled.append(source["source_index"])
            raise

    monkeypatch.setattr(service, "extract_attachment_requirements", extract)
    monkeypatch.setattr(service, "analyze_requirement_item", analyze)
    monkeypatch.setattr(routes, "get_pending_attachments", lambda *args: [object()])
    monkeypatch.setattr(routes, "bind_attachments_to_message", lambda *args: None)
    monkeypatch.setattr(routes, "attachment_context", lambda *args: "附件")
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 504
    assert started == [0, 1]
    assert cancelled == [0, 1]
    await asyncio.sleep(0.05)
    assert started == [0, 1]


@pytest.mark.asyncio
async def test_cancellation_suppressed_by_model_cannot_publish_late_proposal(
    context, monkeypatch
):
    routes.teacher_judge_settings.REQUEST_TIMEOUT_SECONDS = 0.15

    async def chat(*args, **kwargs):
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return "晚到提案", [{"id": "late", "operation": "add"}], {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 504
    db, *_ = context
    assert all(
        "rubric_proposal" not in row.metadata_json
        for row in db.exec(select(TeacherJudgeSessionMessage)).all()
    )


@pytest.mark.asyncio
async def test_same_session_rejects_duplicate_while_previous_request_runs(
    context, monkeypatch
):
    entered, release = asyncio.Event(), asyncio.Event()

    async def chat(*args, **kwargs):
        entered.set()
        await release.wait()
        return "完成", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    task = asyncio.create_task(send(context))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(HTTPException) as error:
            await send(context)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "teacher_judge_request_in_progress"
    finally:
        release.set()
        await task
    db, *_ = context
    assert len(db.exec(select(TeacherJudgeSessionMessage)).all()) == 2


@pytest.mark.asyncio
async def test_dismissal_is_persistent_idempotent_and_scoped_to_session(
    context, monkeypatch
):
    async def chat(*args, **kwargs):
        return "請確認", [{"id": "new", "operation": "add"}], {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    result = await send(context)
    db, class_id, item, file, user = context
    other = TeacherJudgeSession(teaching_class_id=class_id, title="Other")
    db.add(other)
    db.commit()
    message_id = uuid.UUID(result.assistant_message.id)
    with pytest.raises(HTTPException) as error:
        routes.dismiss_message_proposal(class_id, other.id, message_id, db, user)
    assert error.value.status_code == 404
    for _ in range(2):
        routes.dismiss_message_proposal(class_id, item.id, message_id, db, user)
    db.expire_all()
    assert (
        db.get(TeacherJudgeSessionMessage, message_id).metadata_json[
            "proposal_dismissed"
        ]
        is True
    )
    assert file.analysis_revision == 1


@pytest.mark.asyncio
async def test_summary_scheduler_failure_does_not_hide_durable_success(
    context, monkeypatch
):
    async def chat(*args, **kwargs):
        return "完成", [{"id": "new", "operation": "add"}], {}

    def fail(*args, **kwargs):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    monkeypatch.setattr(routes, "schedule_summary", fail)
    result = await send(context)
    assert result.rubric_proposal[0]["id"] == "new"


@pytest.mark.asyncio
async def test_deadline_covers_multiple_calls_without_resetting_budget(
    context, monkeypatch
):
    routes.teacher_judge_settings.REQUEST_TIMEOUT_SECONDS = 0.35
    calls = []

    async def chat(*args, **kwargs):
        for index in range(4):
            calls.append(index)
            await asyncio.sleep(0.2)
        return "不應完成", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 504
    assert 0 < len(calls) < 4


@pytest.mark.asyncio
async def test_clear_then_timeout_does_not_recreate_failure_message(
    context, monkeypatch
):
    routes.teacher_judge_settings.REQUEST_TIMEOUT_SECONDS = 0.1
    db, _, item, _, _ = context

    async def chat(*args, **kwargs):
        routes.clear_session_messages(db, item)
        await asyncio.sleep(0.5)
        return "不應完成", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 504
    assert not db.exec(select(TeacherJudgeSessionMessage)).all()


@pytest.mark.asyncio
async def test_interrupted_request_does_not_block_retry_after_deadline(
    context, monkeypatch
):
    db, _, item, _, user = context
    pending = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.user,
        content="上次未完成",
        created_by=user.id,
        metadata_json={"processing": True, "processing_deadline": 1},
    )
    db.add(pending)
    db.commit()

    async def chat(*args, **kwargs):
        return "本次已完成", None, {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    result = await send(context)
    assert result.assistant_message.content == "本次已完成"


@pytest.mark.asyncio
async def test_revision_changed_during_ai_clears_processing_without_saving_proposal(
    context, monkeypatch
):
    db, _, _, file, _ = context

    async def chat(*args, **kwargs):
        file.analysis_revision += 1
        db.add(file)
        db.commit()
        return "舊版提案", [{"id": "new", "operation": "add"}], {}

    monkeypatch.setattr(routes, "chat_with_rubric", chat)
    with pytest.raises(HTTPException) as error:
        await send(context)
    assert error.value.status_code == 409
    rows = db.exec(select(TeacherJudgeSessionMessage)).all()
    assert len(rows) == 1
    assert rows[0].metadata_json["processing"] is False
