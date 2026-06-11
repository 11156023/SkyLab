"""Teacher Judge uploaded rubric file API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.ai.monitoring import CALL_TJ_RUBRIC, record_ai_template_call
from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.file_service import (
    delete_file,
    get_file_download,
    list_files,
    parse_conflict_strategy,
    prepare_file_payload,
    raise_if_file_name_conflict,
    save_analyzed_file,
    update_file_analysis,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeFileAnalysisUpdateRequest,
    TeacherJudgeFilePublic,
    TeacherJudgeFileUploadResponse,
)
from app.ai.teacher_judge.service import analyze_rubric
from app.ai.teacher_judge.template_command_service import (
    SUPPORTED_TEMPLATE_KEYS,
    get_enabled_template_commands,
)
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_group_access
from app.repositories import group as group_repo

router = APIRouter(prefix="/groups/{group_id}/judge/files", tags=["teacher-judge"])


def _ensure_group_access(
    *,
    session: SessionDep,
    group_id: uuid.UUID,
    current_user: InstructorUser,
) -> None:
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    require_group_access(current_user, db_group.owner_id)


def _normalize_supported_template_key(template_key: str) -> str:
    normalized = template_key.strip().lower() or "linux"
    if normalized not in SUPPORTED_TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail="未知的評分環境 template。")
    return normalized


@router.get("/", response_model=list[TeacherJudgeFilePublic])
def list_group_teacher_judge_files(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[TeacherJudgeFilePublic]:
    _ensure_group_access(session=session, group_id=group_id, current_user=current_user)
    return list_files(session=session, group_id=group_id)


@router.post("/", response_model=TeacherJudgeFileUploadResponse)
async def upload_group_teacher_judge_file(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
    template_key: str = Form(default="linux"),
    conflict_strategy: str | None = Form(default=None),
) -> TeacherJudgeFileUploadResponse:
    _ensure_group_access(session=session, group_id=group_id, current_user=current_user)
    template_key = _normalize_supported_template_key(template_key)
    conflict_strategy = parse_conflict_strategy(conflict_strategy)
    file_bytes = await file.read()

    try:
        original_filename, file_hash, raw_text = prepare_file_payload(
            filename=file.filename or "unknown",
            file_bytes=file_bytes,
            allowed_suffixes={".docx", ".pdf"},
            max_upload_size_bytes=settings.VLLM_MAX_UPLOAD_SIZE_MB * 1024 * 1024,
        )
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    raise_if_file_name_conflict(
        session=session,
        group_id=group_id,
        original_filename=original_filename,
        conflict_strategy=conflict_strategy,
    )

    template_commands = get_enabled_template_commands(session, template_key)
    try:
        analysis, metrics = await analyze_rubric(
            raw_text,
            template_key=template_key,
            template_commands=template_commands,
        )
    except HTTPException as exc:
        record_ai_template_call(
            session=session,
            user_id=current_user.id,
            call_type=CALL_TJ_RUBRIC,
            model_name=settings.VLLM_MODEL_NAME,
            preset=template_key,
            status="error",
            error_message=str(exc.detail),
        )
        raise
    saved_file = save_analyzed_file(
        session=session,
        group_id=group_id,
        uploaded_by=current_user.id,
        original_filename=original_filename,
        file_hash=file_hash,
        template_key=template_key,
        file_bytes=file_bytes,
        analysis=analysis,
        conflict_strategy=conflict_strategy,
    )
    record_ai_template_call(
        session=session,
        user_id=current_user.id,
        call_type=CALL_TJ_RUBRIC,
        model_name=settings.VLLM_MODEL_NAME,
        preset=template_key,
        metrics=metrics,
    )
    return TeacherJudgeFileUploadResponse(
        file=saved_file,
        analysis=analysis,
        ai_metrics=metrics,
        template_key=template_key,
    )


@router.get("/{file_id}/download")
def download_group_teacher_judge_file(
    group_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> FileResponse:
    _ensure_group_access(session=session, group_id=group_id, current_user=current_user)
    path, filename = get_file_download(
        session=session,
        group_id=group_id,
        file_id=file_id,
    )
    return FileResponse(path, filename=filename)


@router.patch("/{file_id}/analysis", response_model=TeacherJudgeFilePublic)
def update_group_teacher_judge_file_analysis(
    group_id: uuid.UUID,
    file_id: uuid.UUID,
    payload: TeacherJudgeFileAnalysisUpdateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeFilePublic:
    _ensure_group_access(session=session, group_id=group_id, current_user=current_user)
    return update_file_analysis(
        session=session,
        group_id=group_id,
        file_id=file_id,
        analysis=payload.analysis,
    )


@router.delete("/{file_id}", status_code=204)
def delete_group_teacher_judge_file(
    group_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> None:
    _ensure_group_access(session=session, group_id=group_id, current_user=current_user)
    delete_file(session=session, group_id=group_id, file_id=file_id)
