"""Class-scoped persistent Teacher Judge session APIs."""

from __future__ import annotations

import json
import uuid

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
from app.ai.teacher_judge.config import settings as teacher_judge_settings
from app.ai.teacher_judge.file_service import create_blank_file
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeScriptArtifactPublic,
    TeacherJudgeScriptRunCreateRequest,
    TeacherJudgeScriptRunPublic,
    TeacherJudgeScriptRunSummary,
    TeacherJudgeSessionAttachmentUploadResponse,
    TeacherJudgeSessionChatResponse,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionForkRequest,
    TeacherJudgeSessionMessageCreateRequest,
    TeacherJudgeSessionMessagePublic,
    TeacherJudgeSessionPublic,
    TeacherJudgeSessionScriptCreateRequest,
    TeacherJudgeSessionUpdateRequest,
)
from app.ai.teacher_judge.script_artifact_service import create_artifact
from app.ai.teacher_judge.script_executor_service import execute_script_run
from app.ai.teacher_judge.script_run_service import _run_to_public, create_script_run
from app.ai.teacher_judge.service import chat_with_rubric
from app.ai.teacher_judge.session_service import (
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
    selected_file_for_chat,
    session_public,
    session_public_many,
    validate_selected_file,
)
from app.ai.teacher_judge.template_command_service import get_enabled_template_commands
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.infrastructure.worker import submit
from app.models import TeachingClass, TeachingClassWeek
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
    from app.models.base import get_datetime_utc

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
        template_commands = get_enabled_template_commands(
            session,
            file.template_key if file else "linux",
            include_cross_template=True,
        )
        chat_result = await chat_with_rubric(
            bounded_history(
                session,
                item.id,
                exclude_attachments_for_message_id=user_message.id,
                summary=item.summary,
                source_file_id=file.id if file else None,
            ),
            json.dumps(file.analysis_json, ensure_ascii=False) if file else "{}",
            is_refine=payload.is_refine,
            template_key=file.template_key if file else "linux",
            template_commands=template_commands,
            environment_keys=file.environment_keys if file else None,
            attachment_context=attachment_context(attachments),
            analysis_revision=base_revision,
            rubric_available=file is not None,
        )
        reply, proposal, metrics = chat_result
        # Without a selected rubric the conversation is general assistance only;
        # do not let an unconstrained model response create an unreviewed proposal.
        if file is None and proposal:
            reply = (
                "這項需求已具備自動檢查條件，但目前尚未選擇檢查表來源，"
                "因此無法建立可套用提案。請先選擇來源後再送出需求。"
            )
            proposal = None
        message_metadata: dict[str, object] = {"metrics": metrics}
        conversation_focus = getattr(chat_result, "conversation_focus", None)
        if isinstance(conversation_focus, dict):
            message_metadata["conversation_focus"] = {
                **conversation_focus,
                "source_file_id": str(file.id) if file else None,
            }
        if payload.is_refine:
            message_metadata["ui_hidden"] = True
        assistant = TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=redact_message_content(reply),
            message_type=TeacherJudgeMessageType.chat,
            metadata_json=message_metadata,
        )
    except HTTPException as exc:
        error_detail = (
            exc.detail.get("message", exc.detail)
            if isinstance(exc.detail, dict)
            else exc.detail
        )
        assistant = TeacherJudgeSessionMessage(
            session_id=item.id,
            role=TeacherJudgeMessageRole.assistant,
            content=f"AI 回覆失敗：{error_detail}",
            message_type=TeacherJudgeMessageType.system_notice,
            metadata_json={"status": "failed"},
        )
        session.add(assistant)
        session.commit()
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
    from app.models.base import get_datetime_utc

    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add_all([assistant, item])
    session.commit()
    session.refresh(assistant)
    schedule_summary(session, item, boundary_message_id=assistant.id)
    return TeacherJudgeSessionChatResponse(
        user_message=message_public(user_message, attachments),
        assistant_message=message_public(assistant),
        rubric_proposal=proposal,
        base_revision=base_revision,
    )


@router.post("/{session_id}/scripts", response_model=TeacherJudgeScriptArtifactPublic)
async def create_session_script(
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    payload: TeacherJudgeSessionScriptCreateRequest | None = None,
) -> TeacherJudgeScriptArtifactPublic:
    _access(session, teaching_class_id, current_user)
    item = get_session(session, teaching_class_id, session_id)
    ensure_active(item)
    file = require_selected_file(session, item)
    expected_revision = payload.analysis_revision if payload else None
    if expected_revision is not None and expected_revision != file.analysis_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_analysis_revision_conflict",
                "message": t("teacherJudgeSessions.analysisRevisionConflict"),
                "analysis_revision": file.analysis_revision,
            },
        )
    rubric_analysis = TeacherJudgeRubricAnalysis.model_validate(file.analysis_json)
    if not rubric_analysis.items:
        raise HTTPException(
            status_code=422,
            detail="目前檢查表沒有檢查項目，請先新增至少一個項目。",
        )
    artifact = await create_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        name=item.title,
        template_key=file.template_key,
        rubric_analysis=rubric_analysis,
        created_by=current_user.id,
        source_file_id=file.id,
        session_id=item.id,
    )
    from app.models.base import get_datetime_utc

    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add(item)
    session.commit()
    return artifact


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
    return _run_to_public(run)


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
    )
    from app.models.base import get_datetime_utc

    item.last_activity_at = get_datetime_utc()
    item.updated_at = item.last_activity_at
    session.add(item)
    session.commit()
    submit(
        execute_script_run(uuid.UUID(run.id)),
        name=f"teacher_judge_script_run:{run.id}",
        task_id=f"teacher_judge_script_run:{run.id}",
    )
    return run
