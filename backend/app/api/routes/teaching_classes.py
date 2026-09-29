"""Teacher-facing classes, weekly content and multi-machine orchestration."""

import csv
import io
import uuid

from fastapi import APIRouter, File, UploadFile
from sqlmodel import col, func, select

from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import (
    can_bypass_teaching_ownership,
    require_teaching_access,
)
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    INSTRUCTOR_ENROLLMENT_STATUS,
    BatchProvisionJob,
    ClassCapacityReservation,
    CourseEnvironment,
    CourseEnvironmentEdge,
    CourseEnvironmentNode,
    CourseEnvironmentPublication,
    CourseEnvironmentVersion,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassTaskFile,
    TeachingClassWeek,
    User,
    UserRole,
    VMTemplate,
)
from app.models.base import get_datetime_utc
from app.repositories import resource as resource_repo
from app.repositories.user import get_user_by_email
from app.schemas.teaching_class import (
    ClassArchive,
    ClassCreate,
    ClassExtend,
    ClassPatch,
    ClassResourceUsageResponse,
    CourseSelect,
    InstructorMachineIn,
    MachineNodeIn,
    StudentAdd,
    WeekIn,
)
from app.schemas.teaching_class import (
    # WeekIn.files 的元素型別；route 本身不直接用，顯式轉出讓 routes.WeekFileIn 可用
    WeekFileIn as WeekFileIn,
)
from app.services.course import course_service, weekly_task_service
from app.services.course_environment import upload_store
from app.services.proxmox import proxmox_service
from app.services.teaching import (
    class_capacity_service,
    class_lifecycle_service,
    class_provision_service,
    class_status_service,
)

router = APIRouter(prefix="/teaching-classes", tags=["teaching-classes"])

# 教材檔的根目錄只定義在 weekly_task_service.TASK_FILE_ROOT（學生下載也讀它）；
# 這裡一律在呼叫當下讀該模組屬性，換掉它就同時涵蓋上傳、刪除與下載。
MAX_TASK_FILE_BYTES = 100 * 1024 * 1024
# 學生名單 CSV：一行一個帳號，1 MB 已足夠放上萬筆
MAX_STUDENT_CSV_BYTES = 1024 * 1024
PUBLIC_PROVISION_ERROR = (
    "Machine provisioning failed. Retry or contact an administrator."
)

# 課次推算與資源用量的計算搬到 service 層；舊名稱留作別名，
# 既有的測試與呼叫端（``routes._generate_weeks`` 等）不必跟著改。
_generate_weeks = class_lifecycle_service.generate_weeks
_class_resource_usage_items = class_status_service.class_resource_usage_items


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


def _class_nodes(
    session: SessionDep, class_id: uuid.UUID
) -> list[TeachingClassMachineNode]:
    """班級的機器節點，依編輯器排序。"""
    return list(
        session.exec(
            select(TeachingClassMachineNode)
            .where(TeachingClassMachineNode.class_id == class_id)
            .order_by(col(TeachingClassMachineNode.sort_order))
        ).all()
    )


def _class_machine_rows(
    session: SessionDep, enrollment_ids: list[uuid.UUID]
) -> list[TeachingClassStudentMachine]:
    """這些選課紀錄對應到的學生機器；沒有選課紀錄就不查。"""
    if not enrollment_ids:
        return []
    return list(
        session.exec(
            select(TeachingClassStudentMachine).where(
                col(TeachingClassStudentMachine.class_student_id).in_(enrollment_ids)
            )
        ).all()
    )


def _course_environment_summary(
    environment: CourseEnvironment, version: CourseEnvironmentVersion
) -> dict[str, object]:
    return {
        "id": environment.id,
        "version_id": version.id,
        "name": environment.name,
        "version": version.version,
        "status": version.status,
    }


def _public_machine_dump(row: TeachingClassStudentMachine) -> dict:
    """Serialize a class machine without exposing stored infrastructure errors."""
    data = row.model_dump()
    if data.get("error"):
        data["error"] = PUBLIC_PROVISION_ERROR
    return data


def _machine_node_dump(
    row: TeachingClassMachineNode, template_names: dict[uuid.UUID, str]
) -> dict:
    # 對照用：範本來源的節點補上 vm_templates.name（自訂節點為 None，
    # 來源本來就記在 custom_image_ref）。
    data = row.model_dump()
    data["template_name"] = template_names.get(row.source_template_id)
    return data


def _template_names_for_nodes(
    session: SessionDep, nodes: list[TeachingClassMachineNode]
) -> dict[uuid.UUID, str]:
    template_ids = {row.source_template_id for row in nodes if row.source_template_id}
    if not template_ids:
        return {}
    return {
        row.id: row.name
        for row in session.exec(
            select(VMTemplate).where(col(VMTemplate.id).in_(template_ids))
        ).all()
    }


