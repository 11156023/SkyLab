"""Student-safe projection of approved Teacher Judge assignments.

Teacher Judge artifacts and Course Lab paths are both linked to one teaching class.
A student can only see approved artifacts from the exact class linked to the path.

Only the rubric requirements are exposed.  Generated scripts, command keys,
detection methods, fallbacks, policy reviews, and other students' results stay
server-side.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, desc, select

from app.ai.teacher_judge import file_service
from app.core.i18n import t
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teacher_judge_student_submission import (
    TeacherJudgeStudentSubmission,
)
from app.models.teaching_class import TeachingClass, TeachingClassStudent
from app.schemas.course import (
    CourseAIAssignmentStudent,
    CourseAICheckItemStudent,
    CourseAICheckStudent,
    CourseAICompletionStudent,
    CourseAISourceDocumentStudent,
    CourseAITaskItemStudent,
)
from app.services.course import course_service

_DETECTABLE_VALUES = {"auto", "partial", "manual"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _student_submission(
    session: Session,
    *,
    artifact_id: uuid.UUID,
    user_id: uuid.UUID,
) -> TeacherJudgeStudentSubmission | None:
    return session.exec(
        select(TeacherJudgeStudentSubmission).where(
            TeacherJudgeStudentSubmission.artifact_id == artifact_id,
            TeacherJudgeStudentSubmission.student_id == user_id,
        )
    ).first()


def _completion_to_student(
    submission: TeacherJudgeStudentSubmission | None,
    *,
    item_ids: list[str],
) -> CourseAICompletionStudent:
    completed = set(submission.completed_item_ids if submission else [])
    completed_item_ids = [item_id for item_id in item_ids if item_id in completed]
    all_completed = bool(item_ids) and len(completed_item_ids) == len(item_ids)
    return CourseAICompletionStudent(
        completed=all_completed,
        completed_item_ids=completed_item_ids,
        ready_at=(
            submission.ready_at
            if submission and all_completed
            else None
        ),
    )


def _requested_item_id(run: TeacherJudgeScriptRun) -> str | None:
    value = (run.target_snapshot_json or {}).get("requested_item_id")
    return str(value) if value else None


def _first_target(run: TeacherJudgeScriptRun) -> dict[str, Any]:
    raw_targets = run.target_results_json.get("targets")
    target = raw_targets[0] if isinstance(raw_targets, list) and raw_targets else {}
    return target if isinstance(target, dict) else {}


def _target_for_student(
    run: TeacherJudgeScriptRun, user_id: uuid.UUID | None
) -> dict[str, Any]:
    """Return only the result target owned by the requested student."""

    if user_id is None:
        return _first_target(run)
    raw_targets = (run.target_results_json or {}).get("targets")
    if isinstance(raw_targets, list):
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                continue
            user = raw_target.get("user")
            # 產生端（script_run_service / script_executor_service）寫的是 "id"；
            # 相容更早期可能寫成 "user_id" 的快照
            target_user_id = (
                (user.get("id") or user.get("user_id")) if isinstance(user, dict) else None
            )
            if target_user_id and str(target_user_id) == str(user_id):
                return raw_target
    # 舊資料相容：學生自己發起、只有一個 target 且沒有 user 快照的 run，
    # 那唯一的 target 一定是本人的。多 target 的 run 不可拿 started_by 當代理，
    # 否則 targets[0]（別的同學的結果）會回給發起者。
    if (
        run.started_by == user_id
        and isinstance(raw_targets, list)
        and len(raw_targets) == 1
        and isinstance(raw_targets[0], dict)
        and not isinstance(raw_targets[0].get("user"), dict)
    ):
        return raw_targets[0]
    return {}


def _teacher_review(target: dict[str, Any]) -> tuple[str, dict[str, str]]:
    raw_review = target.get("teacher_review")
    if not isinstance(raw_review, dict):
        return "", {}
    raw_decisions = raw_review.get("decisions")
    decisions = (
        {
            str(check_id): str(decision)
            for check_id, decision in raw_decisions.items()
            if decision in {"pass", "fail"}
        }
        if isinstance(raw_decisions, dict)
        else {}
    )
    return str(raw_review.get("feedback") or ""), decisions


def _coverage_mappings(
    artifact: TeacherJudgeScriptArtifact | None,
) -> list[dict[str, Any]]:
    if artifact is None:
        return []
    coverage = (artifact.policy_check_result_json or {}).get("coverage")
    mappings = coverage.get("mappings") if isinstance(coverage, dict) else None
    if not isinstance(mappings, list):
        return []
    return [
        mapping
        for mapping in mappings
        if isinstance(mapping, dict)
        and isinstance(mapping.get("check_id"), str)
        and isinstance(mapping.get("rubric_item_ids"), list)
    ]


def _script_checks(
    target: dict[str, Any],
    *,
    artifact: TeacherJudgeScriptArtifact | None,
    item_id: str | None,
) -> list[CourseAICheckItemStudent]:
    parsed_result = target.get("parsed_result")
    raw_checks = parsed_result.get("checks") if isinstance(parsed_result, dict) else None
    if not isinstance(raw_checks, list):
        return []

    _, teacher_decisions = _teacher_review(target)

    check_by_id: dict[str, dict[str, Any]] = {}
    for raw in raw_checks:
        if not isinstance(raw, dict):
            continue
        check_id = str(raw.get("id") or "").strip()
        if check_id:
            check_by_id[check_id] = raw

    if item_id:
        mapped_ids: list[str] = []
        for mapping in _coverage_mappings(artifact):
            if item_id not in {str(value) for value in mapping["rubric_item_ids"]}:
                continue
            check_id = str(mapping["check_id"])
            if check_id not in mapped_ids:
                mapped_ids.append(check_id)
        if not mapped_ids and item_id in check_by_id:
            mapped_ids = [item_id]
        selected = [
            check_by_id[check_id]
            for check_id in mapped_ids
            if check_id in check_by_id
        ]
        if not selected:
            return []
        rubric_title = ""
        if artifact is not None:
            for rubric_item in (artifact.rubric_snapshot_json or {}).get("items", []):
                if isinstance(rubric_item, dict) and str(rubric_item.get("id") or "") == item_id:
                    rubric_title = str(rubric_item.get("title") or "")
                    break
        statuses = [
            teacher_decisions.get(
                str(check.get("id") or ""), str(check.get("status") or "unknown")
            )
            for check in selected
        ]
        status = "fail" if "fail" in statuses else (
            "warning" if "warning" in statuses else (
                "unknown" if "unknown" in statuses else (
                    "skipped" if "skipped" in statuses else (
                        "collected" if "collected" in statuses else "pass"
                    )
                )
            )
        )
        evidence = "\n".join(
            str(check.get("evidence") or "").strip()
            for check in selected
            if str(check.get("evidence") or "").strip()
        )
        return [
            CourseAICheckItemStudent(
                item_id=item_id,
                title=rubric_title or str(selected[0].get("title") or item_id),
                status=status,
                comment=evidence,
            )
        ]

    return [
        CourseAICheckItemStudent(
            item_id=check_id,
            title=str(check.get("title") or check_id),
            status=teacher_decisions.get(
                check_id, str(check.get("status") or "unknown")
            ),
            comment=str(check.get("evidence") or ""),
        )
        for check_id, check in check_by_id.items()
    ]


def _legacy_judgement_items(
    judgement: dict[str, Any], *, item_id: str | None
) -> list[CourseAICheckItemStudent]:
    raw_items = judgement.get("item_judgements")
    if not isinstance(raw_items, list):
        return []
    items: list[CourseAICheckItemStudent] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        if item_id and str(raw.get("item_id") or "") != item_id:
            continue
        items.append(
            CourseAICheckItemStudent(
                item_id=str(raw.get("item_id") or ""),
                title=str(raw.get("title") or ""),
                status=str(raw.get("status") or "unknown"),
                score=raw.get("score") if isinstance(raw.get("score"), int) else None,
                max_score=(
                    raw.get("max_score")
                    if isinstance(raw.get("max_score"), int)
                    else None
                ),
                comment=str(raw.get("comment") or ""),
            )
        )
    return items


def _check_to_student(
    run: TeacherJudgeScriptRun,
    *,
    artifact: TeacherJudgeScriptArtifact | None = None,
    item_id: str | None = None,
    user_id: uuid.UUID | None = None,
) -> CourseAICheckStudent:
    """Project one run down to the feedback that belongs on a student page."""

    target = _target_for_student(run, user_id)
    teacher_feedback, _ = _teacher_review(target)
    judgement = target.get("ai_judgement") if isinstance(target, dict) else {}
    if not isinstance(judgement, dict):
        judgement = {}
    items = _script_checks(target, artifact=artifact, item_id=item_id)
    if not items:
        items = _legacy_judgement_items(judgement, item_id=item_id)

    parsed_result = target.get("parsed_result")
    parsed_summary = (
        str(parsed_result.get("summary") or "")
        if isinstance(parsed_result, dict)
        else ""
    )
    parsed_errors = (
        parsed_result.get("errors")
        if isinstance(parsed_result, dict)
        else []
    )
    target_validation = target.get("validation")
    target_error = (
        str(
            (target_validation.get("error") if isinstance(target_validation, dict) else "")
            or target.get("error")
            or ""
        )
    )
    if not target_error and isinstance(parsed_errors, list):
        target_error = "; ".join(str(error) for error in parsed_errors if error)
    item_score = items[0].score if item_id and items else None
    item_max_score = items[0].max_score if item_id and items else None
    has_script_result = isinstance(parsed_result, dict)
    return CourseAICheckStudent(
        run_id=run.id,
        status=run.status.value,
        submitted_at=run.created_at,
        finished_at=run.finished_at,
        score=item_score if item_id else (
            judgement.get("score")
            if not has_script_result and isinstance(judgement.get("score"), int)
            else None
        ),
        max_score=item_max_score if item_id else (
            judgement.get("max_score")
            if not has_script_result and isinstance(judgement.get("max_score"), int)
            else None
        ),
        summary=parsed_summary or str(judgement.get("summary") or ""),
        teacher_feedback=teacher_feedback,
        error=str(judgement.get("error") or target_error or ""),
        items=items,
    )


def _latest_student_check(
    session: Session,
    *,
    artifact_id: uuid.UUID,
    user_id: uuid.UUID,
) -> CourseAICheckStudent | None:
    runs = session.exec(
        select(TeacherJudgeScriptRun)
        .where(
            TeacherJudgeScriptRun.artifact_id == artifact_id,
        )
        .order_by(desc(TeacherJudgeScriptRun.created_at))
    ).all()
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    run = next((candidate for candidate in runs if _target_for_student(candidate, user_id)), None)
    return (
        _check_to_student(run, artifact=artifact, user_id=user_id)
        if run is not None
        else None
    )


def _latest_student_checkpoint_checks(
    session: Session,
    *,
    artifact_id: uuid.UUID,
    user_id: uuid.UUID,
    item_ids: list[str],
) -> dict[str, CourseAICheckStudent]:
    wanted = set(item_ids)
    checks: dict[str, CourseAICheckStudent] = {}
    artifact = session.get(TeacherJudgeScriptArtifact, artifact_id)
    runs = session.exec(
        select(TeacherJudgeScriptRun)
        .where(
            TeacherJudgeScriptRun.artifact_id == artifact_id,
        )
        .order_by(desc(TeacherJudgeScriptRun.created_at))
    ).all()
    for run in runs:
        if not _target_for_student(run, user_id):
            continue
        requested = _requested_item_id(run)
        candidates = [requested] if requested else item_ids
        for checkpoint_id in candidates:
            if checkpoint_id in wanted and checkpoint_id not in checks:
                checks[checkpoint_id] = _check_to_student(
                    run,
                    artifact=artifact,
                    item_id=checkpoint_id,
                    user_id=user_id,
                )
        if len(checks) == len(wanted):
            break
    return checks


def _student_items(snapshot: dict[str, Any]) -> list[CourseAITaskItemStudent]:
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        return []

    items: list[CourseAITaskItemStudent] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        detectable = str(raw.get("detectable") or "manual").strip().lower()
        if detectable not in _DETECTABLE_VALUES:
            detectable = "manual"
        items.append(
            CourseAITaskItemStudent(
                id=str(raw.get("id") or f"item-{index + 1}"),
                title=title,
                description=str(raw.get("description") or "").strip(),
                detectable=detectable,  # type: ignore[arg-type]
                order=index,
            )
        )
    return items


def _student_source_document(
    source_file: TeacherJudgeFile | None,
    *,
    teaching_class_id: uuid.UUID,
) -> CourseAISourceDocumentStudent | None:
    """Project only an uploaded PDF's display metadata to the student."""

    if (
        source_file is None
        or source_file.teaching_class_id != teaching_class_id
        or source_file.source_type != "uploaded"
        or not source_file.original_filename
        or not source_file.original_filename.lower().endswith(".pdf")
    ):
        return None
    return CourseAISourceDocumentStudent(
        filename=source_file.original_filename,
        display_name=(source_file.display_name or source_file.original_filename),
    )


