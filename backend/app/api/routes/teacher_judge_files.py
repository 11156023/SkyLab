"""Teacher Judge uploaded rubric file API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.ai.teacher_judge.file_service import (
    get_file_download,
    list_files,
    update_file_analysis,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeFileAnalysisUpdateRequest,
    TeacherJudgeFilePublic,
)
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.models import TeachingClass

router = APIRouter(
    prefix="/teaching-classes/{teaching_class_id}/judge/files",
    tags=["teacher-judge"],
)


def _ensure_class_access(
    *,
    session: SessionDep,
    teaching_class_id: uuid.UUID,
    current_user: InstructorUser,
) -> None:
    teaching_class = session.get(TeachingClass, teaching_class_id)
    if not teaching_class:
        raise HTTPException(status_code=404, detail=t("teacherJudgeFiles.classNotFound"))
    require_teaching_access(current_user, teaching_class.owner_id)


@router.get("/", response_model=list[TeacherJudgeFilePublic])
def list_class_teacher_judge_files(
    teaching_class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[TeacherJudgeFilePublic]:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return list_files(session=session, teaching_class_id=teaching_class_id)


@router.get("/{file_id}/download")
def download_class_teacher_judge_file(
    teaching_class_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> FileResponse:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    path, filename = get_file_download(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=file_id,
    )
    return FileResponse(path, filename=filename)


@router.patch("/{file_id}/analysis", response_model=TeacherJudgeFilePublic)
def update_class_teacher_judge_file_analysis(
    teaching_class_id: uuid.UUID,
    file_id: uuid.UUID,
    payload: TeacherJudgeFileAnalysisUpdateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeFilePublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return update_file_analysis(
        session=session,
        teaching_class_id=teaching_class_id,
        file_id=file_id,
        analysis=payload.analysis,
        expected_revision=payload.expected_revision,
    )
