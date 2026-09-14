"""Teacher-facing classes, weekly content and multi-machine orchestration."""

import csv
import io
import logging
import math
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import col, delete, func, select

from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_teaching_access
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    ClassCapacityReservation,
    CourseEnvironment,
    CourseEnvironmentEdge,
    CourseEnvironmentNode,
    CourseEnvironmentPublication,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassTaskFile,
    TeachingClassWeek,
    User,
    UserRole,
)
from app.models.base import get_datetime_utc
from app.repositories import resource as resource_repo
from app.repositories.user import get_user_by_email
from app.services.course import course_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.resource import resource_service
from app.services.teaching import (
    class_capacity_service,
    class_lifecycle_service,
    class_network_service,
    class_status_service,
)
from app.services.vm import batch_provision_service

router = APIRouter(prefix="/teaching-classes", tags=["teaching-classes"])
logger = logging.getLogger(__name__)

DAY_CODE = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
TASK_FILE_ROOT = Path(__file__).resolve().parents[3] / "data" / "teaching-class-tasks"
MAX_TASK_FILE_BYTES = 100 * 1024 * 1024
PUBLIC_PROVISION_ERROR = (
    "Machine provisioning failed. Retry or contact an administrator."
)


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    term: str = Field(min_length=1, max_length=80)
    location: str | None = Field(default=None, max_length=255)
    start_date: date
    end_date: date
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    timezone: str = "Asia/Taipei"
    boot_lead_minutes: int = Field(default=10, ge=0, le=120)
    shutdown_grace_minutes: int = Field(default=30, ge=0, le=240)


