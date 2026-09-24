"""Class-scoped persistent Teacher Judge session APIs."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, desc, select

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
    rubric_item_machine_issues,
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
    _run_to_public,
    create_script_run,
    create_script_run_batch,
    get_script_run_batch_public,
    get_script_run_public,
)
from app.ai.teacher_judge.service import (
    TeacherJudgeChatResult,
    analyze_attachments_itemwise,
    chat_with_rubric,
)
from app.ai.teacher_judge.session_service import (
    WorkflowMessage,
    apply_proposal_operations_to_analysis,
    bounded_history,
    clear_session_messages,
    conversation_focus_from_item_results,
    delete_session_data,
    ensure_active,
    ensure_selected_file_available,
    finalize_cleared_attachments,
    fork_session_data,
    get_session,
    message_attachments_by_message_ids,
    message_public,
    normalize_workflow_item_results,
    reanalysis_workflow_message,
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
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunTargetScope,
)
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
            cleared_attachments = clear_session_messages(
                session, item, commit=False
            )
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
async def upload_session_attachment(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
) -> TeacherJudgeSessionAttachmentUploadResponse:
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
            detail=f"單次最多準備 {MAX_ATTACHMENT_COUNT} 個附件。",
        )
    # 有上限地讀取：多讀 1 byte 即可讓 create_attachment 判定超限，
    # 不必先把整個（可能超大的）上傳檔載入記憶體
    max_upload_bytes = teacher_judge_settings.VLLM_MAX_UPLOAD_SIZE_MB * 1024 * 1024
    file_bytes = await file.read(max_upload_bytes + 1)
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
        raise HTTPException(status_code=404, detail="找不到附件。")
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
        message_public(row, attachments_by_message_id.get(row.id, []))
        for row in rows
    ]


@router.delete(
    "/{session_id}/messages", response_model=TeacherJudgeSessionPublic
)
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
        raise HTTPException(status_code=422, detail="訊息或附件至少需要一項。")
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
    try:
        raw_analysis = file.analysis_json if file else {}
        legacy_command_context = any(
            isinstance(step, dict)
            and (step.get("template_key") or step.get("command_key"))
            for raw_item in (raw_analysis.get("items") or [])
            if isinstance(raw_item, dict)
            for step in (raw_item.get("check_steps") or [])
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
        chat_result: TeacherJudgeChatResult | None = None
        if attachments and not payload.is_refine:
            # Attachment analysis runs itemwise: extract source rows first, then
            # judge each row through the same isolated single-item chat core so
            # one row's Ready reasoning cannot leak into the other rows.
            itemwise = await analyze_attachments_itemwise(
                rubric_context=rubric_context,
                template_key=(file.template_key if legacy_command_context else "linux")
                if file
                else "linux",
                template_commands=template_commands,
                environment_keys=(file.environment_keys if legacy_command_context else None)
                if file
                else None,
                machine_context=machine_context,
                machine_entries=machine_entries,
                attachment_context=attachment_context(attachments),
                analysis_revision=base_revision,
                rubric_available=file is not None,
            )
            reply, proposal, metrics = itemwise.reply, itemwise.proposal, itemwise.metrics
            item_results = itemwise.item_results
            itemwise_error = getattr(itemwise, "error", None)
            if itemwise_error:
                reply = "這次無法逐項核查附件，處理階段沒有完成；請確認附件內容後再試一次。"
        else:
            chat_result = await chat_with_rubric(
                bounded_history(
                    session,
                    item.id,
                    exclude_attachments_for_message_id=user_message.id,
                    summary=item.summary,
                    source_file_id=file.id if file else None,
                    analysis_revision=base_revision,
                    summary_through_message_id=item.summary_through_message_id,
                ),
                rubric_context,
                is_refine=payload.is_refine,
                template_key=(file.template_key if legacy_command_context else "linux")
                if file
                else "linux",
                template_commands=template_commands,
                environment_keys=(file.environment_keys if legacy_command_context else None)
                if file
                else None,
                machine_context=machine_context,
                machine_entries=machine_entries,
                attachment_context=attachment_context(attachments),
                analysis_revision=base_revision,
                rubric_available=file is not None,
            )
            reply, proposal, metrics = chat_result
            focus = getattr(chat_result, "conversation_focus", None)
            if isinstance(focus, dict):
                conversation_focus = focus
        # Without a selected rubric the conversation is general assistance only;
        # do not let an unconstrained model response create an unreviewed proposal.
        if file is None and proposal:
            reply = (
                "這項需求已具備自動檢查條件，但目前尚未選擇檢查表來源，"
                "因此無法建立可套用提案。請先選擇來源後再送出需求。"
            )
            proposal = None
        if proposal and file is not None:
            class_nodes = load_class_machine_nodes(session, teaching_class_id)
            valid_node_keys = {node.node_key for node in class_nodes}
            invalid_node_keys: set[str] = set()
            missing_target_item_ids: list[str] = []
            machine_contract_issues: dict[str, list[str]] = {}
            for raw in proposal:
                if not isinstance(raw, dict):
                    continue
                candidate = raw.get("item")
                candidate = candidate if isinstance(candidate, dict) else raw
                node_key = str(candidate.get("target_node_key") or "").strip()
                if node_key and node_key not in valid_node_keys:
                    invalid_node_keys.add(node_key)
                peer_node_key = str(candidate.get("peer_node_key") or "").strip()
                if peer_node_key and peer_node_key not in valid_node_keys:
                    invalid_node_keys.add(peer_node_key)
                item_issues = rubric_item_machine_issues(candidate)
                if item_issues:
                    machine_contract_issues[
                        str(candidate.get("id") or candidate.get("title") or "未命名項目")
                    ] = item_issues
                if (
                    class_nodes
                    and str(candidate.get("detectable") or "").strip().lower() == "auto"
                    and not node_key
                ):
                    missing_target_item_ids.append(
                        str(candidate.get("id") or candidate.get("title") or "未命名項目")
                    )
            if invalid_node_keys:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "teacher_judge_target_node_not_in_class",
                        "message": "提案中的 target_node_key 不屬於目前班級。",
                        "target_node_keys": sorted(invalid_node_keys),
                    },
                )
            if missing_target_item_ids:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "teacher_judge_target_node_required",
                        "message": "可執行的提案項目必須指定 target_node_key。",
                        "item_ids": list(dict.fromkeys(missing_target_item_ids)),
                    },
                )
            if machine_contract_issues:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "teacher_judge_machine_contract_invalid",
                        "message": "提案中的執行節點、觀察節點或 peer token 不一致。",
                        "items": machine_contract_issues,
                    },
                )
        workflow: WorkflowMessage | None = None
        if payload.is_refine and file is not None:
            # Readiness is determined from the effective server-side candidate,
            # not from model prose or a duplicated frontend approximation.
            base_analysis = TeacherJudgeRubricAnalysis.model_validate(file.analysis_json)
            candidate_analysis = apply_proposal_operations_to_analysis(
                base_analysis,
                proposal,
            )
            readiness_nodes = load_class_machine_nodes(session, teaching_class_id)
            blockers = get_script_generation_blockers(
                candidate_analysis,
                template_commands,
                require_target_node=bool(readiness_nodes),
                require_typed_plan=True,
            )
            if blockers:
                logger.warning(
                    "Teacher Judge script readiness blocked: session=%s source_file=%s "
                    "revision=%s blockers=%s",
                    item.id,
                    file.id,
                    base_revision,
                    [
                        {
                            "item_id": blocker.get("item_id"),
                            "status": blocker.get("status"),
                            "reason_code": blocker.get("reason_code"),
                        }
                        for blocker in blockers
                    ],
                )
            workflow = reanalysis_workflow_message(
                blockers,
                source_file_id=file.id,
                analysis_revision=base_revision,
                proposal=proposal,
                assistant_reply=reply,
            )
            reply = workflow["content"]
            item_results = workflow["metadata"].get("item_results")
            conversation_focus = workflow["metadata"].get("conversation_focus")

        message_metadata: dict[str, Any] = {"metrics": metrics}
        if workflow is not None:
            message_metadata.update(workflow["metadata"])
        elif item_results is not None:
            item_results = normalize_workflow_item_results(item_results)
            if itemwise_error:
                item_results = [
                    {
                        "item_id": "attachment-analysis",
                        "title": "附件逐項核查",
                        "status": "analysis_error",
                        "missing_information": [],
                        "reason_code": "teacher_judge_attachment_analysis_failed",
                        "detail": itemwise_error,
                    }
                ]
            message_metadata["item_results"] = item_results
            message_metadata["conversation_focus"] = conversation_focus_from_item_results(
                item_results,
                source_file_id=file.id if file else None,
                analysis_revision=base_revision,
                turn_kind="follow_up",
            )
            message_metadata.update(
                {
                    "status": (
                        "analysis_error"
                        if any(row.get("status") == "analysis_error" for row in item_results)
                        else "needs_information"
                        if any(row.get("status") == "needs_information" for row in item_results)
                        else "unsupported"
                        if any(row.get("status") == "unsupported" for row in item_results)
                        else "resolved"
                    ),
                    "stage": "attachment_analysis",
                    "source_file_id": str(file.id) if file else None,
                    "analysis_revision": base_revision,
                }
            )
        elif conversation_focus is not None:
            message_metadata["conversation_focus"] = {
                **conversation_focus,
                "source_file_id": str(file.id) if file else None,
                "analysis_revision": base_revision,
            }
        if chat_result is not None:
            chat_tool_calls = getattr(chat_result, "tool_calls", None)
            if chat_tool_calls:
                message_metadata["tool_calls"] = chat_tool_calls
        assistant = TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=redact_message_content(reply),
            message_type=TeacherJudgeMessageType.chat,
            metadata_json=message_metadata,
        )
    except HTTPException as exc:
        failure = workflow_error_message(
            stage="reanalysis",
            status_code=exc.status_code,
            source_file_id=file.id if file else None,
            analysis_revision=base_revision,
        )
        _save_workflow_message(
            session,
            item,
            content=failure["content"],
            metadata=failure["metadata"],
            created_by=current_user.id,
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=failure["content"],
            headers=exc.headers,
        ) from exc
    except Exception:
        logger.exception("Teacher Judge message processing failed for session %s", item.id)
        failure = workflow_error_message(
            stage="reanalysis",
            source_file_id=file.id if file else None,
            analysis_revision=base_revision,
        )
        _save_workflow_message(
            session,
            item,
            content=failure["content"],
            metadata=failure["metadata"],
            created_by=current_user.id,
        )
        raise
    # Source changes clear the conversation while this request may still be
    # waiting on the model.  Revalidate before saving the generated answer so
    # an old response cannot be attached to the new rubric context.
    session.refresh(item)
    ensure_active(item)
    current_file = selected_file_for_chat(session, item)
    if current_file is not None:
        session.refresh(current_file)
        current_file = selected_file_for_chat(session, item)
    if (
        (current_file.id if current_file else None) != (file.id if file else None)
        or (current_file.analysis_revision if current_file else None) != base_revision
    ):
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
                "message": "目前檢查表沒有可製作腳本的檢查項目。",
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
            require_target_node=bool(load_class_machine_nodes(session, teaching_class_id)),
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
        script_set = await create_artifact_set(
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
                "message": "目前選取的檢查表不是此 script set 的來源。",
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
        raise HTTPException(status_code=422, detail="不支援的 script set 執行範圍。")
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
    rows = session.exec(
        select(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(
            TeacherJudgeScriptArtifact.session_id == session_id,
            TeacherJudgeScriptRun.teaching_class_id == teaching_class_id,
        )
        .order_by(desc(TeacherJudgeScriptRun.created_at))
        .offset(skip)
        .limit(limit)
    ).all()
    return [
        TeacherJudgeScriptRunSummary(
            id=str(row.id),
            run_batch_id=str(row.run_batch_id) if row.run_batch_id else None,
            teaching_class_id=str(row.teaching_class_id),
            artifact_id=str(row.artifact_id),
            status=row.status.value,
            progress_json=row.progress_json,
            result_summary_json=row.result_summary_json,
            started_at=row.started_at.isoformat() if row.started_at else None,
            finished_at=row.finished_at.isoformat() if row.finished_at else None,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )
        for row in rows
    ]


def _run_to_teacher_review_public(
    run: TeacherJudgeScriptRun,
) -> TeacherJudgeScriptRunPublic:
    """Expose only the VM identity needed by the authorized teacher review UI."""

    public = _run_to_public(run)
    public_targets = public.target_results_json.get("targets")
    raw_targets = (run.target_results_json or {}).get("targets")
    if isinstance(public_targets, list) and isinstance(raw_targets, list):
        for public_target, raw_target in zip(public_targets, raw_targets, strict=False):
            if isinstance(public_target, dict) and isinstance(raw_target, dict):
                public_target["vmid"] = raw_target.get("vmid")
    return public


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
    run = session.exec(
        select(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(
            TeacherJudgeScriptRun.id == run_id,
            TeacherJudgeScriptRun.teaching_class_id == teaching_class_id,
            TeacherJudgeScriptArtifact.session_id == session_id,
        )
    ).first()
    if not run:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.runResultNotFound")
        )
    return _run_to_teacher_review_public(run)


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
    run = session.exec(
        select(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(
            TeacherJudgeScriptRun.id == run_id,
            TeacherJudgeScriptRun.teaching_class_id == teaching_class_id,
            TeacherJudgeScriptArtifact.session_id == session_id,
        )
    ).first()
    if run is None:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.runResultNotFound")
        )
    if run.status.value != "completed":
        raise HTTPException(status_code=409, detail="只能核查已完成的執行結果。")

    result_document = dict(run.target_results_json or {})
    raw_targets = result_document.get("targets")
    targets = [dict(target) for target in raw_targets] if isinstance(raw_targets, list) else []
    target_index = next(
        (
            index
            for index, target in enumerate(targets)
            if isinstance(target, dict)
            and (
                (vmid is not None and str(target.get("vmid")) == str(vmid))
                or (
                    student_id is not None
                    and str(target.get("student_id")) == student_id
                )
            )
        ),
        None,
    )
    if target_index is None:
        raise HTTPException(status_code=404, detail="找不到這位學生的執行結果。")

    target = targets[target_index]
    parsed_result = target.get("parsed_result")
    raw_checks = parsed_result.get("checks") if isinstance(parsed_result, dict) else []
    reviewable_ids = {
        str(check.get("id") or "")
        for check in raw_checks
        if isinstance(check, dict)
        and str(check.get("status") or "") in {"warning", "unknown", "collected"}
    }
    invalid_ids = sorted(set(payload.decisions) - reviewable_ids)
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail="只能人工判定待導師核查或需注意的項目：" + "、".join(invalid_ids),
        )

    now = get_datetime_utc()
    if payload.feedback or payload.decisions:
        target["teacher_review"] = {
            "feedback": payload.feedback,
            "decisions": dict(payload.decisions),
            "reviewed_by": str(current_user.id),
            "updated_at": now.isoformat(),
        }
    else:
        target.pop("teacher_review", None)
    targets[target_index] = target
    result_document["targets"] = targets
    run.target_results_json = result_document
    run.updated_at = now
    session.add(run)
    session.commit()
    session.refresh(run)
    return _run_to_teacher_review_public(run)


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
