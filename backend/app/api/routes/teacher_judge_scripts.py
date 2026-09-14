"""Teacher Judge managed script artifact API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from app.ai.teacher_judge.schemas import (
    TeacherJudgeScriptArtifactPublic,
    TeacherJudgeScriptCreateRequest,
    TeacherJudgeScriptRegenerateRequest,
    TeacherJudgeScriptRunCreateRequest,
    TeacherJudgeScriptRunPublic,
    TeacherJudgeScriptUpdateRequest,
)
from app.ai.teacher_judge.script_artifact_service import (
    approve_artifact,
    archive_artifact,
    create_artifact,
    delete_artifact,
    get_artifact_public,
    list_artifacts,
    regenerate_artifact,
    rename_artifact,
)
from app.ai.teacher_judge.script_executor_service import execute_script_run
from app.ai.teacher_judge.script_run_service import (
    create_script_run,
    get_script_run_public,
)
from app.ai.teacher_judge.template_command_service import SUPPORTED_TEMPLATE_KEYS
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.infrastructure.worker import submit
from app.models import TeachingClass
from app.models.teacher_judge_script_run import TeacherJudgeScriptRunTargetScope

router = APIRouter(
    prefix="/teaching-classes/{teaching_class_id}/judge/scripts",
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
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeScripts.classNotFound")
        )
    require_teaching_access(current_user, teaching_class.owner_id)


def _normalize_supported_template_key(template_key: str) -> str:
    normalized = template_key.strip().lower() or "linux"
    if normalized not in SUPPORTED_TEMPLATE_KEYS:
        raise HTTPException(
            status_code=400, detail=t("teacherJudgeScripts.unknownTemplate")
        )
    return normalized


@router.get("/", response_model=list[TeacherJudgeScriptArtifactPublic])
def list_class_teacher_judge_scripts(
    teaching_class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    session_id: uuid.UUID | None = None,
) -> list[TeacherJudgeScriptArtifactPublic]:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return list_artifacts(
        session=session, teaching_class_id=teaching_class_id, session_id=session_id
    )


@router.post("/", response_model=TeacherJudgeScriptArtifactPublic)
async def create_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    payload: TeacherJudgeScriptCreateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    template_key = _normalize_supported_template_key(payload.template_key)
    return await create_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        name=payload.name,
        template_key=template_key,
        rubric_analysis=payload.rubric_snapshot,
        created_by=current_user.id,
        source_file_id=payload.source_file_id,
    )


@router.get("/{script_id}", response_model=TeacherJudgeScriptArtifactPublic)
def get_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return get_artifact_public(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
    )


@router.post("/{script_id}/regenerate", response_model=TeacherJudgeScriptArtifactPublic)
async def regenerate_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    payload: TeacherJudgeScriptRegenerateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return await regenerate_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
        rubric_analysis=payload.rubric_snapshot,
        created_by=current_user.id,
    )


@router.post("/{script_id}/approve", response_model=TeacherJudgeScriptArtifactPublic)
def approve_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return approve_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
        approved_by=current_user.id,
    )


@router.patch("/{script_id}", response_model=TeacherJudgeScriptArtifactPublic)
def rename_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    payload: TeacherJudgeScriptUpdateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return rename_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
        name=payload.name,
    )


@router.post("/{script_id}/runs", response_model=TeacherJudgeScriptRunPublic)
def create_class_teacher_judge_script_run(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    payload: TeacherJudgeScriptRunCreateRequest,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    run = create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
        target_scope=TeacherJudgeScriptRunTargetScope(payload.target_scope),
        target_vmids=payload.target_vmids,
        started_by=current_user.id,
    )
    submit(
        execute_script_run(uuid.UUID(run.id)),
        name=f"teacher_judge_script_run:{run.id}",
        task_id=f"teacher_judge_script_run:{run.id}",
    )
    return run


@router.get("/{script_id}/runs/{run_id}", response_model=TeacherJudgeScriptRunPublic)
def get_class_teacher_judge_script_run(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    run_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptRunPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return get_script_run_public(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
        run_id=run_id,
    )


@router.post("/{script_id}/archive", response_model=TeacherJudgeScriptArtifactPublic)
def archive_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> TeacherJudgeScriptArtifactPublic:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    return archive_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
    )


@router.delete("/{script_id}", status_code=204)
def delete_class_teacher_judge_script(
    teaching_class_id: uuid.UUID,
    script_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> None:
    _ensure_class_access(
        session=session, teaching_class_id=teaching_class_id, current_user=current_user
    )
    delete_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=script_id,
    )