class ClassPatch(BaseModel):
    name: str | None = None
    term: str | None = None
    location: str | None = Field(default=None, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    start_time: time | None = None
    end_time: time | None = None
    timezone: str | None = None
    boot_lead_minutes: int | None = Field(default=None, ge=0, le=120)
    shutdown_grace_minutes: int | None = Field(default=None, ge=0, le=240)


class ClassExtend(BaseModel):
    end_date: date


class ClassArchive(BaseModel):
    reclaim_resources: bool = True
    force: bool = False


class StudentAdd(BaseModel):
    emails: list[str]


class MachineNodeIn(BaseModel):
    node_key: str
    source_type: str = "template"
    source_template_id: uuid.UUID | None = None
    custom_image_ref: str | None = None
    custom_storage: str | None = None
    custom_username: str | None = None
    custom_unprivileged: bool = True
    name: str
    role: str
    resource_type: str
    cpu: int
    memory_mb: int
    disk_gb: int
    network: str | None = None


class CourseSelect(BaseModel):
    course_version_id: uuid.UUID


class WeekFileIn(BaseModel):
    filename: str
    storage_key: str | None = None
    target_path: str | None = None


class WeekIn(BaseModel):
    week_number: int
    session_date: date
    title: str = ""
    target_node_key: str | None = None
    status: str = "draft"
    files: list[WeekFileIn] = Field(default_factory=list)


class ClassResourceUsageItem(BaseModel):
    vmid: int
    status: str
    cpu_usage_pct: float | None = None
    ram_usage_pct: float | None = None
    mem_used_bytes: int | None = None
    mem_total_bytes: int | None = None


class ClassResourceUsageResponse(BaseModel):
    collected_at: datetime
    items: list[ClassResourceUsageItem]


def _get_class(session: SessionDep, current_user, class_id: uuid.UUID) -> TeachingClass:
    item = session.get(TeachingClass, class_id)
    if not item:
        raise NotFoundError(t("teachingClasses.notFound"))
    require_teaching_access(current_user, item.owner_id)
    return item


def _students(session: SessionDep, class_id: uuid.UUID) -> list[TeachingClassStudent]:
    return list(
        session.exec(
            select(TeachingClassStudent)
            .where(TeachingClassStudent.class_id == class_id)
            .order_by(TeachingClassStudent.joined_at)
        ).all()
    )


def _public_machine_dump(row: TeachingClassStudentMachine) -> dict:
    """Serialize a class machine without exposing stored infrastructure errors."""
    data = row.model_dump()
    if data.get("error"):
        data["error"] = PUBLIC_PROVISION_ERROR
    return data


def _finite_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _usage_percent(used, total) -> float | None:
    used_number = _finite_float(used)
    total_number = _finite_float(total)
    if used_number is None or total_number is None or total_number <= 0:
        return None
    return round(max(0.0, min(100.0, used_number / total_number * 100)), 2)


def _class_resource_usage_items(
    vmids: list[int], cluster_resources: list[dict]
) -> list[ClassResourceUsageItem]:
    resources_by_vmid = {
        int(resource["vmid"]): resource
        for resource in cluster_resources
        if resource.get("vmid") is not None
    }
    items: list[ClassResourceUsageItem] = []
    for vmid in sorted(set(vmids)):
        resource = resources_by_vmid.get(vmid)
        if resource is None:
            items.append(ClassResourceUsageItem(vmid=vmid, status="unknown"))
            continue

        cpu_ratio = _finite_float(resource.get("cpu"))
        cpu_usage_pct = (
            round(max(0.0, min(100.0, cpu_ratio * 100)), 2)
            if cpu_ratio is not None
            else None
        )
        mem_used = resource.get("mem")
        mem_total = resource.get("maxmem")
        items.append(
            ClassResourceUsageItem(
                vmid=vmid,
                status=str(resource.get("status") or "unknown").lower(),
                cpu_usage_pct=cpu_usage_pct,
                ram_usage_pct=_usage_percent(mem_used, mem_total),
                mem_used_bytes=int(float(mem_used))
                if _finite_float(mem_used) is not None
                else None,
                mem_total_bytes=int(float(mem_total))
                if _finite_float(mem_total) is not None
                else None,
            )
        )
    return items


def _serialize(session: SessionDep, item: TeachingClass) -> dict:
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == item.id)
            .order_by(TeachingClassMachineNode.sort_order)
        ).all()
    )
    weeks = list(
        session.exec(
            select(TeachingClassWeek)
            .where(TeachingClassWeek.class_id == item.id)
            .order_by(TeachingClassWeek.week_number)
        ).all()
    )
    week_rows = []
    for week in weeks:
        files = session.exec(
            select(TeachingClassTaskFile).where(
                TeachingClassTaskFile.week_id == week.id
            )
        ).all()
        week_rows.append(
            {**week.model_dump(), "files": [row.model_dump() for row in files]}
        )

    enrollments = _students(session, item.id)
    enrollment_ids = [row.id for row in enrollments]
    user_ids = [row.user_id for row in enrollments]
    users = (
        {
            row.id: row
            for row in session.exec(select(User).where(User.id.in_(user_ids))).all()
        }
        if user_ids
        else {}
    )
    machine_rows = (
        list(
            session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id.in_(enrollment_ids)
                )
            ).all()
        )
        if enrollment_ids
        else []
    )
    machines_by_student: dict[uuid.UUID, list[dict]] = {}
    for row in machine_rows:
        machines_by_student.setdefault(row.class_student_id, []).append(
            _public_machine_dump(row)
        )
    student_rows = []
    for enrollment in enrollments:
        user = users.get(enrollment.user_id)
        student_rows.append(
            {
                **enrollment.model_dump(),
                "email": user.email if user else None,
                "full_name": user.full_name if user else None,
                "machines": machines_by_student.get(enrollment.id, []),
            }
        )

    jobs = [
        session.get(BatchProvisionJob, node.batch_job_id)
        for node in nodes
        if node.batch_job_id
    ]
    ready = sum(
        1 for row in machine_rows if row.status == "completed" and row.vmid is not None
    )
    course_environment = None
    topology_edges = []
    # 上課環境要畫得跟課程環境一樣：位置沿用老師在編輯器排好的座標
    # （班級複本沒有存座標，只能回頭讀環境版本），對外服務也一併帶出來。
    node_positions: dict[str, dict[str, float]] = {}
    publications: list[dict] = []
    if item.course_version_id:
        version = session.get(CourseEnvironmentVersion, item.course_version_id)
        environment = (
            session.get(CourseEnvironment, version.environment_id) if version else None
        )
        if version and environment:
            topology_edges = [
                row.model_dump()
                for row in session.exec(
                    select(CourseEnvironmentEdge).where(
                        CourseEnvironmentEdge.version_id == version.id
                    )
                ).all()
            ]
            node_positions = {
                row.node_key: {"x": row.position_x, "y": row.position_y}
                for row in session.exec(
                    select(CourseEnvironmentNode).where(
                        CourseEnvironmentNode.version_id == version.id
                    )
                ).all()
            }
            publications = [
                row.model_dump()
                for row in session.exec(
                    select(CourseEnvironmentPublication)
                    .where(CourseEnvironmentPublication.version_id == version.id)
                    .order_by(CourseEnvironmentPublication.sort_order)
                ).all()
            ]
            course_environment = {
                "id": environment.id,
                "version_id": version.id,
                "name": environment.name,
                "version": version.version,
                "status": version.status,
            }
    capacity = class_capacity_service.preview(
        session, nodes=nodes, students=enrollments
    )
    reservation = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == item.id
        )
    ).first()
    return {
        **item.model_dump(),
        "member_count": len(enrollments),
        "machine_nodes": [row.model_dump() for row in nodes],
        "weeks": week_rows,
        "students": student_rows,
        "ready_machines": ready,
        "total_machines": len(enrollments) * len(nodes),
        "provision_jobs": [
            {
                "id": job.id,
                "status": job.status,
                "total": job.total,
                "done": job.done,
                "failed_count": job.failed_count,
            }
            for job in jobs
            if job
        ],
        "course_environment": course_environment,
        "topology_edges": topology_edges,
        "node_positions": node_positions,
        "publications": publications,
        "capacity_preview": capacity,
        "capacity_reservation": reservation.model_dump() if reservation else None,
    }


