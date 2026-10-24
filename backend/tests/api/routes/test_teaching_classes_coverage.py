"""Coverage for app.api.routes.teaching_classes: serializers and every route branch."""

import io
import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import teaching_classes as routes
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
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassTaskFile,
    TeachingClassWeek,
    User,
    UserRole,
    VMTemplate,
    VMTemplateStatus,
)
from app.models.base import get_datetime_utc
from app.schemas.teaching_class import (
    ClassArchive,
    ClassCreate,
    ClassExtend,
    ClassPatch,
    CourseSelect,
    MachineNodeIn,
    StudentAdd,
    WeekIn,
)
from app.services.user import roster_csv
from tests.utils.class_machines import add_student_machine

TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")
ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _stub_services(monkeypatch, tmp_path):
    calls: dict[str, list] = {}

    def recorder(name, result=None):
        def _fn(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return result

        return _fn

    monkeypatch.setattr(
        routes.class_capacity_service,
        "preview",
        recorder("preview", {"ok": True}),
    )
    lifecycle = routes.class_lifecycle_service
    monkeypatch.setattr(lifecycle, "validate_schedule", recorder("validate_schedule"))
    monkeypatch.setattr(
        lifecycle, "validate_schedule_span", recorder("validate_schedule_span")
    )
    monkeypatch.setattr(lifecycle, "generate_weeks", recorder("generate_weeks"))
    monkeypatch.setattr(
        lifecycle, "clear_schedule_windows", recorder("clear_schedule_windows")
    )
    monkeypatch.setattr(
        lifecycle, "queue_reclaim", recorder("queue_reclaim", {"queued": True})
    )
    monkeypatch.setattr(
        lifecycle, "archive_and_reclaim", recorder("archive_and_reclaim", {"r": 1})
    )
    monkeypatch.setattr(
        lifecycle, "remove_task_file_blob", recorder("remove_task_file_blob")
    )
    monkeypatch.setattr(
        routes.course_service, "ensure_class_path", recorder("ensure_class_path")
    )
    provision = routes.class_provision_service
    for name in (
        "select_course",
        "provision_class",
        "retry_failed_class",
        "reset_failed_class",
        "reconcile_class",
    ):
        monkeypatch.setattr(provision, name, recorder(name))
    monkeypatch.setattr(routes.weekly_task_service, "TASK_FILE_ROOT", tmp_path)
    return calls


def _class(db: Session, *, status=TeachingClassStatus.planning, **overrides):
    fields = {
        "owner_id": TEACHER.id,
        "name": "Linux",
        "code": f"cls-{uuid.uuid4().hex[:8]}",
        "term": "115-1",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 15),
        "weekday": 1,
        "start_time": time(13, 10),
        "end_time": time(16, 0),
        "status": status,
    }
    fields.update(overrides)
    item = TeachingClass(**fields)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _node(db: Session, item: TeachingClass, key: str, **overrides):
    fields = {
        "class_id": item.id,
        "node_key": key,
        "source_type": "custom",
        "custom_image_ref": "local:vztmpl/debian.tar.zst",
        "name": key,
        "role": "target",
        "resource_type": "lxc",
        "cpu": 1,
        "memory_mb": 512,
        "disk_gb": 8,
        "sort_order": 0,
    }
    fields.update(overrides)
    node = TeachingClassMachineNode(**fields)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


def _user(db: Session, email: str, role=UserRole.student) -> User:
    user = User(email=email, hashed_password="x", role=role, full_name=email)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _week(db: Session, item, number: int, day: date) -> TeachingClassWeek:
    week = TeachingClassWeek(class_id=item.id, week_number=number, session_date=day)
    db.add(week)
    db.commit()
    db.refresh(week)
    return week


