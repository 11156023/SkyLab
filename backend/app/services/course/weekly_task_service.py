"""Student-facing weekly teaching-class tasks and protected task files."""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlmodel import Session, select

from app.core.i18n import t
from app.exceptions import NotFoundError
from app.models import TeachingClassTaskFile, TeachingClassWeek
from app.models.teacher_judge_file import TeacherJudgeFile
from app.models.teacher_judge_session import TeacherJudgeSession
from app.schemas.course import (
    CourseAITaskItemStudent,
    CourseWeeklyCheckpointStudent,
    CourseWeeklyTaskFileStudent,
    CourseWeeklyTaskStudent,
)
from app.services.course import ai_assignment_service, course_service

TASK_FILE_ROOT = Path(__file__).resolve().parents[3] / "data" / "teaching-class-tasks"
VISIBLE_WEEK_STATUSES = {"published", "completed"}


def _pdf_file(row: TeachingClassTaskFile) -> CourseWeeklyTaskFileStudent | None:
    if not row.filename.lower().endswith(".pdf") or not row.storage_key:
        return None
    return CourseWeeklyTaskFileStudent(id=row.id, filename=row.filename)


def _source_items(source_file: TeacherJudgeFile | None) -> list[CourseAITaskItemStudent]:
    """Expose the AI-extracted task list even before a runnable script is approved."""
    raw_items = (source_file.analysis_json or {}).get("items", []) if source_file else []
    if not isinstance(raw_items, list):
        return []

    items: list[CourseAITaskItemStudent] = []
    for order, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or f"item-{order + 1}").strip()
        title = str(raw.get("title") or "").strip()
        if not item_id or not title:
            continue
        detectable = str(raw.get("detectable") or "manual")
        items.append(
            CourseAITaskItemStudent(
                id=item_id,
                title=title,
                description=str(raw.get("description") or "").strip(),
                detectable=(
                    detectable if detectable in {"auto", "partial", "manual"} else "manual"
                ),
                order=order,
            )
        )
    return items


def list_student_weekly_tasks(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
) -> list[CourseWeeklyTaskStudent]:
    teaching_class = course_service.get_student_class_for_path(
        session,
        user_id=user_id,
        path_id=path_id,
    )
    if teaching_class is None:
        return []

    weeks = session.exec(
        select(TeachingClassWeek)
        .where(
            TeachingClassWeek.class_id == teaching_class.id,
            TeachingClassWeek.status.in_(VISIBLE_WEEK_STATUSES),
            TeachingClassWeek.title != "",
        )
        .order_by(TeachingClassWeek.session_date, TeachingClassWeek.week_number)
    ).all()
    if not weeks:
        return []

    week_ids = [week.id for week in weeks]
    files = session.exec(
        select(TeachingClassTaskFile)
        .where(TeachingClassTaskFile.week_id.in_(week_ids))
        .order_by(TeachingClassTaskFile.filename)
    ).all()
    files_by_week: dict[uuid.UUID, list[CourseWeeklyTaskFileStudent]] = {}
    for row in files:
        public_file = _pdf_file(row)
        if public_file is not None:
            files_by_week.setdefault(row.week_id, []).append(public_file)

    assignments = ai_assignment_service.list_student_ai_assignments(
        session,
        user_id=user_id,
        path_id=path_id,
    )
    assignments_by_session = {
        assignment.session_id: assignment
        for assignment in assignments
        if assignment.session_id is not None
    }
    judge_sessions = session.exec(
        select(TeacherJudgeSession).where(
            TeacherJudgeSession.teaching_class_id == teaching_class.id,
            TeacherJudgeSession.teaching_class_week_id.in_(week_ids),
        )
    ).all()
    checkpoints_by_week: dict[uuid.UUID, list[CourseWeeklyCheckpointStudent]] = {}
    for judge_session in judge_sessions:
        week_id = judge_session.teaching_class_week_id
        if week_id is None:
            continue
        assignment = assignments_by_session.get(judge_session.id)
        source_file = (
            session.get(TeacherJudgeFile, judge_session.selected_file_id)
            if judge_session.selected_file_id is not None
            else None
        )
        items = assignment.items if assignment is not None else _source_items(source_file)
        task_title = (
            assignment.title
            if assignment is not None
            else (source_file.display_name if source_file else judge_session.title)
        )
        for item in items:
            checkpoints_by_week.setdefault(week_id, []).append(
                CourseWeeklyCheckpointStudent(
                    id=item.id,
                    task_id=judge_session.id,
                    assignment_id=assignment.id if assignment is not None else None,
                    assignment_title=task_title,
                    check_available=assignment is not None,
                    title=item.title,
                    description=item.description,
                    detectable=item.detectable,
                    order=item.order,
                    latest_check=(
                        assignment.checkpoint_checks.get(item.id)
                        if assignment is not None
                        else None
                    ),
                )
            )

    return [
        CourseWeeklyTaskStudent(
            id=week.id,
            teaching_class_id=teaching_class.id,
            teaching_class_name=teaching_class.name,
            week_number=week.week_number,
            session_date=week.session_date,
            title=week.title,
            target_node_key=week.target_node_key,
            files=files_by_week.get(week.id, []),
            checkpoints=sorted(
                checkpoints_by_week.get(week.id, []), key=lambda item: item.order
            ),
        )
        for week in weeks
    ]


def get_student_weekly_task_pdf(
    session: Session,
    *,
    user_id: uuid.UUID,
    path_id: uuid.UUID,
    week_id: uuid.UUID,
    file_id: uuid.UUID,
) -> tuple[Path, str]:
    teaching_class = course_service.get_student_class_for_path(
        session,
        user_id=user_id,
        path_id=path_id,
    )
    week = session.get(TeachingClassWeek, week_id)
    task_file = session.get(TeachingClassTaskFile, file_id)
    if (
        teaching_class is None
        or week is None
        or week.class_id != teaching_class.id
        or week.status not in VISIBLE_WEEK_STATUSES
        or task_file is None
        or task_file.week_id != week.id
        or not task_file.storage_key
        or not task_file.filename.lower().endswith(".pdf")
    ):
        raise NotFoundError(t("weekly_task.pdf_not_found"))

    root = TASK_FILE_ROOT.resolve()
    stored_path = (root / task_file.storage_key).resolve()
    if not stored_path.is_relative_to(root) or not stored_path.is_file():
        raise NotFoundError(t("weekly_task.pdf_not_found"))
    return stored_path, task_file.filename


__all__ = [
    "TASK_FILE_ROOT",
    "get_student_weekly_task_pdf",
    "list_student_weekly_tasks",
]