def list_student_ai_assignments(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
) -> list[CourseAIAssignmentStudent]:
    """Return approved AI assignments visible to one student for a path."""

    teaching_class = course_service.get_student_class_for_path(
        session,
        user_id=user_id,
        path_id=path_id,
    )
    if teaching_class is None:
        return []

    rows = session.exec(
        select(
            TeacherJudgeScriptArtifact,
            TeachingClass,
            TeacherJudgeFile,
            TeacherJudgeSession,
        )
        .join(
            TeachingClass,
            TeacherJudgeScriptArtifact.teaching_class_id == TeachingClass.id,
        )
        .outerjoin(
            TeacherJudgeFile,
            TeacherJudgeScriptArtifact.source_file_id == TeacherJudgeFile.id,
        )
        .outerjoin(
            TeacherJudgeSession,
            TeacherJudgeScriptArtifact.session_id == TeacherJudgeSession.id,
        )
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == TeachingClass.id,
        )
        .where(
            TeachingClass.id == teaching_class.id,
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
            TeacherJudgeScriptArtifact.status == TeacherJudgeScriptStatus.approved,
        )
        .order_by(
            desc(TeacherJudgeScriptArtifact.approved_at),
            desc(TeacherJudgeScriptArtifact.updated_at),
        )
    ).all()

    assignments: list[CourseAIAssignmentStudent] = []
    seen_sources: set[tuple[uuid.UUID, str]] = set()
    for artifact, teaching_class, source_file, judge_session in rows:
        # Regeneration creates a new version.  Show only the newest approved
        # version for the same source rubric (or name when no source exists).
        source_key = str(artifact.source_file_id or artifact.name)
        dedupe_key = (teaching_class.id, source_key)
        if dedupe_key in seen_sources:
            continue
        seen_sources.add(dedupe_key)

        snapshot = artifact.rubric_snapshot_json or {}
        items = _student_items(snapshot)
        if not items:
            continue
        checkpoint_checks = _latest_student_checkpoint_checks(
            session,
            artifact_id=artifact.id,
            user_id=user_id,
            item_ids=[item.id for item in items],
        )
        submission = _student_submission(
            session,
            artifact_id=artifact.id,
            user_id=user_id,
        )
        assignments.append(
            CourseAIAssignmentStudent(
                id=artifact.id,
                teaching_class_id=teaching_class.id,
                teaching_class_name=teaching_class.name,
                session_id=judge_session.id if judge_session else None,
                teaching_class_week_id=(
                    judge_session.teaching_class_week_id if judge_session else None
                ),
                title=artifact.name,
                summary=str(snapshot.get("summary") or "").strip(),
                template_key=artifact.template_key,
                version=artifact.version,
                approved_at=artifact.approved_at,
                items=items,
                source_document=_student_source_document(
                    source_file,
                    teaching_class_id=teaching_class.id,
                ),
                completion=_completion_to_student(
                    submission,
                    item_ids=[item.id for item in items],
                ),
                latest_check=_latest_student_check(
                    session,
                    artifact_id=artifact.id,
                    user_id=user_id,
                ),
                checkpoint_checks=checkpoint_checks,
            )
        )
    return assignments