# 列表頁只吃摘要欄位（班級數 × 週次數 筆查詢的 _serialize 會把首屏拖到數秒），
# 所以另走一條批次路徑：每種關聯各一次查詢，數量用 COUNT 讓資料庫算完再回來。
# 詳情頁仍走 _serialize，需要 students/拓撲/容量預估的那些欄位這裡一律不回。
def _serialize_list(session: SessionDep, items: list[TeachingClass]) -> list[dict]:
    if not items:
        return []
    class_ids = [item.id for item in items]

    nodes_by_class: dict[uuid.UUID, list[dict]] = {}
    for row in session.exec(
        select(TeachingClassMachineNode)
        .where(col(TeachingClassMachineNode.class_id).in_(class_ids))
        .order_by(TeachingClassMachineNode.sort_order)
    ).all():
        nodes_by_class.setdefault(row.class_id, []).append(row.model_dump())

    weeks_by_class: dict[uuid.UUID, list[dict]] = {}
    for row in session.exec(
        select(TeachingClassWeek)
        .where(col(TeachingClassWeek.class_id).in_(class_ids))
        .order_by(TeachingClassWeek.week_number)
    ).all():
        weeks_by_class.setdefault(row.class_id, []).append(row.model_dump())

    member_counts = dict(
        session.exec(
            select(col(TeachingClassStudent.class_id), func.count())
            .where(col(TeachingClassStudent.class_id).in_(class_ids))
            .group_by(col(TeachingClassStudent.class_id))
        ).all()
    )

    ready_counts = dict(
        session.exec(
            select(col(TeachingClassStudent.class_id), func.count())
            .join(
                TeachingClassStudentMachine,
                col(TeachingClassStudentMachine.class_student_id)
                == col(TeachingClassStudent.id),
            )
            .where(col(TeachingClassStudent.class_id).in_(class_ids))
            .where(col(TeachingClassStudentMachine.status) == "completed")
            .where(col(TeachingClassStudentMachine.vmid).is_not(None))
            .group_by(col(TeachingClassStudent.class_id))
        ).all()
    )

    version_ids = [item.course_version_id for item in items if item.course_version_id]
    environment_by_version: dict[uuid.UUID, dict] = {}
    if version_ids:
        for version, environment in session.exec(
            select(CourseEnvironmentVersion, CourseEnvironment)
            .join(
                CourseEnvironment,
                col(CourseEnvironment.id)
                == col(CourseEnvironmentVersion.environment_id),
            )
            .where(col(CourseEnvironmentVersion.id).in_(version_ids))
        ).all():
            environment_by_version[version.id] = {
                "id": environment.id,
                "version_id": version.id,
                "name": environment.name,
                "version": version.version,
                "status": version.status,
            }

    rows = []
    for item in items:
        nodes = nodes_by_class.get(item.id, [])
        members = member_counts.get(item.id, 0)
        rows.append(
            {
                **item.model_dump(),
                "member_count": members,
                "machine_nodes": nodes,
                "weeks": weeks_by_class.get(item.id, []),
                "ready_machines": ready_counts.get(item.id, 0),
                "total_machines": members * len(nodes),
                "course_environment": environment_by_version.get(
                    item.course_version_id
                ),
            }
        )
    return rows


def _validate_schedule(item) -> None:
    if item.end_date < item.start_date or item.end_time <= item.start_time:
        raise BadRequestError(t("teachingClasses.scheduleInvalid"))


@router.post("")
def create_class(body: ClassCreate, session: SessionDep, current_user: InstructorUser):
    class_id = uuid.uuid4()
    item = TeachingClass(
        id=class_id,
        owner_id=current_user.id,
        code=f"cls-{class_id.hex[:8]}",
        **body.model_dump(),
    )
    _validate_schedule(item)
    session.add(item)
    session.flush()
    course_service.ensure_class_path(
        session,
        teaching_class=item,
    )
    session.commit()
    session.refresh(item)
    _generate_weeks(session, item)
    return _serialize(session, item)


@router.get("")
def list_classes(session: SessionDep, current_user: InstructorUser):
    query = select(TeachingClass).order_by(TeachingClass.updated_at.desc())
    if not current_user.is_superuser and current_user.role != "admin":
        query = query.where(TeachingClass.owner_id == current_user.id)
    return _serialize_list(session, list(session.exec(query).all()))


@router.get("/{class_id}")
def get_class(class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser):
    return _serialize(session, _get_class(session, current_user, class_id))


