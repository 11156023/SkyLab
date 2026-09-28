"""Class-scoped persistent Teacher Judge session APIs."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, desc, select

from app.ai.monitoring import new_ai_request_id, record_ai_template_call, usage_metrics
from app.ai.teacher_judge.attachment_service import (
    MAX_ATTACHMENT_COUNT,
    attachment_context,
    attachment_public,
    create_attachment,
    delete_attachment,
    get_pending_attachments,
)
from app.ai.teacher_judge.automation_support import (
    ensure_script_generation_supported,
    get_script_generation_blockers,
)
from app.ai.teacher_judge.config import settings as teacher_judge_settings
from app.ai.teacher_judge.file_service import create_blank_file
from app.ai.teacher_judge.machine_context import (
    format_machine_context,
    load_class_machine_nodes,
    machine_context_entries,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRunBatchPublic,
    TeacherJudgeScriptRunCreateRequest,
    TeacherJudgeScriptRunPublic,
    TeacherJudgeScriptRunSummary,
    TeacherJudgeScriptSetPublic,
    TeacherJudgeScriptSetRunRequest,
    TeacherJudgeSessionAttachmentUploadResponse,
    TeacherJudgeSessionChatResponse,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionForkRequest,
    TeacherJudgeSessionMessageCreateRequest,
    TeacherJudgeSessionMessagePublic,
    TeacherJudgeSessionPublic,
    TeacherJudgeSessionScriptCreateRequest,
    TeacherJudgeSessionUpdateRequest,
    TeacherJudgeTargetReviewUpdate,
)
from app.ai.teacher_judge.script_artifact_service import (
    create_artifact_set,
    get_artifact_set,
    list_artifact_sets,
)
from app.ai.teacher_judge.script_executor_service import (
    execute_script_run,
    execute_script_run_batch,
)
from app.ai.teacher_judge.script_run_service import (
    create_script_run,
    create_script_run_batch,
    get_script_run_batch_public,
    get_script_run_public,
    get_session_run_for_review,
    get_session_run_record,
    list_session_run_summaries,
)
from app.ai.teacher_judge.script_run_service import (
    update_target_review as save_target_review,
)
from app.ai.teacher_judge.service import (
    analyze_attachments_itemwise,
    chat_with_rubric,
)
from app.ai.teacher_judge.session_chat_service import (
    build_assistant_metadata,
    prompt_template_scope,
    refine_readiness_workflow,
    uses_legacy_command_context,
    validate_proposal_machine_nodes,
)
from app.ai.teacher_judge.session_service import (
    WorkflowMessage,
    bounded_history,
    clear_session_messages,
    delete_session_data,
    ensure_active,
    ensure_selected_file_available,
    finalize_cleared_attachments,
    fork_session_data,
    get_session,
    message_attachments_by_message_ids,
    message_public,
    redact_message_content,
    require_selected_file,
    schedule_summary,
    script_blocker_workflow_message,
    selected_file_for_chat,
    session_public,
    session_public_many,
    validate_selected_file,
    workflow_error_message,
)
from app.ai.teacher_judge.template_command_service import get_enabled_template_commands
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.infrastructure.worker import submit
from app.models import TeachingClass, TeachingClassWeek
from app.models.base import get_datetime_utc
from app.models.teacher_judge_attachment import TeacherJudgeSessionAttachment
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import TeacherJudgeScriptRunTargetScope
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeMessageType,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)

router = APIRouter(
    prefix="/teaching-classes/{teaching_class_id}/judge/sessions",
    tags=["teacher-judge"],
)

logger = logging.getLogger(__name__)


def _is_selected_file_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig or exc).lower()
    return "uq_teacher_judge_sessions_selected_file" in message or (
        "teacher_judge_sessions" in message and "selected_file_id" in message
    )


def _selected_file_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "teacher_judge_file_in_use",
            "message": t("teacherJudgeSessions.selectedFileInUse"),
        },
    )


def _end_read_transaction(session: SessionDep) -> None:
    """在等待模型前結束目前的讀取交易，把 DB 連線還給連線池。

    暫時關掉 expire_on_commit：已讀進來的 ORM 物件（範本指令、附件）會在
    LLM 呼叫期間被讀取，若被標成過期，讀屬性時會在 await 途中重新開一段
    交易，等於白做。之後需要最新狀態的地方本來就會明確 refresh。
    """
    expire_on_commit = session.expire_on_commit
    session.expire_on_commit = False
    try:
        session.commit()
    finally:
        session.expire_on_commit = expire_on_commit


def _save_workflow_message(
    session: SessionDep,
    item: TeacherJudgeSession,
    *,
    content: str,
    metadata: dict[str, Any],
    created_by: uuid.UUID | None = None,
) -> TeacherJudgeSessionMessage | None:
    """Best-effort persistence for a safe, teacher-facing workflow outcome."""
    assistant = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.assistant,
        content=redact_message_content(content),
        message_type=TeacherJudgeMessageType.chat,
        metadata_json=metadata,
        created_by=created_by,
    )
    try:
        session.add(assistant)
        session.commit()
        session.refresh(assistant)
    except Exception:
        session.rollback()
        logger.exception(
            "Unable to persist Teacher Judge workflow message for session %s",
            item.id,
        )
        return None
    try:
        schedule_summary(session, item, boundary_message_id=assistant.id)
    except Exception:
        # The message is already durable; a summary scheduling failure must not
        # turn a successful workflow response into an API error.
        logger.exception(
            "Unable to schedule Teacher Judge summary for workflow message %s",
            assistant.id,
        )
    return assistant


def _record_chat_failure(
    session: SessionDep,
    item: TeacherJudgeSession,
    *,
    user_id: uuid.UUID,
    source_file_id: uuid.UUID | None,
    analysis_revision: int | None,
    ai_request_id: str,
    ai_started: float,
    ai_started_at: datetime,
    status_code: int | None,
    error_message: str,
) -> WorkflowMessage:
    """對話處理失敗時：留一則給老師看的失敗訊息，並記一筆失敗的 AI 呼叫。

    只負責記錄；要怎麼往外丟例外由呼叫端決定。
    """
    failure = workflow_error_message(
        stage="reanalysis",
        status_code=status_code,
        source_file_id=source_file_id,
        analysis_revision=analysis_revision,
    )
    _save_workflow_message(
        session,
        item,
        content=failure["content"],
        metadata=failure["metadata"],
        created_by=user_id,
    )
    record_ai_template_call(
        session=session,
        user_id=user_id,
        call_type="teacher_judge_chat",
        model_name=teacher_judge_settings.VLLM_MODEL_NAME,
        metrics=usage_metrics(
            {},
            perf_counter() - ai_started,
            request_id=ai_request_id,
            started_at=ai_started_at,
        ),
        status="error",
        error_message=error_message,
    )
    return failure


def _save_script_set_failure(
    session: SessionDep,
    item: TeacherJudgeSession,
    *,
    detail: Any,
    stage: str,
    status_code: int | None,
    source_file_id: uuid.UUID | str | None,
    analysis_revision: int | None,
    created_by: uuid.UUID | None,
) -> None:
    """Persist a bounded script-set failure for Chat/history projection."""
    detail_dict = detail if isinstance(detail, dict) else {}
    if isinstance(detail_dict.get("items"), list):
        outcome = script_blocker_workflow_message(
            detail_dict["items"],
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
        )
    else:
        reason_code = detail_dict.get("code")
        if reason_code == "teacher_judge_analysis_revision_conflict":
            reason_code = "analysis_revision_conflict"
        outcome = workflow_error_message(
            stage=stage,
            status_code=status_code,
            source_file_id=source_file_id,
            analysis_revision=analysis_revision,
            reason_code=reason_code if isinstance(reason_code, str) else None,
        )
    _save_workflow_message(
        session,
        item,
        content=outcome["content"],
        metadata=outcome["metadata"],
        created_by=created_by,
    )


def _access(db: SessionDep, class_id: uuid.UUID, user: InstructorUser) -> None:
    teaching_class = db.get(TeachingClass, class_id)
    if not teaching_class:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.classNotFound")
        )
    require_teaching_access(user, teaching_class.owner_id)


def _validate_week(
    db: SessionDep, class_id: uuid.UUID, week_id: uuid.UUID | None
) -> None:
    if week_id is None:
        return
    week = db.get(TeachingClassWeek, week_id)
    if week is None or week.class_id != class_id:
        raise HTTPException(
            status_code=400, detail=t("teacherJudgeSessions.weekNotInClass")
        )


@router.get("/", response_model=list[TeacherJudgeSessionPublic])
def list_sessions(
    teaching_class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    status: TeacherJudgeSessionStatus = TeacherJudgeSessionStatus.active,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[TeacherJudgeSessionPublic]:
    _access(session, teaching_class_id, current_user)
    rows = session.exec(
        select(TeacherJudgeSession)
        .where(
            TeacherJudgeSession.teaching_class_id == teaching_class_id,
            TeacherJudgeSession.status == status,
        )
        .order_by(
            desc(case((col(TeacherJudgeSession.pinned_at).is_not(None), 1), else_=0)),
            desc(TeacherJudgeSession.pinned_at),
            desc(TeacherJudgeSession.last_activity_at),
            desc(TeacherJudgeSession.id),
        )
        .offset(skip)
        .limit(limit)
    ).all()
    return session_public_many(session, list(rows))


@router.post("/", response_model=TeacherJudgeSessionPublic)
def create_session(
    teaching_class_id: uuid.UUID,
    payload: TeacherJudgeSessionCreateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    _access(session, teaching_class_id, current_user)
    try:
        _validate_week(session, teaching_class_id, payload.teaching_class_week_id)
        selected_file_id = payload.selected_file_id
        if payload.creation_mode == "blank":
            rubric = create_blank_file(
                session=session,
                teaching_class_id=teaching_class_id,
                created_by=current_user.id,
                display_name=payload.rubric_name or "檢查表",
                environment_keys=payload.environment_keys or [],
            )
            selected_file_id = rubric.id
        else:
            validate_selected_file(session, teaching_class_id, selected_file_id)
            if selected_file_id is not None:
                ensure_selected_file_available(session, selected_file_id)
        item = TeacherJudgeSession(
            teaching_class_id=teaching_class_id,
            teaching_class_week_id=payload.teaching_class_week_id,
            title=payload.title.strip(),
            selected_file_id=selected_file_id,
            created_by=current_user.id,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return session_public(session, item)
    except IntegrityError as exc:
        session.rollback()
        if not _is_selected_file_conflict(exc):
            raise
        raise _selected_file_conflict() from exc
    except Exception:
        session.rollback()
        raise


@router.post("/{session_id}/fork", response_model=TeacherJudgeSessionPublic)
def fork_session(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: TeacherJudgeSessionForkRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    _access(session, teaching_class_id, current_user)
    source = get_session(session, teaching_class_id, session_id)
    cloned = fork_session_data(
        session,
        source,
        title=payload.title,
        created_by=current_user.id,
    )
    return session_public(session, cloned)


@router.get("/{session_id}", response_model=TeacherJudgeSessionPublic)
def get_session_detail(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    _access(session, teaching_class_id, current_user)
    return session_public(session, get_session(session, teaching_class_id, session_id))


@router.patch("/{session_id}", response_model=TeacherJudgeSessionPublic)
def update_session(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: TeacherJudgeSessionUpdateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    changes = payload.model_fields_set
    if item.status == TeacherJudgeSessionStatus.archived and changes - {"status"}:
        raise HTTPException(
            status_code=409, detail=t("teacherJudgeSessions.archivedReadOnly")
        )
    if "title" in changes and payload.title is not None:
        item.title = payload.title.strip()
    if "teaching_class_week_id" in changes:
        _validate_week(session, teaching_class_id, payload.teaching_class_week_id)
        item.teaching_class_week_id = payload.teaching_class_week_id
    cleared_attachments: list[TeacherJudgeSessionAttachment] = []
    if "selected_file_id" in changes:
        validate_selected_file(session, teaching_class_id, payload.selected_file_id)
        if payload.selected_file_id is not None:
            ensure_selected_file_available(
                session,
                payload.selected_file_id,
                exclude_session_id=item.id,
            )
        if payload.selected_file_id != item.selected_file_id:
            cleared_attachments = clear_session_messages(session, item, commit=False)
        item.selected_file_id = payload.selected_file_id
    if payload.status is not None:
        item.status = TeacherJudgeSessionStatus(payload.status)
        if item.status == TeacherJudgeSessionStatus.archived:
            item.pinned_at = None
    if payload.is_pinned is not None:
        if item.status == TeacherJudgeSessionStatus.archived and payload.is_pinned:
            raise HTTPException(
                status_code=409, detail=t("teacherJudgeSessions.archivedCannotPin")
            )
        item.pinned_at = get_datetime_utc() if payload.is_pinned else None

    item.updated_at = get_datetime_utc()
    item.last_activity_at = item.updated_at
    session.add(item)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if not _is_selected_file_conflict(exc):
            raise
        raise _selected_file_conflict() from exc
    session.refresh(item)
    if cleared_attachments:
        finalize_cleared_attachments(cleared_attachments)
    return session_public(session, item)


@router.post("/{session_id}/archive", response_model=TeacherJudgeSessionPublic)
def archive_session(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    return update_session(
        teaching_class_id,
        session_id,
        TeacherJudgeSessionUpdateRequest(status="archived"),
        session,
        current_user,
    )


@router.delete("/{session_id}", status_code=204)
def delete_session(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> None:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    delete_session_data(session, item)


@router.post(
    "/{session_id}/attachments",
    response_model=TeacherJudgeSessionAttachmentUploadResponse,
)
def upload_session_attachment(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
) -> TeacherJudgeSessionAttachmentUploadResponse:
    # 刻意寫成同步 route：附件解析（pdfplumber 最多 200 頁、.doc 走 LibreOffice
    # 最長 30 秒）、寫檔與 DB commit 都是阻塞工作，交給 FastAPI 的 threadpool
    # 跑整個 handler，才不會卡住 event loop 上的其他請求與 VNC／教室 WebSocket。
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    pending_count = session.exec(
        select(func.count())
        .select_from(TeacherJudgeSessionAttachment)
        .where(
            TeacherJudgeSessionAttachment.session_id == item.id,
            col(TeacherJudgeSessionAttachment.message_id).is_(None),
        )
    ).one()
    if pending_count >= MAX_ATTACHMENT_COUNT:
        raise HTTPException(
            status_code=400,
            detail=t(
                "teacherJudgeSessions.attachmentLimit", count=MAX_ATTACHMENT_COUNT
            ),
        )
    # 有上限地讀取：多讀 1 byte 即可讓 create_attachment 判定超限，
    # 不必先把整個（可能超大的）上傳檔載入記憶體
    max_upload_bytes = teacher_judge_settings.VLLM_MAX_UPLOAD_SIZE_MB * 1024 * 1024
    file_bytes = file.file.read(max_upload_bytes + 1)
    try:
        attachment = create_attachment(
            session,
            session_id=item.id,
            uploaded_by=current_user.id,
            filename=file.filename,
            media_type=file.content_type,
            file_bytes=file_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return TeacherJudgeSessionAttachmentUploadResponse(
        attachment=attachment_public(attachment)
    )


@router.delete("/{session_id}/attachments/{attachment_id}", status_code=204)
def delete_session_attachment(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> None:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    attachment = session.get(TeacherJudgeSessionAttachment, attachment_id)
    if not attachment or attachment.session_id != item.id:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.attachmentNotFound")
        )
    delete_attachment(session, attachment)


@router.get(
    "/{session_id}/messages", response_model=list[TeacherJudgeSessionMessagePublic]
)
def list_messages(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    before: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> list[TeacherJudgeSessionMessagePublic]:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    query = select(TeacherJudgeSessionMessage).where(
        TeacherJudgeSessionMessage.session_id == session_id
    )
    if before:
        cursor = session.get(TeacherJudgeSessionMessage, before)
        if not cursor or cursor.session_id != session_id:
            raise HTTPException(
                status_code=400, detail=t("teacherJudgeSessions.invalidMessageCursor")
            )
        query = query.where(
            (TeacherJudgeSessionMessage.created_at < cursor.created_at)
            | (
                (TeacherJudgeSessionMessage.created_at == cursor.created_at)
                & (TeacherJudgeSessionMessage.id < cursor.id)
            )
        )
    rows = list(
        session.exec(
            query.order_by(
                desc(TeacherJudgeSessionMessage.created_at),
                desc(TeacherJudgeSessionMessage.id),
            ).limit(limit)
        )
    )
    rows.reverse()
    attachments_by_message_id = message_attachments_by_message_ids(
        session, [row.id for row in rows]
    )
    return [
        message_public(row, attachments_by_message_id.get(row.id, [])) for row in rows
    ]


@router.delete("/{session_id}/messages", response_model=TeacherJudgeSessionPublic)
def clear_messages(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionPublic:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    clear_session_messages(session, item)
    return session_public(session, item)


@router.post("/{session_id}/messages", response_model=TeacherJudgeSessionChatResponse)
async def create_message(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: TeacherJudgeSessionMessageCreateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeSessionChatResponse:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    file = selected_file_for_chat(session, item)
    base_revision = file.analysis_revision if file else None
    if (
        file
        and payload.analysis_revision is not None
        and payload.analysis_revision != file.analysis_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_analysis_revision_conflict",
                "message": t("teacherJudgeSessions.analysisRevisionConflict"),
                "analysis_revision": file.analysis_revision,
            },
        )
    if not payload.content.strip() and not payload.attachment_ids:
        raise HTTPException(
            status_code=422,
            detail=t("teacherJudgeSessions.messageOrAttachmentRequired"),
        )
    attachments = get_pending_attachments(session, item.id, payload.attachment_ids)
    user_message = TeacherJudgeSessionMessage(
        session_id=item.id,
        role=TeacherJudgeMessageRole.user,
        content=redact_message_content(payload.content.strip()),
        metadata_json={"ui_hidden": True} if payload.is_refine else {},
        created_by=current_user.id,
    )
    session.add(user_message)
    session.flush()
    for attachment in attachments:
        attachment.message_id = user_message.id
        session.add(attachment)
    session.commit()
    session.refresh(user_message)
    ai_request_id = new_ai_request_id()
    ai_started = perf_counter()
    ai_started_at = datetime.now(timezone.utc)
    try:
        legacy_command_context = uses_legacy_command_context(
            file.analysis_json if file else None
        )
        template_commands = (
            get_enabled_template_commands(
                session,
                file.template_key if file else "linux",
                include_cross_template=True,
            )
            if legacy_command_context
            else []
        )
        rubric_context = (
            json.dumps(file.analysis_json, ensure_ascii=False) if file else "{}"
        )
        machine_entries = machine_context_entries(session, teaching_class_id)
        machine_context = format_machine_context(machine_entries)
        item_results: list[dict[str, Any]] | None = None
        conversation_focus: dict[str, Any] | None = None
        itemwise_error: str | None = None
        tool_calls: Any = None
        # 所有要餵給模型的資料都先從 DB 讀成區域變數，再結束讀取交易才開始等
        # 模型；否則這條連線會 idle in transaction 整段 LLM 呼叫（可能數分鐘），
        # PgBouncer transaction pooling 下每個請求都佔住一條 server 連線。
        template_key, environment_keys = prompt_template_scope(
            file, legacy_command_context
        )
        prompt_attachment_context = attachment_context(attachments)
        use_itemwise = bool(attachments) and not payload.is_refine
        history = (
            None
            if use_itemwise
            else bounded_history(
                session,
                item.id,
                exclude_attachments_for_message_id=user_message.id,
                summary=item.summary,
                source_file_id=file.id if file else None,
                analysis_revision=base_revision,
                summary_through_message_id=item.summary_through_message_id,
            )
        )
        _end_read_transaction(session)
        if use_itemwise:
            # Attachment analysis runs itemwise: extract source rows first, then
            # judge each row through the same isolated single-item chat core so
            # one row's Ready reasoning cannot leak into the other rows.
            itemwise = await analyze_attachments_itemwise(
                rubric_context=rubric_context,
                template_key=template_key,
                template_commands=template_commands,
                environment_keys=environment_keys,
                machine_context=machine_context,
                machine_entries=machine_entries,
                attachment_context=prompt_attachment_context,
                analysis_revision=base_revision,
                rubric_available=file is not None,
            )
            reply, proposal, metrics = (
                itemwise.reply,
                itemwise.proposal,
                itemwise.metrics,
            )
            item_results = itemwise.item_results
            itemwise_error = getattr(itemwise, "error", None)
            if itemwise_error:
                reply = (
                    "這次無法逐項核查附件，處理階段沒有完成；請確認附件內容後再試一次。"
                )
        else:
            chat_result = await chat_with_rubric(
                history or [],
                rubric_context,
                is_refine=payload.is_refine,
                template_key=template_key,
                template_commands=template_commands,
                environment_keys=environment_keys,
                machine_context=machine_context,
                machine_entries=machine_entries,
                attachment_context=prompt_attachment_context,
                analysis_revision=base_revision,
                rubric_available=file is not None,
            )
            reply, proposal, metrics = chat_result
            focus = getattr(chat_result, "conversation_focus", None)
            if isinstance(focus, dict):
                conversation_focus = focus
            tool_calls = getattr(chat_result, "tool_calls", None)
        # Without a selected rubric the conversation is general assistance only;
        # do not let an unconstrained model response create an unreviewed proposal.
        if file is None and proposal:
            reply = (
                "這項需求已具備自動檢查條件，但目前尚未選擇檢查表來源，"
                "因此無法建立可套用提案。請先選擇來源後再送出需求。"
            )
            proposal = None
        if proposal and file is not None:
            validate_proposal_machine_nodes(session, teaching_class_id, proposal)
        workflow: WorkflowMessage | None = None
        if payload.is_refine and file is not None:
            workflow = refine_readiness_workflow(
                session,
                teaching_class_id=teaching_class_id,
                session_id=item.id,
                file=file,
                proposal=proposal,
                template_commands=template_commands,
                reply=reply,
                analysis_revision=base_revision,
            )
            reply = workflow["content"]
        assistant = TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=redact_message_content(reply),
            message_type=TeacherJudgeMessageType.chat,
            metadata_json=build_assistant_metadata(
                metrics=metrics,
                workflow=workflow,
                item_results=item_results,
                itemwise_error=itemwise_error,
                conversation_focus=conversation_focus,
                tool_calls=tool_calls,
                source_file_id=file.id if file else None,
                analysis_revision=base_revision,
            ),
        )
    except HTTPException as exc:
        failure = _record_chat_failure(
            session,
            item,
            user_id=current_user.id,
            source_file_id=file.id if file else None,
            analysis_revision=base_revision,
            ai_request_id=ai_request_id,
            ai_started=ai_started,
            ai_started_at=ai_started_at,
            status_code=exc.status_code,
            error_message=f"http_{exc.status_code}",
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=failure["content"],
            headers=exc.headers,
        ) from exc
    except Exception as exc:
        logger.exception(
            "Teacher Judge message processing failed for session %s", item.id
        )
        _record_chat_failure(
            session,
            item,
            user_id=current_user.id,
            source_file_id=file.id if file else None,
            analysis_revision=base_revision,
            ai_request_id=ai_request_id,
            ai_started=ai_started,
            ai_started_at=ai_started_at,
            status_code=None,
            error_message=str(exc),
        )
        raise
    record_ai_template_call(
        session=session,
        user_id=current_user.id,
        call_type="teacher_judge_chat",
        model_name=teacher_judge_settings.VLLM_MODEL_NAME,
        metrics={
            **metrics,
            "request_id": ai_request_id,
            "usage_reported": bool(metrics.get("usage_reported", False)),
            "response_model": metrics.get("response_model"),
            "started_at": ai_started_at,
            "completed_at": datetime.now(timezone.utc),
        },
    )
    # Source changes clear the conversation while this request may still be
    # waiting on the model.  Revalidate before saving the generated answer so
    # an old response cannot be attached to the new rubric context.
    session.refresh(item)
    ensure_active(item)
    current_file = selected_file_for_chat(session, item)
    if current_file is not None:
        session.refresh(current_file)
        current_file = selected_file_for_chat(session, item)
    if (current_file.id if current_file else None) != (file.id if file else None) or (
        current_file.analysis_revision if current_file else None
    ) != base_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_context_changed",
                "message": t("teacherJudgeSessions.analysisRevisionConflict"),
                "analysis_revision": current_file.analysis_revision
                if current_file
                else None,
            },
        )
    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add_all([assistant, item])
    session.commit()
    session.refresh(assistant)
    schedule_summary(session, item, boundary_message_id=assistant.id)
    return TeacherJudgeSessionChatResponse(
        user_message=message_public(user_message, attachments),
        assistant_message=message_public(assistant),
        rubric_proposal=([] if payload.is_refine and proposal is None else proposal),
        base_revision=base_revision,
    )


def _session_rubric_for_script_set(
    *,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    expected_revision: int | None,
) -> tuple[TeacherJudgeSession, Any, TeacherJudgeRubricAnalysis]:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    file = require_selected_file(session, item)
    if expected_revision is not None and expected_revision != file.analysis_revision:
        conflict = HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_analysis_revision_conflict",
                "message": t("teacherJudgeSessions.analysisRevisionConflict"),
                "analysis_revision": file.analysis_revision,
            },
        )
        _save_script_set_failure(
            session,
            item,
            detail=conflict.detail,
            stage="persistence",
            status_code=conflict.status_code,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise conflict
    rubric_analysis = TeacherJudgeRubricAnalysis.model_validate(file.analysis_json)
    commands = get_enabled_template_commands(
        session,
        file.template_key,
        include_cross_template=True,
    )
    if not rubric_analysis.items:
        not_ready = HTTPException(
            status_code=422,
            detail={
                "code": "teacher_judge_script_not_ready",
                "message": t("teacherJudgeSessions.noScriptableItems"),
                "items": get_script_generation_blockers(
                    rubric_analysis,
                    commands,
                    require_target_node=bool(
                        load_class_machine_nodes(session, teaching_class_id)
                    ),
                ),
            },
        )
        _save_script_set_failure(
            session,
            item,
            detail=not_ready.detail,
            stage="script_preflight",
            status_code=not_ready.status_code,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise not_ready
    try:
        ensure_script_generation_supported(
            rubric_analysis,
            commands,
            require_target_node=bool(
                load_class_machine_nodes(session, teaching_class_id)
            ),
            require_typed_plan=True,
        )
    except HTTPException as exc:
        _save_script_set_failure(
            session,
            item,
            detail=exc.detail,
            stage="script_preflight",
            status_code=exc.status_code,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise
    return item, file, rubric_analysis


@router.post(
    "/{session_id}/script-sets",
    response_model=TeacherJudgeScriptSetPublic,
)
async def create_session_script_set(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    payload: TeacherJudgeSessionScriptCreateRequest | None = None,
) -> TeacherJudgeScriptSetPublic:
    item, file, rubric_analysis = _session_rubric_for_script_set(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        session=session,
        current_user=current_user,
        expected_revision=payload.analysis_revision if payload else None,
    )
    return await _generate_script_set(
        session=session,
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        item=item,
        file=file,
        rubric_analysis=rubric_analysis,
        current_user=current_user,
        log_label="creation",
    )


async def _generate_script_set(
    *,
    session: SessionDep,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    item: Any,
    file: Any,
    rubric_analysis: TeacherJudgeRubricAnalysis,
    current_user: InstructorUser,
    log_label: str,
    artifact_set_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptSetPublic:
    """產生（或重新產生）script set；失敗一律回滾並在 session 上留下失敗紀錄。"""
    try:
        script_set = create_artifact_set(
            session=session,
            teaching_class_id=teaching_class_id,
            session_id=session_id,
            name=item.title,
            template_key=file.template_key,
            rubric_analysis=rubric_analysis,
            source_analysis_revision=file.analysis_revision,
            created_by=current_user.id,
            source_file_id=file.id,
            artifact_set_id=artifact_set_id,
        )
    except HTTPException as exc:
        session.rollback()
        _save_script_set_failure(
            session,
            item,
            detail=exc.detail,
            stage="script_generation",
            status_code=exc.status_code,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise
    except Exception:
        session.rollback()
        logger.exception(
            "Teacher Judge script set %s failed for session %s", log_label, item.id
        )
        _save_script_set_failure(
            session,
            item,
            detail=None,
            stage="script_generation",
            status_code=None,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise
    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add(item)
    session.commit()
    return script_set


@router.get(
    "/{session_id}/script-sets",
    response_model=list[TeacherJudgeScriptSetPublic],
)
def list_session_script_sets(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[TeacherJudgeScriptSetPublic]:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    return list_artifact_sets(
        session=session,
        teaching_class_id=teaching_class_id,
        session_id=session_id,
    )


@router.get(
    "/{session_id}/script-sets/{artifact_set_id}",
    response_model=TeacherJudgeScriptSetPublic,
)
def get_session_script_set(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptSetPublic:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    return get_artifact_set(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_set_id=artifact_set_id,
        session_id=session_id,
    )


@router.post(
    "/{session_id}/script-sets/{artifact_set_id}/regenerate",
    response_model=TeacherJudgeScriptSetPublic,
)
async def regenerate_session_script_set(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    payload: TeacherJudgeSessionScriptCreateRequest | None = None,
) -> TeacherJudgeScriptSetPublic:
    item, file, rubric_analysis = _session_rubric_for_script_set(
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        session=session,
        current_user=current_user,
        expected_revision=payload.analysis_revision if payload else None,
    )
    existing = get_artifact_set(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_set_id=artifact_set_id,
        session_id=session_id,
    )
    if existing.source_file_id != str(file.id):
        mismatch = HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_script_set_source_mismatch",
                "message": t("teacherJudgeSessions.scriptSetSourceMismatch"),
            },
        )
        _save_script_set_failure(
            session,
            item,
            detail=mismatch.detail,
            stage="persistence",
            status_code=mismatch.status_code,
            source_file_id=file.id,
            analysis_revision=file.analysis_revision,
            created_by=current_user.id,
        )
        raise mismatch
    return await _generate_script_set(
        session=session,
        teaching_class_id=teaching_class_id,
        session_id=session_id,
        item=item,
        file=file,
        rubric_analysis=rubric_analysis,
        current_user=current_user,
        log_label="regeneration",
        artifact_set_id=artifact_set_id,
    )


@router.post(
    "/{session_id}/script-sets/{artifact_set_id}/runs",
    response_model=TeacherJudgeRunBatchPublic,
)
def create_session_script_set_run(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    payload: TeacherJudgeScriptSetRunRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeRunBatchPublic:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    get_artifact_set(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_set_id=artifact_set_id,
        session_id=session_id,
    )
    if payload.target_scope != "all_students_in_set":
        raise HTTPException(
            status_code=422, detail=t("teacherJudgeSessions.unsupportedRunScope")
        )
    batch = create_script_run_batch(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_set_id=artifact_set_id,
        started_by=current_user.id,
        session_id=session_id,
    )
    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add(item)
    session.commit()
    submit(
        execute_script_run_batch(uuid.UUID(batch.run_batch_id)),
        name=f"teacher_judge_script_run_batch:{batch.run_batch_id}",
        task_id=f"teacher_judge_script_run_batch:{batch.run_batch_id}",
    )
    return get_script_run_batch_public(
        session=session,
        teaching_class_id=teaching_class_id,
        run_batch_id=uuid.UUID(batch.run_batch_id),
        session_id=session_id,
    )


@router.get(
    "/{session_id}/run-batches/{run_batch_id}",
    response_model=TeacherJudgeRunBatchPublic,
)
def get_session_script_run_batch(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_batch_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeRunBatchPublic:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    return get_script_run_batch_public(
        session=session,
        teaching_class_id=teaching_class_id,
        run_batch_id=run_batch_id,
        session_id=session_id,
    )


@router.get("/{session_id}/runs", response_model=list[TeacherJudgeScriptRunSummary])
def list_session_runs(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> list[TeacherJudgeScriptRunSummary]:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    return list_session_run_summaries(
        session, teaching_class_id, session_id, skip, limit
    )


@router.get("/{session_id}/runs/{run_id}", response_model=TeacherJudgeScriptRunPublic)
def get_session_run(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    return get_session_run_for_review(session, teaching_class_id, session_id, run_id)


def _update_target_review(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    payload: TeacherJudgeTargetReviewUpdate,
    session: SessionDep,
    current_user: InstructorUser,
    *,
    vmid: int | None = None,
    student_id: str | None = None,
) -> TeacherJudgeScriptRunPublic:
    _access(session, teaching_class_id, current_user)
    get_session(session, teaching_class_id, session_id)
    run = get_session_run_record(session, teaching_class_id, session_id, run_id)
    return save_target_review(
        session,
        run,
        vmid=vmid,
        student_id=student_id,
        feedback=payload.feedback,
        decisions=dict(payload.decisions),
        reviewer_id=current_user.id,
    )


@router.patch(
    "/{session_id}/runs/{run_id}/targets/{vmid}/review",
    response_model=TeacherJudgeScriptRunPublic,
)
def update_target_review(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    vmid: int,
    payload: TeacherJudgeTargetReviewUpdate,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    """Save a review for a target that has an assigned VM."""

    return _update_target_review(
        teaching_class_id,
        session_id,
        run_id,
        payload,
        session,
        current_user,
        vmid=vmid,
    )


@router.patch(
    "/{session_id}/runs/{run_id}/students/{student_id}/review",
    response_model=TeacherJudgeScriptRunPublic,
)
def update_student_target_review(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    student_id: str,
    payload: TeacherJudgeTargetReviewUpdate,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    """Save a review when preflight failed before a VMID was available."""

    return _update_target_review(
        teaching_class_id,
        session_id,
        run_id,
        payload,
        session,
        current_user,
        student_id=student_id,
    )


@router.post(
    "/{session_id}/scripts/{artifact_id}/runs",
    response_model=TeacherJudgeScriptRunPublic,
)
def create_session_run(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    artifact_id: uuid.UUID,
    payload: TeacherJudgeScriptRunCreateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    if (
        not artifact
        or artifact.teaching_class_id != teaching_class_id
        or artifact.session_id != session_id
    ):
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.scriptNotFound")
        )
    run = create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
        target_scope=TeacherJudgeScriptRunTargetScope(payload.target_scope),
        target_vmids=payload.target_vmids,
        started_by=current_user.id,
        target_node_key=payload.target_node_key,
    )
    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add(item)
    session.commit()
    submit(
        execute_script_run(uuid.UUID(run.id)),
        name=f"teacher_judge_script_run:{run.id}",
        task_id=f"teacher_judge_script_run:{run.id}",
    )
    return get_script_run_public(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
        run_id=uuid.UUID(run.id),
    )