def get_student_ai_assignment(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> CourseAIAssignmentStudent:
    """Return one visible assignment or hide it behind a 404."""

    from fastapi import HTTPException

    assignment = next(
        (
            item
            for item in list_student_ai_assignments(
                session,
                user_id=user_id,
                path_id=path_id,
            )
            if item.id == assignment_id
        ),
        None,
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail=t("ai_assignment.not_found"))
    return assignment


def get_student_ai_assignment_source_document(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> tuple[Path, str]:
    """Return an enrolled student's PDF path after assignment authorization."""

    from fastapi import HTTPException

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    artifact = session.get(TeacherJudgeScriptArtifact, assignment.id)
    source_file = (
        session.get(TeacherJudgeFile, artifact.source_file_id)
        if artifact is not None and artifact.source_file_id is not None
        else None
    )
    if (
        _student_source_document(
            source_file,
            teaching_class_id=assignment.teaching_class_id,
        )
        is None
        or source_file is None
    ):
        raise HTTPException(
            status_code=404, detail=t("ai_assignment.source_pdf_not_found")
        )
    return file_service.get_file_download(
        session=session,
        teaching_class_id=assignment.teaching_class_id,
        file_id=source_file.id,
    )


def get_student_ai_check(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    run_id: uuid.UUID,
) -> CourseAICheckStudent:
    """Return only the current student's own run for a visible assignment."""

    from fastapi import HTTPException

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    run = session.get(TeacherJudgeScriptRun, run_id)
    if (
        run is None
        or run.artifact_id != assignment.id
        or run.teaching_class_id != assignment.teaching_class_id
        or not _target_for_student(run, user_id)
    ):
        raise HTTPException(status_code=404, detail=t("ai_assignment.check_not_found"))
    artifact = session.get(TeacherJudgeScriptArtifact, assignment.id)
    return _check_to_student(
        run,
        artifact=artifact,
        item_id=_requested_item_id(run),
        user_id=user_id,
    )


def update_student_completion(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    assignment_id: uuid.UUID,
    item_id: str | None,
    completed: bool,
) -> CourseAICompletionStudent:
    """Persist completion without starting any check.

    When ``item_id`` is omitted, all rubric items are toggled atomically so the
    student-facing course page can expose one completion control per week.
    """

    assignment = get_student_ai_assignment(
        session,
        user_id=user_id,
        path_id=path_id,
        assignment_id=assignment_id,
    )
    submission = _student_submission(
        session,
        artifact_id=assignment.id,
        user_id=user_id,
    )
    valid_item_ids = [item.id for item in assignment.items]
    if item_id is not None and item_id not in valid_item_ids:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=t("ai_assignment.task_item_not_found")
        )

    now = _now()
    if item_id is None:
        completed_items = set(valid_item_ids if completed else [])
    else:
        completed_items = set(submission.completed_item_ids if submission else [])
        if completed:
            completed_items.add(item_id)
        else:
            completed_items.discard(item_id)
    completed_item_ids = [
        valid_item_id
        for valid_item_id in valid_item_ids
        if valid_item_id in completed_items
    ]
    all_completed = len(completed_item_ids) == len(valid_item_ids)
    if submission is None:
        submission = TeacherJudgeStudentSubmission(
            teaching_class_id=assignment.teaching_class_id,
            artifact_id=assignment.id,
            student_id=user_id,
            completed_item_ids=completed_item_ids,
            is_ready=all_completed,
            ready_at=now if all_completed else None,
            updated_at=now,
        )
    else:
        was_ready = submission.is_ready
        submission.completed_item_ids = completed_item_ids
        submission.is_ready = all_completed
        submission.ready_at = (
            submission.ready_at if all_completed and was_ready else now
        ) if all_completed else None
        submission.updated_at = now
    session.add(submission)
    session.commit()
    session.refresh(submission)
    return _completion_to_student(
        submission,
        item_ids=valid_item_ids,
    )


__all__ = [
    "get_student_ai_assignment",
    "get_student_ai_assignment_source_document",
    "get_student_ai_check",
    "list_student_ai_assignments",
    "update_student_completion",
]