def _serialize(session: SessionDep, item: TeachingClass) -> dict:
    nodes = _class_nodes(session, item.id)
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
    machine_rows = _class_machine_rows(session, enrollment_ids)
    machines_by_student: dict[uuid.UUID, list[dict]] = {}
    for row in machine_rows:
        machines_by_student.setdefault(row.class_student_id, []).append(
            _public_machine_dump(row)
        )
    student_rows = []
    instructor_row = None
    for enrollment in enrollments:
        user = users.get(enrollment.user_id)
        row = {
            **enrollment.model_dump(),
            "email": user.email if user else None,
            "full_name": user.full_name if user else None,
            "machines": machines_by_student.get(enrollment.id, []),
        }
        if enrollment.status == INSTRUCTOR_ENROLLMENT_STATUS:
            instructor_row = row
        else:
            student_rows.append(row)

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
            course_environment = _course_environment_summary(environment, version)
    capacity = class_capacity_service.preview(
        session, nodes=nodes, students=enrollments
    )
    reservation = session.exec(
        select(ClassCapacityReservation).where(
            ClassCapacityReservation.class_id == item.id
        )
    ).first()
    template_names = _template_names_for_nodes(session, nodes)
    return {
        **item.model_dump(),
        "member_count": len(student_rows),
        "machine_nodes": [_machine_node_dump(row, template_names) for row in nodes],
        "weeks": week_rows,
        "students": student_rows,
        # 老師自己那套機器不列進學生名單，但仍算進 total_machines 與容量
        "instructor_machine": instructor_row,
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

    nodes_by_class: dict[uuid.UUID, list[TeachingClassMachineNode]] = {}
    node_rows: list[TeachingClassMachineNode] = []
    for row in session.exec(
        select(TeachingClassMachineNode)
        .where(col(TeachingClassMachineNode.class_id).in_(class_ids))
        .order_by(TeachingClassMachineNode.sort_order)
    ).all():
        nodes_by_class.setdefault(row.class_id, []).append(row)
        node_rows.append(row)
    template_names = _template_names_for_nodes(session, node_rows)

    weeks_by_class: dict[uuid.UUID, list[dict]] = {}
    for row in session.exec(
        select(TeachingClassWeek)
        .where(col(TeachingClassWeek.class_id).in_(class_ids))
        .order_by(TeachingClassWeek.week_number)
    ).all():
        weeks_by_class.setdefault(row.class_id, []).append(row.model_dump())

    member_counts: dict[uuid.UUID, int] = {}
    enrollment_counts: dict[uuid.UUID, int] = {}
    for class_id, status, count in session.exec(
        select(
            col(TeachingClassStudent.class_id),
            col(TeachingClassStudent.status),
            func.count(),
        )
        .where(col(TeachingClassStudent.class_id).in_(class_ids))
        .group_by(col(TeachingClassStudent.class_id), col(TeachingClassStudent.status))
    ).all():
        enrollment_counts[class_id] = enrollment_counts.get(class_id, 0) + count
        if status != INSTRUCTOR_ENROLLMENT_STATUS:
            member_counts[class_id] = member_counts.get(class_id, 0) + count

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
            environment_by_version[version.id] = _course_environment_summary(
                environment, version
            )

    rows = []
    for item in items:
        nodes = nodes_by_class.get(item.id, [])
        members = member_counts.get(item.id, 0)
        rows.append(
            {
                **item.model_dump(),
                "member_count": members,
                "machine_nodes": [
                    _machine_node_dump(row, template_names) for row in nodes
                ],
                "weeks": weeks_by_class.get(item.id, []),
                "ready_machines": ready_counts.get(item.id, 0),
                "total_machines": enrollment_counts.get(item.id, 0) * len(nodes),
                "course_environment": environment_by_version.get(
                    item.course_version_id
                ),
            }
        )
    return rows


@router.post("")
def create_class(body: ClassCreate, session: SessionDep, current_user: InstructorUser):
    class_id = uuid.uuid4()
    item = TeachingClass(
        id=class_id,
        owner_id=current_user.id,
        code=f"cls-{class_id.hex[:8]}",
        **body.model_dump(),
    )
    class_lifecycle_service.validate_schedule(item)
    session.add(item)
    session.flush()
    course_service.ensure_class_path(
        session,
        teaching_class=item,
    )
    session.commit()
    session.refresh(item)
    class_lifecycle_service.generate_weeks(session, item)
    return _serialize(session, item)


@router.get("")
def list_classes(session: SessionDep, current_user: InstructorUser):
    query = select(TeachingClass).order_by(TeachingClass.updated_at.desc())
    if not can_bypass_teaching_ownership(current_user):
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
    machine_rows = _class_machine_rows(session, enrollment_ids)
    vmids = [row.vmid for row in machine_rows if row.vmid is not None]
    resources = proxmox_service.list_all_resources() if vmids else []
    return ClassResourceUsageResponse(
        collected_at=get_datetime_utc(),
        items=class_status_service.class_resource_usage_items(vmids, resources),
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
    class_lifecycle_service.validate_schedule(item)
    item.updated_at = get_datetime_utc()
    session.add(item)
    session.commit()
    class_lifecycle_service.generate_weeks(session, item, preserve=True)
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
    class_lifecycle_service.validate_schedule_span(item.start_date, body.end_date)
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
    class_lifecycle_service.generate_weeks(session, item, preserve=True)
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


@router.put("/{class_id}/instructor-machine")
def set_instructor_machine(
    class_id: uuid.UUID,
    body: InstructorMachineIn,
    session: SessionDep,
    current_user: InstructorUser,
):
    """讓班級擁有者也拿一套和學生相同的機器。

    做法是替擁有者加一列 status=instructor 的成員：容量預留、建機、機器
    對應、拓樸與到期回收都照學生的流程走，不必另開一條路。和名單一樣，
    送出建機後就鎖定。
    """
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.studentsLocked"))
    row = session.exec(
        select(TeachingClassStudent).where(
            TeachingClassStudent.class_id == class_id,
            TeachingClassStudent.user_id == item.owner_id,
        )
    ).first()
    if body.enabled and row is None:
        session.add(
            TeachingClassStudent(
                class_id=class_id,
                user_id=item.owner_id,
                status=INSTRUCTOR_ENROLLMENT_STATUS,
            )
        )
    elif not body.enabled and row is not None:
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
    try:
        raw = await file.read(MAX_STUDENT_CSV_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > MAX_STUDENT_CSV_BYTES:
        raise BadRequestError(
            t(
                "teachingClasses.csvTooLarge",
                size=MAX_STUDENT_CSV_BYTES // (1024 * 1024),
            )
        )
    content = None
    # UTF-8 要先試：Excel「CSV UTF-8」開頭的 BOM 加一個英文字母剛好是兩個
    # 合法的 Big5 字，先用 cp950 解不會報錯，第一格就被解成亂碼（標題列
    # 沒被跳過、或第一位學生被默默漏掉）。真正的 Big5 位元組幾乎一定不是
    # 合法的 UTF-8，所以舊的 cp950 檔案仍會落到第二順位解出來。
    for encoding in ("utf-8-sig", "cp950"):
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
        value = row[0].strip().lstrip("\ufeff").strip()
        if index == 0 and value.lower() in {"email", "學號", "帳號"}:
            continue
        if value:
            emails.append(value if "@" in value else f"{value}@ntub.edu.tw")
    return add_students(class_id, StudentAdd(emails=emails), session, current_user)


@router.post("/{class_id}/generate-weeks")
def generate_weeks(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    item = _get_class(session, current_user, class_id)
    class_lifecycle_service.generate_weeks(session, item, preserve=True)
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
    class_provision_service.select_course(
        session,
        item=item,
        course_version_id=body.course_version_id,
        current_user=current_user,
    )
    return _serialize(session, item)


@router.put("/{class_id}/weeks")
def replace_weeks(
    class_id: uuid.UUID,
    body: list[WeekIn],
    session: SessionDep,
    current_user: InstructorUser,
):
    """逐週差異更新。

    以前是整批刪掉再重建：週次的 id 每存一次就換一組（掛在週次上的檢查
    session 會跟著斷），檔案列也跟著重建，磁碟上的舊檔沒人清。現在改成
    就地更新，被移出清單的檔案走和單檔刪除同一段清檔邏輯。
    """
    item = _get_class(session, current_user, class_id)
    if item.status == TeachingClassStatus.archived:
        raise BadRequestError(t("teachingClasses.archivedCannotEditWeeklyContent"))
    weeks = list(
        session.exec(
            select(TeachingClassWeek).where(TeachingClassWeek.class_id == class_id)
        ).all()
    )
    if {row.session_date for row in weeks} != {row.session_date for row in body}:
        raise BadRequestError(t("teachingClasses.weekDatesMustMatchSchedule"))

    targets = {row.target_node_key for row in body if row.target_node_key is not None}
    node_keys = (
        set(
            session.exec(
                select(TeachingClassMachineNode.node_key).where(
                    TeachingClassMachineNode.class_id == class_id
                )
            ).all()
        )
        if targets
        else set()
    )
    unknown_targets = targets - node_keys
    if unknown_targets:
        raise BadRequestError(
            t(
                "teachingClasses.weekTargetNodeUnknown",
                keys=", ".join(sorted(unknown_targets)),
            )
        )

    weeks_by_number = {row.week_number: row for row in weeks}
    weeks_by_date = {row.session_date: row for row in weeks}
    # 班級底下所有的教材檔：送進來的 id 必須落在這個範圍內，
    # 才不會把別的班級的檔案接管過來
    files_by_id: dict[uuid.UUID, TeachingClassTaskFile] = {}
    if weeks:
        files_by_id = {
            row.id: row
            for row in session.exec(
                select(TeachingClassTaskFile).where(
                    col(TeachingClassTaskFile.week_id).in_(
                        [week.id for week in weeks]
                    )
                )
            ).all()
        }

    kept_week_ids: set[uuid.UUID] = set()
    kept_file_ids: set[uuid.UUID] = set()
    for row in body:
        week = weeks_by_number.get(row.week_number) or weeks_by_date.get(
            row.session_date
        )
        if week is None:
            week = TeachingClassWeek(class_id=class_id, week_number=row.week_number)
            session.add(week)
        for key, value in row.model_dump(exclude={"files"}).items():
            setattr(week, key, value)
        session.add(week)
        session.flush()
        kept_week_ids.add(week.id)
        for file in row.files:
            task_file = files_by_id.get(file.id)
            if task_file is None:
                raise NotFoundError(t("teachingClasses.taskFileNotFound"))
            task_file.week_id = week.id
            task_file.target_path = file.target_path
            session.add(task_file)
            kept_file_ids.add(task_file.id)

    removed_storage_keys = [
        row.storage_key
        for row in files_by_id.values()
        if row.id not in kept_file_ids
    ]
    for row in files_by_id.values():
        if row.id not in kept_file_ids:
            session.delete(row)
    for week in weeks:
        if week.id not in kept_week_ids:
            session.delete(week)
    session.commit()
    for storage_key in removed_storage_keys:
        class_lifecycle_service.remove_task_file_blob(storage_key)
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

    filename = upload_store.sanitize_upload_filename(
        file.filename,
        default="task-file",
        invalid_message=t("teachingClasses.invalidFileName"),
        too_long_message=t("teachingClasses.taskFileNameTooLong"),
    )
    file_id, storage_key, _written = await upload_store.save_upload(
        file,
        root=weekly_task_service.TASK_FILE_ROOT,
        suffix=".task",
        max_bytes=MAX_TASK_FILE_BYTES,
        too_large_message=t("teachingClasses.taskFileTooLarge"),
    )

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
    class_lifecycle_service.remove_task_file_blob(storage_key)
    return _serialize(session, item)


@router.get("/{class_id}/capacity-preview")
def capacity_preview(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    if item.status != TeachingClassStatus.planning:
        raise BadRequestError(t("teachingClasses.onlyPlanningCanRecheckCapacity"))
    nodes = _class_nodes(session, class_id)
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
    class_provision_service.provision_class(
        session,
        item=item,
        nodes=_class_nodes(session, class_id),
        students=_students(session, class_id),
    )
    return _serialize(session, item)


@router.post("/{class_id}/retry-failed")
def retry_failed_class(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    class_provision_service.retry_failed_class(session, item=item)
    return _serialize(session, item)


@router.post("/{class_id}/reset-failed")
def reset_failed_class(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
):
    item = _get_class(session, current_user, class_id)
    class_provision_service.reset_failed_class(session, item=item)
    return _serialize(session, item)


@router.get("/{class_id}/provision-status")
def provision_status(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    """純讀取的建機進度。

    這支以前會順手把建機結果寫回班級、重算狀態、並在全部完成時套用
    網路拓樸——而前端每三秒打一次。一個 GET 送出幾百次 PVE 呼叫，
    而且任何人重整頁面都會觸發。改寫的部分搬到 ``/reconcile``。
    """
    return _serialize(session, _get_class(session, current_user, class_id))


@router.post("/{class_id}/reconcile")
def reconcile_class(
    class_id: uuid.UUID, session: SessionDep, current_user: InstructorUser
):
    """把建機工作的結果寫回班級：學生機器對應、班級狀態、必要時套用拓樸。

    建機 worker 每完成一個節點也會重算一次狀態；這支是給老師開著班級頁
    時補齊「哪位學生拿到哪台機器」用的，前端不需要每次輪詢都呼叫。
    """
    item = _get_class(session, current_user, class_id)
    class_provision_service.reconcile_class(session, item=item)
    return _serialize(session, item)
