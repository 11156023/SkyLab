"""Student home reminders derived from existing platform records."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.core.i18n import t
from app.models import (
    CoursePath,
    CoursePathStatus,
    Resource,
    TeachingClass,
    TeachingClassStudent,
    TeachingClassWeek,
    VMRequest,
    VMRequestStatus,
)
from app.schemas.course import CourseReminderStudent

_TAIPEI = ZoneInfo("Asia/Taipei")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _date_label(value: date, today: date) -> str:
    if value == today:
        return t("course_reminder.today")
    if value == today + timedelta(days=1):
        return t("course_reminder.tomorrow")
    return f"{value.month}/{value.day}"


def list_student_reminders(
    session: Session,
    *,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> list[CourseReminderStudent]:
    """Return actionable, non-persistent reminders for one student."""

    current = _aware(now or datetime.now(UTC))
    local_now = current.astimezone(_TAIPEI)
    today = local_now.date()
    reminders: list[CourseReminderStudent] = []

    resource_rows = session.exec(
        select(Resource, VMRequest)
        .outerjoin(VMRequest, Resource.request_id == VMRequest.id)
        .where(
            Resource.user_id == user_id,
            Resource.expiry_date.is_not(None),
            Resource.expiry_date >= today,
            Resource.expiry_date <= today + timedelta(days=7),
        )
        .order_by(Resource.expiry_date)
    ).all()
    for resource, request in resource_rows:
        expiry = resource.expiry_date
        if expiry is None:
            continue
        days = (expiry - today).days
        resource_name = (
            request.hostname
            if request
            else t("course_reminder.resource_fallback_name", vmid=resource.vmid)
        )
        if days == 0:
            description = t("course_reminder.resource_expires_today")
        else:
            description = t("course_reminder.resource_expires_in_days", days=days)
        reminders.append(
            CourseReminderStudent(
                id=f"resource-expiry:{resource.vmid}:{expiry.isoformat()}",
                kind="resource_expiry",
                tone="warning" if days > 1 else "danger",
                icon="schedule",
                title=t("course_reminder.resource_expiring_title", name=resource_name),
                description=description,
                time_label=_date_label(expiry, today),
                target="/my-resources",
                occurred_at=datetime.combine(expiry, time.max, tzinfo=_TAIPEI),
            )
        )

    review_cutoff = current - timedelta(days=14)
    request_rows = session.exec(
        select(VMRequest)
        .where(
            VMRequest.user_id == user_id,
            VMRequest.reviewed_at.is_not(None),
            VMRequest.reviewed_at >= review_cutoff,
            VMRequest.status.in_(
                [VMRequestStatus.approved, VMRequestStatus.rejected]
            ),
        )
        .order_by(VMRequest.reviewed_at.desc())
    ).all()
    for request in request_rows:
        reviewed_at = _aware(request.reviewed_at or current)
        approved = request.status == VMRequestStatus.approved
        reminders.append(
            CourseReminderStudent(
                id=f"request-review:{request.id}:{request.status.value}",
                kind="request_review",
                tone="success" if approved else "danger",
                icon="check_circle" if approved else "cancel",
                title=t(
                    "course_reminder.request_approved_title"
                    if approved
                    else "course_reminder.request_rejected_title"
                ),
                description=t(
                    "course_reminder.request_approved_description"
                    if approved
                    else "course_reminder.request_rejected_description",
                    hostname=request.hostname,
                ),
                time_label=_date_label(
                    reviewed_at.astimezone(_TAIPEI).date(), today
                ),
                target="/my-requests",
                occurred_at=reviewed_at,
            )
        )

    task_rows = session.exec(
        select(TeachingClassWeek, TeachingClass, CoursePath)
        .join(TeachingClass, TeachingClassWeek.class_id == TeachingClass.id)
        .join(
            TeachingClassStudent,
            TeachingClassStudent.class_id == TeachingClass.id,
        )
        .join(CoursePath, CoursePath.teaching_class_id == TeachingClass.id)
        .where(
            TeachingClassStudent.user_id == user_id,
            TeachingClassStudent.status == "active",
            CoursePath.status == CoursePathStatus.published,
            TeachingClassWeek.status.in_(["published", "completed"]),
            TeachingClassWeek.session_date >= today,
            TeachingClassWeek.session_date <= today + timedelta(days=2),
            TeachingClassWeek.title != "",
        )
        .order_by(TeachingClassWeek.session_date)
    ).all()
    for week, teaching_class, path in task_rows:
        label = _date_label(week.session_date, today)
        reminders.append(
            CourseReminderStudent(
                id=f"class-task:{week.id}",
                kind="class_task",
                tone="info" if week.session_date > today else "warning",
                icon="assignment",
                title=t(
                    "course_reminder.class_task_title",
                    class_name=teaching_class.name,
                    title=week.title,
                ),
                description=t(
                    "course_reminder.class_task_description",
                    week=week.week_number,
                ),
                time_label=label,
                target=f"/dashboard/course/{path.id}",
                occurred_at=datetime.combine(
                    week.session_date,
                    teaching_class.start_time,
                    tzinfo=_TAIPEI,
                ),
            )
        )

    # Upcoming urgency first; recently reviewed requests still remain near the top.
    return sorted(reminders, key=lambda item: item.occurred_at)[:12]


__all__ = ["list_student_reminders"]