@router.get(
    "/{class_id}/resource-usage",
    response_model=ClassResourceUsageResponse,
)
def get_class_resource_usage(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> ClassResourceUsageResponse:
    item = _get_class(session, current_user, class_id)
    enrollment_ids = [row.id for row in _students(session, item.id)]
    machine_rows = (
        list(
            session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id.in_(enrollment_ids)
                )
            ).all()
        )
        if enrollment_ids
        else []
    )
    vmids = [row.vmid for row in machine_rows if row.vmid is not None]
    resources = proxmox_service.list_all_resources() if vmids else []
    return ClassResourceUsageResponse(
        collected_at=get_datetime_utc(),
        items=_class_resource_usage_items(vmids, resources),
    )


@router.patch("/{class_id}")
def update_class(
    class_id: uuid.UUID,
    body: ClassPatch,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.fixedScheduleLocked"))
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(item, key, value)
    _validate_schedule(item)
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    _generate_weeks(session, item, preserve=True)
    return _serialize(session, item)


@router.post("/{class_id}/extend")
def extend_class(
    class_id: uuid.UUID,
    body: ClassExtend,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.archivedCannotExtend"))
    if body.end_date <= item.end_date:
        raise BadRequestError(t("teachingClasses.extendDateMustBeLater"))
    item.end_date = body.end_date
    item.updated_at = get_datetime_utc()
    item.resources_reclaimed_at = None
    for resource in resource_repo.get_resources_by_teaching_class(
        session=session, teaching_class_id=class_id
    ):
        resource.expiry_date = body.end_date
        resource.expiry_notified_at = None
        resource.scheduled_deletion_at = None
        session.add(resource)
    class_lifecycle_service.clear_schedule_windows(session, class_id)
    session.add(item)
    session.commit()
    _generate_weeks(session, item, preserve=True)
    return _serialize(session, item)


@router.post("/{class_id}/archive")
def archive_class(
    class_id: uuid.UUID,
    body: ClassArchive,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    reclaim = class_lifecycle_service.archive_and_reclaim(
        session=session,
        item=item,
        requested_by=current_user.id,
        force=body.force,
        reclaim_resources=body.reclaim_resources,
    )
    return {"class": _serialize(session, item), "reclaim": reclaim}


@router.post("/{class_id}/reclaim")
def reclaim_class_resources(
    class_id: uuid.UUID,
    body: ClassArchive,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.mustBeArchivedBeforeReclaim"))
    return class_lifecycle_service.queue_reclaim(
        session=session,
        item=item,
        requested_by=current_user.id,
        force=body.force,
    )


@router.post("/{class_id}/students")
def add_students(
    class_id: uuid.UUID,
    body: StudentAdd,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.studentsLocked"))
    existing = {row.user_id for row in _students(session, class_id)}
    added, not_found, invalid_role = 0, [], []
    for raw in body.emails:
        email = raw.strip().lower()
        user = get_user_by_email(session=session, email=email)
        if not user:
            not_found.append(email)
        elif user.role != UserRole.student:
            invalid_role.append(email)
        elif user.id not in existing:
            session.add(TeachingClassStudent(class_id=class_id, user_id=user.id))
            existing.add(user.id)
            added += 1
    session.commit()
    return {
        "added": added,
        "not_found": not_found,
        "invalid_role": invalid_role,
        "class": _serialize(session, item),
    }


@router.delete("/{class_id}/students/{student_id}")
def remove_student(
    class_id: uuid.UUID,
    student_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.studentsLocked"))
    row = session.get(TeachingClassStudent, student_id)
    if not row or row.class_id != class_id:
        raise NotFoundError(t("teachingClasses.studentNotFound"))
    session.delete(row)
    session.commit()
    return _serialize(session, item)


@router.post("/{class_id}/students/import-csv")
async def import_students(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.studentsLocked"))
    raw = await file.read()
    content = None
    for encoding in ("cp950", "utf-8-sig", "utf-8"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise BadRequestError(t("teachingClasses.csvDecodeFailed"))
    emails = []
    for index, row in enumerate(csv.reader(io.StringIO(content))):
        if not row:
            continue
        value = row[0].strip()
        if index == 0 and value.lower() in {"email", "學號", "帳號"}:
            continue
        if value:
            emails.append(value if "@" in value else f"{value}@ntub.edu.tw")
    return add_students(class_id, StudentAdd(emails=emails), session, current_user)


def _first_session_date(item: TeachingClass) -> date:
    """課程期間內第一個落在「每週上課日」的日期。

    ``start_date`` 只是課程期間的起點，它的星期未必等於 ``weekday``（例如學期
    從週一開始、但每週三上課），所以課次與排程都必須從這裡推算。
    """
    return item.start_date + timedelta(
        days=(item.weekday - item.start_date.weekday()) % 7
    )


def _generate_weeks(session, item: TeachingClass, preserve=False):
    """重建課次；``preserve`` 時沿用既有週次的主題與教材。

    對應的鍵是「第幾週」而不是上課日期：改動每週上課日或開始日期會讓所有日期
    整批位移，用日期比對會一筆都對不上，等於把老師填好的主題與上傳的教材全部
    刪掉。改用 week_number 之後，第 N 週的內容仍然留在第 N 週，只是日期跟著搬。
    """
    existing = (
        {
            row.week_number: row
            for row in session.exec(
                select(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
            ).all()
        }
        if preserve
        else {}
    )
    if not preserve:
        session.exec(
            delete(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
        )
    current = _first_session_date(item)
    number, keep = 1, set()
    while current <= item.end_date:
        keep.add(number)
        row = existing.get(number)
        if row:
            row.session_date = current
            session.add(row)
        else:
            session.add(
                TeachingClassWeek(
                    class_id=item.id, week_number=number, session_date=current
                )
            )
        current += timedelta(days=7)
        number += 1
    if preserve:
        for week_number, row in existing.items():
            if week_number not in keep:
                session.delete(row)
    session.commit()


@router.post("/{class_id}/generate-weeks")
def generate_weeks(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    item = _get_class(session, current_user, class_id)
    _generate_weeks(session, item, preserve=True)
    return _serialize(session, item)


@router.put("/{class_id}/machines")
def replace_machines(
    class_id: uuid.UUID,
    body: list[MachineNodeIn],
    session: SessionDep,
    current_user: InstructorUser,
):
    _get_class(session, current_user, class_id)
    raise BadRequestError(t("teachingClasses.machinesManagedByCourseEnvironment"))


@router.put("/{class_id}/course")
def select_course(
    class_id: uuid.UUID,
    body: CourseSelect,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning or item.locked_at is not None:
        raise BadRequestError(t("teachingClasses.courseLockedCannotChange"))
    version = session.get(CourseEnvironmentVersion, body.course_version_id)
    if version is None or version.status != CourseEnvironmentVersionStatus.published:
        raise BadRequestError(t("teachingClasses.onlyPublishedCourseVersion"))
    environment = session.get(CourseEnvironment, version.environment_id)
    if environment is None:
        raise NotFoundError(t("teachingClasses.courseEnvironmentNotFound"))
    if environment.usage_scope not in {"course", "both"}:
        raise BadRequestError(t("teachingClasses.environmentNotForFormalCourse"))
    require_teaching_access(current_user, environment.owner_id)
    source_nodes = list(
        session.exec(
            select(CourseEnvironmentNode)
            .where(CourseEnvironmentNode.version_id == version.id)
            .order_by(CourseEnvironmentNode.sort_order)
        ).all()
    )
    if not source_nodes:
        raise BadRequestError(t("teachingClasses.courseVersionNoMachines"))
    session.exec(
        delete(TeachingClassMachineNode).where(
            TeachingClassMachineNode.class_id == class_id
        )
    )
    for node in source_nodes:
        session.add(
            TeachingClassMachineNode(
                class_id=class_id,
                node_key=node.node_key,
                source_type=node.source_type,
                source_template_id=node.source_template_id,
                custom_image_ref=node.custom_image_ref,
                custom_storage=None,
                custom_username=node.custom_username,
                custom_unprivileged=node.custom_unprivileged,
                name=node.name,
                role=node.role,
                resource_type=node.resource_type,
                cpu=node.cpu,
                memory_mb=node.memory_mb,
                # 克隆機不可能小於來源範本；把下限寫進班級節點，之後的容量
                # 預檢、IP/資源保留與開機才會用同一個數字。
                disk_gb=provisioning_service.clone_source_disk_gb(session, node),
                network=node.network,
                sort_order=node.sort_order,
            )
        )
    item.course_version_id = version.id
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    return _serialize(session, item)


@router.put("/{class_id}/weeks")
def replace_weeks(
    class_id: uuid.UUID,
    body: list[WeekIn],
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.archivedCannotEditWeeklyContent"))
    expected = {
        row.session_date
        for row in session.exec(
            select(TeachingClassWeek).where(TeachingClassWeek.class_id == class_id)
        ).all()
    }
    received = {row.session_date for row in body}
    if expected != received:
        raise BadRequestError(t("teachingClasses.weekDatesMustMatchSchedule"))
    session.exec(
        delete(TeachingClassWeek).where(TeachingClassWeek.class_id == class_id)
    )
    session.commit()
    for row in body:
        week = TeachingClassWeek(class_id=class_id, **row.model_dump(exclude={"files"}))
        session.add(week)
        session.flush()
        for file in row.files:
            session.add(TeachingClassTaskFile(week_id=week.id, **file.model_dump()))
    session.commit()
    return _serialize(session, item)


@router.post("/{class_id}/weeks/{week_id}/files")
async def upload_week_file(
    class_id: uuid.UUID,
    week_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
    file: UploadFile = File(...),
):
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.archivedCannotEditWeeklyContent"))
    week = session.get(TeachingClassWeek, week_id)
    if not week or week.class_id != class_id:
        raise NotFoundError(t("teachingClasses.weekNotFound"))

    filename = (file.filename or "task-file").replace("\\", "/").split("/")[-1].strip()
    if not filename or filename in {".", ".."}:
        raise BadRequestError(t("teachingClasses.invalidFileName"))
    if len(filename) > 255:
        raise BadRequestError(t("teachingClasses.taskFileNameTooLong"))

    file_id = uuid.uuid4()
    storage_key = f"{file_id.hex}.task"
    destination = TASK_FILE_ROOT / storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_TASK_FILE_BYTES:
                    raise BadRequestError(t("teachingClasses.taskFileTooLarge"))
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    session.add(
        TeachingClassTaskFile(
            id=file_id,
            week_id=week_id,
            filename=filename,
            storage_key=storage_key,
        )
    )
    session.commit()
    return _serialize(session, item)


@router.delete("/{class_id}/weeks/{week_id}/files/{file_id}")
def delete_week_file(
    class_id: uuid.UUID,
    week_id: uuid.UUID,
    file_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.archivedCannotEditWeeklyContent"))
    week = session.get(TeachingClassWeek, week_id)
    task_file = session.get(TeachingClassTaskFile, file_id)
    if (
        not week
        or week.class_id != class_id
        or not task_file
        or task_file.week_id != week_id
    ):
        raise NotFoundError(t("teachingClasses.taskFileNotFound"))

    storage_key = task_file.storage_key
    session.delete(task_file)
    session.commit()
    if storage_key:
        root = TASK_FILE_ROOT.resolve()
        stored_path = (root / storage_key).resolve()
        if stored_path.is_relative_to(root):
            stored_path.unlink(missing_ok=True)
    return _serialize(session, item)


def _recurrence(item: TeachingClass):
    first_session = _first_session_date(item)
    start = datetime.combine(first_session, item.start_time) - timedelta(
        minutes=item.boot_lead_minutes
    )
    duration = (
        int(
            (
                datetime.combine(first_session, item.end_time)
                - datetime.combine(first_session, item.start_time)
            ).total_seconds()
            / 60
        )
        + item.boot_lead_minutes
        + int(getattr(item, "shutdown_grace_minutes", 0) or 0)
    )
    return (
        f"FREQ=WEEKLY;BYDAY={DAY_CODE[start.weekday()]};BYHOUR={start.hour};BYMINUTE={start.minute}",
        duration,
    )


def _node_source_params(node: TeachingClassMachineNode) -> dict:
    if node.source_type == "template" and node.source_template_id:
        return {"vm_template_id": str(node.source_template_id)}
    if node.resource_type.lower() == "lxc":
        return {
            "ostemplate": node.custom_image_ref,
            "storage": "local-lvm",
            "unprivileged": node.custom_unprivileged,
        }
    return {
        "template_id": int(node.custom_image_ref or "0"),
        "storage": "local-lvm",
        "username": node.custom_username or "student",
    }


def _submit_node_job(
    session: SessionDep,
    *,
    item: TeachingClass,
    node: TeachingClassMachineNode,
    member_user_ids: list[uuid.UUID],
    retry: bool = False,
) -> uuid.UUID:
    rule, duration = _recurrence(item)
    retry_suffix = f"-r{uuid.uuid4().hex[:8]}" if retry else ""
    return batch_provision_service.submit_batch_job_for_users(
        session=session,
        member_user_ids=member_user_ids,
        teaching_class_id=item.id,
        initiated_by_id=item.owner_id,
        resource_type="lxc" if node.resource_type.lower() == "lxc" else "qemu",
        hostname_prefix=(
            f"{item.code.lower().replace('_', '-')[:35]}-"
            f"{node.sort_order + 1}{retry_suffix}"
        ),
        params={
            **_node_source_params(node),
            "cores": node.cpu,
            "memory": node.memory_mb,
            "disk_size": node.disk_gb,
            "rootfs_size": node.disk_gb,
            "environment_type": f"{item.name}-{node.role}",
            "expiry_date": item.end_date.isoformat(),
            "ip_reservation_prefix": f"{item.id}:{node.node_key}",
        },
        recurrence_rule=rule,
        recurrence_duration_minutes=duration,
        schedule_timezone=item.timezone,
        capacity_reserved=True,
    )


@router.get("/{class_id}/capacity-preview")
def capacity_preview(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.onlyPlanningCanRecheckCapacity"))
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == class_id)
            .order_by(TeachingClassMachineNode.sort_order)
        ).all()
    )
    students = _students(session, class_id)
    return class_capacity_service.preview(
        session,
        nodes=nodes,
        students=students,
        check_cluster=True,
    )


@router.post("/{class_id}/provision")
def provision_class(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    item = _get_class(session, current_user, class_id)
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == class_id)
            .order_by(TeachingClassMachineNode.sort_order)
        ).all()
    )
    students = _students(session, class_id)
    if not nodes or not students:
        raise BadRequestError(t("teachingClasses.studentsAndMachinesRequired"))
    if item.status != TeachingClassStatus.planning or item.locked_at is not None:
        raise BadRequestError(t("teachingClasses.classLockedOrSubmitted"))
    if item.course_version_id is None:
        raise BadRequestError(t("teachingClasses.selectPublishedCourseFirst"))
    class_capacity_service.reserve(
        session,
        class_id=item.id,
        course_version_id=item.course_version_id,
        nodes=nodes,
        students=students,
    )
    item.locked_at = get_datetime_utc()
    session.add(item)
    session.commit()
    for node in nodes:
        if node.batch_job_id:
            continue
        node.batch_job_id = _submit_node_job(
            session=session,
            item=item,
            node=node,
            member_user_ids=[row.user_id for row in students],
        )
        session.add(node)
        session.commit()
    item.status = TeachingClassStatus.pending_review
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    return _serialize(session, item)


@router.post("/{class_id}/retry-failed")
def retry_failed_class(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status not in {
        TeachingClassStatus.partial_failed,
        TeachingClassStatus.provisioning,
    }:
        raise BadRequestError(t("teachingClasses.onlyFailedCanRetry"))
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == class_id)
            .order_by(TeachingClassMachineNode.sort_order)
        ).all()
    )
    submitted = 0
    recovered = 0
    stale_before = get_datetime_utc() - timedelta(minutes=30)
    for node in nodes:
        job = session.get(BatchProvisionJob, node.batch_job_id) if node.batch_job_id else None
        if not job:
            continue
        tasks = list(
            session.exec(
                select(BatchProvisionTask).where(BatchProvisionTask.job_id == job.id)
            ).all()
        )
        retry_user_ids = []
        for task in tasks:
            started_at = task.started_at
            if started_at and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            stale = (
                task.status == BatchProvisionTaskStatus.running
                and started_at is not None
                and started_at <= stale_before
            )
            terminal_job = job.status in {
                BatchProvisionJobStatus.failed,
                BatchProvisionJobStatus.rejected,
                BatchProvisionJobStatus.cancelled,
            }
            retryable = (
                task.status == BatchProvisionTaskStatus.failed
                or stale
                or (
                    terminal_job
                    and task.status != BatchProvisionTaskStatus.completed
                )
            )
            if not retryable:
                continue
            if _recover_existing_task_resource(
                session=session,
                item=item,
                node=node,
                task=task,
            ):
                recovered += 1
                continue
            if stale or (
                terminal_job
                and task.status != BatchProvisionTaskStatus.failed
            ):
                task.status = BatchProvisionTaskStatus.failed
                task.error = (
                    "Stale provisioning task superseded by class retry"
                    if stale
                    else "Incomplete task superseded by class retry"
                )
                task.finished_at = get_datetime_utc()
                session.add(task)
            retry_user_ids.append(task.user_id)
        job.done = sum(
            task.status == BatchProvisionTaskStatus.completed for task in tasks
        )
        job.failed_count = sum(
            task.status == BatchProvisionTaskStatus.failed for task in tasks
        )
        if job.done == job.total and job.failed_count == 0:
            job.status = BatchProvisionJobStatus.completed
            job.finished_at = get_datetime_utc()
        session.add(job)
        session.commit()
        if not retry_user_ids:
            continue
        if job.status == BatchProvisionJobStatus.running:
            job.status = BatchProvisionJobStatus.failed
            job.finished_at = get_datetime_utc()
            session.add(job)
            session.commit()
        node.batch_job_id = _submit_node_job(
            session=session,
            item=item,
            node=node,
            member_user_ids=retry_user_ids,
            retry=True,
        )
        session.add(node)
        session.commit()
        submitted += 1

    current_jobs = [
        session.get(BatchProvisionJob, node.batch_job_id)
        for node in nodes
        if node.batch_job_id
    ]
    all_jobs_ready = (
        len(current_jobs) == len(nodes)
        and bool(nodes)
        and all(
            job is not None
            and job.status == BatchProvisionJobStatus.completed
            and job.done == job.total
            and job.failed_count == 0
            for job in current_jobs
        )
    )
    if submitted:
        item.status = TeachingClassStatus.pending_review
    elif item.status == TeachingClassStatus.provisioning and not recovered:
        raise BadRequestError(t("teachingClasses.noFailedOrStaleTasksToRetry"))
    elif not all_jobs_ready:
        item.status = TeachingClassStatus.provisioning
    else:
        topology_errors = class_network_service.apply_class_topology(
            session, class_id=class_id
        )
        if topology_errors:
            raise BadRequestError(
                t(
                    "teachingClasses.topologyRetryFailed",
                    details="；".join(topology_errors),
                )
            )
        item.status = TeachingClassStatus.active
        course_service.ensure_class_path(
            session,
            teaching_class=item,
            published=True,
        )
        reservation = session.exec(
            select(ClassCapacityReservation).where(
                ClassCapacityReservation.class_id == class_id
            )
        ).first()
        if reservation:
            reservation.status = "consumed"
            session.add(reservation)
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    return _serialize(session, item)


def _recover_existing_task_resource(
    *,
    session: SessionDep,
    item: TeachingClass,
    node: TeachingClassMachineNode,
    task: BatchProvisionTask,
) -> bool:
    """Recover a VM created before a worker crash instead of cloning twice."""
    resource = session.exec(
        select(Resource).where(
            Resource.batch_job_id == task.job_id,
            Resource.user_id == task.user_id,
        )
    ).first()
    if resource is None:
        return False
    try:
        proxmox_service.find_resource(resource.vmid)
    except NotFoundError:
        resource_service.delete_orphan_db_record(
            session=session,
            vmid=resource.vmid,
            user_id=item.owner_id,
        )
        session.commit()
        return False
    except Exception:
        logger.exception(
            "Failed to verify existing class resource class_id=%s vmid=%s",
            item.id,
            resource.vmid,
        )
        raise BadRequestError(
            t("teachingClasses.cannotVerifyResourceRetryLater")
        ) from None

    resource_repo.assign_to_teaching_class(
        session=session,
        vmid=resource.vmid,
        teaching_class_id=item.id,
        commit=False,
    )
    enrollment = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == item.id,
            TeachingClassStudent.user_id == task.user_id,
        )
    ).first()
    if enrollment is None:
        raise BadRequestError(t("teachingClasses.provisionedUserNoLongerInClass"))
    mapping = session.exec(
        select(TeachingClassStudentMachine).where(
            TeachingClassStudentMachine.class_student_id == enrollment.id,
            TeachingClassStudentMachine.machine_node_id == node.id,
        )
    ).first()
    if mapping is None:
        mapping = TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=node.id,
        )
    mapping.batch_task_id = task.id
    mapping.vmid = resource.vmid
    mapping.status = "completed"
    mapping.error = None
    task.status = BatchProvisionTaskStatus.completed
    task.vmid = resource.vmid
    task.resource_vmid = resource.vmid
    task.error = None
    task.finished_at = get_datetime_utc()
    session.add(mapping)
    session.add(task)
    session.flush()
    return True


@router.post("/{class_id}/reset-failed")
def reset_failed_class(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.partial_failed:
        raise BadRequestError(t("teachingClasses.onlyFailedCanResetToEdit"))
    enrollment_ids = [row.id for row in _students(session, class_id)]
    machine_rows = (
        list(
            session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id.in_(enrollment_ids)
                )
            ).all()
        )
        if enrollment_ids
        else []
    )
    if any(row.vmid is not None for row in machine_rows):
        raise BadRequestError(t("teachingClasses.partialMachinesUseRetry"))
    for row in machine_rows:
        session.delete(row)
    nodes = session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.class_id == class_id
        )
    ).all()
    for node in nodes:
        node.batch_job_id = None
        session.add(node)
    class_capacity_service.release(session, class_id=class_id)
    item.status = TeachingClassStatus.planning
    item.locked_at = None
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    return _serialize(session, item)


@router.get("/{class_id}/provision-status")
def provision_status(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        return _serialize(session, item)
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == class_id
            )
        ).all()
    )
    students = _students(session, class_id)
    enrollment_by_user = {row.user_id: row for row in students}
    for node in nodes:
        job = (
            session.get(BatchProvisionJob, node.batch_job_id)
            if node.batch_job_id
            else None
        )
        if not job:
            continue
        tasks = session.exec(
            select(BatchProvisionTask).where(BatchProvisionTask.job_id == job.id)
        ).all()
        for task in tasks:
            enrollment = enrollment_by_user.get(task.user_id)
            if not enrollment:
                continue
            mapping = session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id == enrollment.id,
                    TeachingClassStudentMachine.machine_node_id == node.id,
                )
            ).first()
            if not mapping:
                mapping = TeachingClassStudentMachine(
                    class_student_id=enrollment.id, machine_node_id=node.id
                )
            mapping.batch_task_id = task.id
            mapping.vmid = task.vmid
            mapping.status = (
                task.status.value if hasattr(task.status, "value") else str(task.status)
            )
            mapping.error = task.error
            session.add(mapping)
    class_status_service.recompute(session=session, class_id=class_id)
    session.commit()
    session.refresh(item)
    return _serialize(session, item)