def _file(db: Session, week, name: str = "lab.txt") -> TeachingClassTaskFile:
    row = TeachingClassTaskFile(
        week_id=week.id, filename=name, storage_key=f"{uuid.uuid4().hex}.task"
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _environment(db: Session, item: TeachingClass) -> CourseEnvironmentVersion:
    env = CourseEnvironment(owner_id=TEACHER.id, name="Web Lab")
    db.add(env)
    db.flush()
    version = CourseEnvironmentVersion(environment_id=env.id, version=1)
    db.add(version)
    db.flush()
    db.add(
        CourseEnvironmentNode(
            version_id=version.id,
            node_key="web",
            source_type="custom",
            custom_image_ref="local:vztmpl/debian.tar.zst",
            name="web",
            role="target",
            resource_type="lxc",
            cpu=1,
            memory_mb=512,
            disk_gb=8,
            position_x=10.0,
            position_y=20.0,
        )
    )
    db.add(
        CourseEnvironmentEdge(
            version_id=version.id, source_node_key="web", target_node_key="db"
        )
    )
    db.add(CourseEnvironmentPublication(version_id=version.id, node_key="web", port=80))
    item.course_version_id = version.id
    db.add(item)
    db.commit()
    return version


# --- _serialize / _serialize_list -------------------------------------------


def test_serialize_full_class(db: Session) -> None:
    item = _class(db)
    version = _environment(db, item)
    template = VMTemplate(
        pve_vmid=9001, name="Ubuntu", node="pve", status=VMTemplateStatus.ready
    )
    db.add(template)
    db.commit()
    job = BatchProvisionJob(
        teaching_class_id=item.id,
        resource_type="lxc",
        hostname_prefix="t",
        template_params={},
        created_at=get_datetime_utc(),
    )
    db.add(job)
    db.commit()
    node = _node(
        db,
        item,
        "web",
        source_type="template",
        source_template_id=template.id,
        batch_job_id=job.id,
    )
    _node(db, item, "orphan", sort_order=1, batch_job_id=uuid.uuid4())
    week = _week(db, item, 1, date(2026, 9, 1))
    _file(db, week)
    student = _user(db, "stu@x.edu")
    enrollment = TeachingClassStudent(class_id=item.id, user_id=student.id)
    instructor = TeachingClassStudent(
        class_id=item.id, user_id=TEACHER.id, status=INSTRUCTOR_ENROLLMENT_STATUS
    )
    db.add(enrollment)
    db.add(instructor)
    db.commit()
    add_student_machine(db, enrollment=enrollment, node=node, vmid=7001)
    db.add(
        ClassCapacityReservation(
            class_id=item.id,
            course_version_id=version.id,
            student_count=1,
            machine_count=1,
            cpu_cores=1,
            memory_mb=512,
            disk_gb=8,
            ip_count=1,
            network_count=1,
        )
    )
    db.commit()

    data = routes._serialize(db, item)

    assert data["member_count"] == 1
    assert data["students"][0]["email"] == "stu@x.edu"
    # 熱力圖／整班開關機／觀看畫面都靠 vmid 與 status 對機器，不能被 model_dump 漏掉
    machine = data["students"][0]["machines"][0]
    assert machine["vmid"] == 7001
    assert machine["status"] == "completed"
    assert machine["error"] is None
    assert data["instructor_machine"]["email"] is None
    assert data["ready_machines"] == 1
    assert data["total_machines"] == 4
    assert [job_row["id"] for job_row in data["provision_jobs"]] == [job.id]
    assert data["course_environment"]["name"] == "Web Lab"
    assert data["node_positions"] == {"web": {"x": 10.0, "y": 20.0}}
    assert len(data["topology_edges"]) == 1
    assert len(data["publications"]) == 1
    assert data["capacity_reservation"]["student_count"] == 1
    assert data["machine_nodes"][0]["template_name"] == "Ubuntu"
    assert data["weeks"][0]["files"][0]["filename"] == "lab.txt"


def test_serialize_list_empty_and_with_environment(db: Session) -> None:
    assert routes._serialize_list(db, []) == []
    item = _class(db)
    _environment(db, item)
    plain = _class(db)
    rows = routes._serialize_list(db, [item, plain])
    assert rows[0]["course_environment"]["name"] == "Web Lab"
    assert rows[1]["course_environment"] is None


# --- create / list / get / update ------------------------------------------


def test_create_class(db: Session, _stub_services) -> None:
    body = ClassCreate(
        name="Net",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 15),
        weekday=1,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )
    data = routes.create_class(body, db, TEACHER)
    assert data["name"] == "Net"
    assert data["code"].startswith("cls-")
    assert data["owner_id"] == TEACHER.id
    assert "ensure_class_path" in _stub_services
    assert "generate_weeks" in _stub_services


def test_list_classes_scoped_to_owner_unless_bypass(db: Session) -> None:
    _class(db)
    _class(db, owner_id=uuid.uuid4())
    assert len(routes.list_classes(db, TEACHER)) == 1
    assert len(routes.list_classes(db, ADMIN)) == 2


def test_get_class_and_not_found(db: Session) -> None:
    item = _class(db)
    assert routes.get_class(item.id, db, TEACHER)["id"] == item.id
    with pytest.raises(NotFoundError):
        routes.get_class(uuid.uuid4(), db, TEACHER)


def test_update_class_locked_and_applied(db: Session) -> None:
    locked = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.update_class(locked.id, ClassPatch(name="x"), db, TEACHER)
    item = _class(db)
    data = routes.update_class(item.id, ClassPatch(name="Renamed"), db, TEACHER)
    assert data["name"] == "Renamed"


# --- extend / archive / reclaim --------------------------------------------


def test_extend_class_branches(db: Session, _stub_services) -> None:
    archived = _class(db, status=TeachingClassStatus.archived)
    with pytest.raises(BadRequestError):
        routes.extend_class(
            archived.id, ClassExtend(end_date=date(2027, 1, 1)), db, TEACHER
        )
    item = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.extend_class(item.id, ClassExtend(end_date=item.end_date), db, TEACHER)

    db.add(
        Resource(
            vmid=7100,
            user_id=uuid.uuid4(),
            teaching_class_id=item.id,
            allocation_scope="teaching_class",
            environment_type="teaching_class",
            created_at=get_datetime_utc(),
            expiry_notified_at=get_datetime_utc(),
            scheduled_deletion_at=get_datetime_utc(),
        )
    )
    db.commit()
    new_end = date(2026, 12, 31)
    data = routes.extend_class(item.id, ClassExtend(end_date=new_end), db, TEACHER)
    assert data["end_date"] == new_end
    resource = db.get(Resource, 7100)
    assert resource.expiry_date is not None
    assert resource.expiry_notified_at is None
    assert resource.scheduled_deletion_at is None
    assert "clear_schedule_windows" in _stub_services


def test_reclaim_requires_archived(db: Session) -> None:
    item = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.reclaim_class_resources(item.id, ClassArchive(), db, TEACHER)
    archived = _class(db, status=TeachingClassStatus.archived)
    assert routes.reclaim_class_resources(
        archived.id, ClassArchive(force=True), db, TEACHER
    ) == {"queued": True}


# --- students ---------------------------------------------------------------


def test_add_students_branches(db: Session) -> None:
    locked = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.add_students(locked.id, StudentAdd(emails=[]), db, TEACHER)

    item = _class(db)
    _user(db, "stu1@x.edu")
    _user(db, "tea@x.edu", role=UserRole.teacher)
    result = routes.add_students(
        item.id,
        StudentAdd(emails=[" STU1@x.edu ", "stu1@x.edu", "missing@x.edu", "tea@x.edu"]),
        db,
        TEACHER,
    )
    assert result["added"] == 1
    assert result["not_found"] == ["missing@x.edu"]
    assert result["invalid_role"] == ["tea@x.edu"]
    assert result["class"]["member_count"] == 1


def test_remove_student_branches(db: Session) -> None:
    locked = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.remove_student(locked.id, uuid.uuid4(), db, TEACHER)

    item = _class(db)
    other = _class(db)
    with pytest.raises(NotFoundError):
        routes.remove_student(item.id, uuid.uuid4(), db, TEACHER)
    foreign = TeachingClassStudent(class_id=other.id, user_id=uuid.uuid4())
    mine = TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4())
    db.add(foreign)
    db.add(mine)
    db.commit()
    with pytest.raises(NotFoundError):
        routes.remove_student(item.id, foreign.id, db, TEACHER)
    routes.remove_student(item.id, mine.id, db, TEACHER)
    assert db.get(TeachingClassStudent, mine.id) is None


def _upload(raw: bytes, name: str = "students.csv") -> UploadFile:
    return UploadFile(file=io.BytesIO(raw), filename=name)


async def test_import_students_locked_class(db: Session) -> None:
    item = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        await routes.import_students(item.id, db, TEACHER, _upload(b"a@x.edu\n"))


async def test_import_students_undecodable(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "_get_class",
        lambda *_a: SimpleNamespace(status=TeachingClassStatus.planning),
    )
    monkeypatch.setattr(roster_csv, "student_email_domain", lambda _session: None)
    with pytest.raises(BadRequestError):
        await routes.import_students(uuid.uuid4(), None, TEACHER, _upload(b"\x80"))


async def test_import_students_skips_blank_rows(monkeypatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(
        routes,
        "_get_class",
        lambda *_a: SimpleNamespace(status=TeachingClassStatus.planning),
    )
    monkeypatch.setattr(
        routes,
        "add_students",
        lambda _cid, body, _s, _u: captured.append(body.emails) or {},
    )
    monkeypatch.setattr(roster_csv, "student_email_domain", lambda _session: None)
    await routes.import_students(
        uuid.uuid4(), None, TEACHER, _upload(b"email\n\na@x.edu\n ,\n")
    )
    assert captured == [["a@x.edu"]]


# --- misc routes ------------------------------------------------------------


def test_generate_weeks_route(db: Session, _stub_services) -> None:
    item = _class(db)
    assert routes.generate_weeks(item.id, db, TEACHER)["id"] == item.id
    assert _stub_services["generate_weeks"]


def test_replace_machines_is_rejected(db: Session) -> None:
    item = _class(db)
    node = MachineNodeIn(
        node_key="a",
        name="a",
        role="r",
        resource_type="lxc",
        cpu=1,
        memory_mb=512,
        disk_gb=8,
    )
    with pytest.raises(BadRequestError):
        routes.replace_machines(item.id, [node], db, TEACHER)


def test_select_course(db: Session, _stub_services) -> None:
    item = _class(db)
    version_id = uuid.uuid4()
    routes.select_course(
        item.id, CourseSelect(course_version_id=version_id), db, TEACHER
    )
    (_args, kwargs) = _stub_services["select_course"][0]
    assert kwargs["course_version_id"] == version_id


def test_provision_related_routes(db: Session, _stub_services) -> None:
    item = _class(db)
    routes.provision_class(item.id, db, TEACHER)
    routes.retry_failed_class(item.id, db, TEACHER)
    routes.reset_failed_class(item.id, db, TEACHER)
    routes.reconcile_class(item.id, db, TEACHER)
    assert routes.provision_status(item.id, db, TEACHER)["id"] == item.id
    for name in (
        "provision_class",
        "retry_failed_class",
        "reset_failed_class",
        "reconcile_class",
    ):
        assert _stub_services[name]


def test_capacity_preview_route(db: Session, _stub_services) -> None:
    locked = _class(db, status=TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        routes.capacity_preview(locked.id, db, TEACHER)
    item = _class(db)
    assert routes.capacity_preview(item.id, db, TEACHER) == {"ok": True}
    assert _stub_services["preview"][-1][1]["check_cluster"] is True


# --- weeks ------------------------------------------------------------------


def test_replace_weeks_archived(db: Session) -> None:
    item = _class(db, status=TeachingClassStatus.archived)
    with pytest.raises(BadRequestError):
        routes.replace_weeks(item.id, [], db, TEACHER)


def test_replace_weeks_never_drops_a_week(db: Session) -> None:
    """送來的內容沒涵蓋到某一週時整筆拒絕，不會順手把那一週刪掉。"""
    item = _class(db)
    day = date(2026, 9, 1)
    first = _week(db, item, 1, day)
    second = _week(db, item, 2, day)
    with pytest.raises(BadRequestError):
        routes.replace_weeks(
            item.id, [WeekIn(week_number=1, session_date=day, title="t")], db, TEACHER
        )
    remaining = db.exec(
        select(TeachingClassWeek).where(TeachingClassWeek.class_id == item.id)
    ).all()
    assert sorted(row.id for row in remaining) == sorted([first.id, second.id])


def test_week_for_upload_branches(db: Session) -> None:
    archived = _class(db, status=TeachingClassStatus.archived)
    with pytest.raises(BadRequestError):
        routes._week_for_upload(db, TEACHER, archived.id, uuid.uuid4())
    item = _class(db, status=TeachingClassStatus.active)
    other = _class(db)
    with pytest.raises(NotFoundError):
        routes._week_for_upload(db, TEACHER, item.id, uuid.uuid4())
    foreign = _week(db, other, 1, date(2026, 9, 1))
    with pytest.raises(NotFoundError):
        routes._week_for_upload(db, TEACHER, item.id, foreign.id)
    week = _week(db, item, 1, date(2026, 9, 1))
    assert routes._week_for_upload(db, TEACHER, item.id, week.id) is item


async def test_upload_week_file_records_file(db: Session, tmp_path) -> None:
    item = _class(db, status=TeachingClassStatus.active)
    week = _week(db, item, 1, date(2026, 9, 1))
    data = await routes.upload_week_file(
        item.id, week.id, db, TEACHER, _upload(b"hello", name="lab.txt")
    )
    files = data["weeks"][0]["files"]
    assert [row["filename"] for row in files] == ["lab.txt"]
    assert (tmp_path / files[0]["storage_key"]).read_bytes() == b"hello"


def test_delete_week_file_branches(db: Session, _stub_services) -> None:
    archived = _class(db, status=TeachingClassStatus.archived)
    with pytest.raises(BadRequestError):
        routes.delete_week_file(archived.id, uuid.uuid4(), uuid.uuid4(), db, TEACHER)
    item = _class(db, status=TeachingClassStatus.active)
    week = _week(db, item, 1, date(2026, 9, 1))
    with pytest.raises(NotFoundError):
        routes.delete_week_file(item.id, week.id, uuid.uuid4(), db, TEACHER)
    task_file = _file(db, week)
    routes.delete_week_file(item.id, week.id, task_file.id, db, TEACHER)
    assert db.get(TeachingClassTaskFile, task_file.id) is None
    (args, _kwargs) = _stub_services["remove_task_file_blob"][-1]
    assert args == (task_file.storage_key,)
