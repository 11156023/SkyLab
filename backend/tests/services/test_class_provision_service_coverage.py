"""Coverage tests for app.services.teaching.class_provision_service."""

import uuid
from datetime import date, time, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.core import authorizers
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    CourseEnvironment,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassWeek,
    VMTemplate,
)
from app.models.base import get_datetime_utc
from app.services.teaching import class_provision_service as svc
from app.services.teaching import class_status_service


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassMachineNode.__table__,  # type: ignore[arg-type]
            TeachingClassStudent.__table__,  # type: ignore[arg-type]
            TeachingClassStudentMachine.__table__,  # type: ignore[arg-type]
            TeachingClassWeek.__table__,  # type: ignore[arg-type]
            BatchProvisionJob.__table__,  # type: ignore[arg-type]
            BatchProvisionTask.__table__,  # type: ignore[arg-type]
            Resource.__table__,  # type: ignore[arg-type]
            CourseEnvironment.__table__,  # type: ignore[arg-type]
            CourseEnvironmentVersion.__table__,  # type: ignore[arg-type]
            CourseEnvironmentNode.__table__,  # type: ignore[arg-type]
            VMTemplate.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as s:
        yield s


@pytest.fixture
def capacity(monkeypatch):
    calls: dict[str, list] = {"reserve": [], "release": []}
    monkeypatch.setattr(
        svc.class_capacity_service,
        "reserve",
        lambda _s, **kw: calls["reserve"].append(kw),
    )
    monkeypatch.setattr(
        svc.class_capacity_service,
        "release",
        lambda _s, *, class_id: calls["release"].append(class_id) or 0,
    )
    return calls


@pytest.fixture
def submitted(monkeypatch):
    calls: list[dict] = []

    def fake_submit(**kwargs):
        calls.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(
        svc.batch_provision_service, "submit_batch_job_for_users", fake_submit
    )
    return calls


def _class(
    session: Session,
    status: TeachingClassStatus = TeachingClassStatus.planning,
    *,
    locked: bool = False,
    course_version_id: uuid.UUID | None = None,
) -> TeachingClass:
    item = TeachingClass(
        owner_id=uuid.uuid4(),
        name="Linux",
        code=f"CLS_{uuid.uuid4().hex[:6]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 12, 31),
        weekday=2,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=status,
        course_version_id=course_version_id,
        locked_at=get_datetime_utc() if locked else None,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _node(
    session: Session,
    item: TeachingClass,
    *,
    job_id: uuid.UUID | None = None,
    sort_order: int = 0,
    source_type: str = "custom",
    source_template_id: uuid.UUID | None = None,
    resource_type: str = "lxc",
) -> TeachingClassMachineNode:
    node = TeachingClassMachineNode(
        class_id=item.id,
        node_key=f"n{sort_order}",
        source_type=source_type,
        source_template_id=source_template_id,
        name=f"node{sort_order}",
        role="server",
        resource_type=resource_type,
        cpu=1,
        memory_mb=1024,
        disk_gb=8,
        sort_order=sort_order,
        batch_job_id=job_id,
    )
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


def _student(session: Session, item: TeachingClass, user_id=None):
    row = TeachingClassStudent(class_id=item.id, user_id=user_id or uuid.uuid4())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _job(session: Session, item: TeachingClass, *, status, total=0):
    job = BatchProvisionJob(
        teaching_class_id=item.id,
        resource_type="lxc",
        hostname_prefix="x",
        template_params={},
        status=status,
        total=total,
        created_at=get_datetime_utc(),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _task(session: Session, job, user_id, *, status, started_at=None, index=0):
    task = BatchProvisionTask(
        job_id=job.id,
        user_id=user_id,
        member_index=index,
        status=status,
        started_at=started_at,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# ---------------------------------------------------------------- params


def test_node_source_params_variants():
    tpl = uuid.uuid4()
    node = TeachingClassMachineNode(
        class_id=uuid.uuid4(),
        node_key="a",
        source_type="template",
        source_template_id=tpl,
        name="a",
        role="r",
        resource_type="qemu",
        cpu=1,
        memory_mb=512,
        disk_gb=8,
    )
    assert svc.node_source_params(node) == {"vm_template_id": str(tpl)}

    node.source_type = "custom"
    node.resource_type = "LXC"
    node.custom_image_ref = "local:vztmpl/debian.tar.zst"
    node.custom_unprivileged = False
    assert svc.node_source_params(node) == {
        "ostemplate": "local:vztmpl/debian.tar.zst",
        "storage": "local-lvm",
        "unprivileged": False,
    }

    node.resource_type = "qemu"
    node.custom_image_ref = None
    node.custom_username = None
    assert svc.node_source_params(node) == {
        "template_id": 0,
        "storage": "local-lvm",
        "username": "student",
    }


# ------------------------------------------------- recover_existing_task


def _resource(session: Session, *, vmid: int, job_id, user_id):
    res = Resource(
        vmid=vmid,
        user_id=user_id,
        batch_job_id=job_id,
        environment_type="teaching_class",
        created_at=get_datetime_utc(),
    )
    session.add(res)
    session.commit()
    return res


def test_recover_returns_false_without_resource(session):
    item = _class(session)
    node = _node(session, item)
    job = _job(session, item, status=BatchProvisionJobStatus.failed)
    task = _task(session, job, uuid.uuid4(), status=BatchProvisionTaskStatus.failed)
    assert not svc.recover_existing_task_resource(
        session=session, item=item, node=node, task=task
    )


def test_recover_deletes_orphan_when_vm_gone(session, monkeypatch):
    item = _class(session)
    node = _node(session, item)
    job = _job(session, item, status=BatchProvisionJobStatus.failed)
    user = uuid.uuid4()
    task = _task(session, job, user, status=BatchProvisionTaskStatus.failed)
    _resource(session, vmid=501, job_id=job.id, user_id=user)

    def missing(vmid):
        raise NotFoundError("gone")

    deleted: list[dict] = []
    monkeypatch.setattr(svc.proxmox_service, "find_resource", missing)
    monkeypatch.setattr(
        svc.resource_service,
        "delete_orphan_db_record",
        lambda **kw: deleted.append(kw),
    )
    assert not svc.recover_existing_task_resource(
        session=session, item=item, node=node, task=task
    )
    assert deleted == [{"session": session, "vmid": 501, "user_id": item.owner_id}]


def test_recover_raises_when_pve_check_fails(session, monkeypatch):
    item = _class(session)
    node = _node(session, item)
    job = _job(session, item, status=BatchProvisionJobStatus.failed)
    user = uuid.uuid4()
    task = _task(session, job, user, status=BatchProvisionTaskStatus.failed)
    _resource(session, vmid=502, job_id=job.id, user_id=user)

    def boom(vmid):
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc.proxmox_service, "find_resource", boom)
    with pytest.raises(BadRequestError):
        svc.recover_existing_task_resource(
            session=session, item=item, node=node, task=task
        )


def test_recover_requires_enrollment(session, monkeypatch):
    item = _class(session)
    node = _node(session, item)
    job = _job(session, item, status=BatchProvisionJobStatus.failed)
    user = uuid.uuid4()
    task = _task(session, job, user, status=BatchProvisionTaskStatus.failed)
    _resource(session, vmid=503, job_id=job.id, user_id=user)
    monkeypatch.setattr(svc.proxmox_service, "find_resource", lambda vmid: {})
    with pytest.raises(BadRequestError):
        svc.recover_existing_task_resource(
            session=session, item=item, node=node, task=task
        )


# ------------------------------------------------------- retry_failed_class


def test_retry_rejects_other_statuses(session):
    item = _class(session, TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        svc.retry_failed_class(session, item=item)


def test_retry_resubmits_stale_and_terminal_tasks(session, monkeypatch, submitted):
    item = _class(session, TeachingClassStatus.partial_failed)
    _node(session, item, sort_order=0)  # no job -> skipped
    failed_job = _job(session, item, status=BatchProvisionJobStatus.failed, total=3)
    node = _node(session, item, job_id=failed_job.id, sort_order=1)
    done_user, pending_user, failed_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _task(session, failed_job, done_user, status=BatchProvisionTaskStatus.completed)
    pending = _task(
        session,
        failed_job,
        pending_user,
        status=BatchProvisionTaskStatus.pending,
        index=1,
    )
    _task(
        session,
        failed_job,
        failed_user,
        status=BatchProvisionTaskStatus.failed,
        index=2,
    )

    running_job = _job(session, item, status=BatchProvisionJobStatus.running, total=2)
    node2 = _node(session, item, job_id=running_job.id, sort_order=2)
    stale_user = uuid.uuid4()
    stale = _task(
        session,
        running_job,
        stale_user,
        status=BatchProvisionTaskStatus.running,
        started_at=get_datetime_utc() - timedelta(hours=2),
    )
    _task(
        session,
        running_job,
        uuid.uuid4(),
        status=BatchProvisionTaskStatus.completed,
        index=1,
    )

    zero_job = _job(session, item, status=BatchProvisionJobStatus.running, total=1)
    _node(session, item, job_id=zero_job.id, sort_order=3)
    _task(
        session,
        zero_job,
        uuid.uuid4(),
        status=BatchProvisionTaskStatus.running,
        started_at=get_datetime_utc() - timedelta(hours=3),
    )

    monkeypatch.setattr(svc, "recover_existing_task_resource", lambda **kw: False)
    svc.retry_failed_class(session, item=item)

    assert item.status == TeachingClassStatus.pending_review
    session.refresh(pending)
    assert pending.status == BatchProvisionTaskStatus.failed
    assert pending.error == "Incomplete task superseded by class retry"
    session.refresh(stale)
    assert stale.error == "Stale provisioning task superseded by class retry"
    session.refresh(running_job)
    assert running_job.status == BatchProvisionJobStatus.completed
    session.refresh(zero_job)
    assert zero_job.status == BatchProvisionJobStatus.failed
    assert len(submitted) == 3
    assert set(submitted[0]["member_user_ids"]) == {pending_user, failed_user}
    assert submitted[1]["member_user_ids"] == [stale_user]
    session.refresh(node)
    session.refresh(node2)
    assert node.batch_job_id != failed_job.id
    assert node2.batch_job_id != running_job.id


def test_retry_recovered_tasks_activate_class(session, monkeypatch):
    item = _class(session, TeachingClassStatus.provisioning)
    job = _job(session, item, status=BatchProvisionJobStatus.running, total=1)
    _node(session, item, job_id=job.id)
    task = _task(session, job, uuid.uuid4(), status=BatchProvisionTaskStatus.failed)

    def recover(**kw):
        kw["task"].status = BatchProvisionTaskStatus.completed
        return True

    monkeypatch.setattr(svc, "recover_existing_task_resource", recover)
    monkeypatch.setattr(class_status_service, "jobs_all_ready", lambda n, j: True)
    activated: list = []
    monkeypatch.setattr(
        class_status_service,
        "activate_class",
        lambda s, i, jobs: activated.append(jobs) or [],
    )
    svc.retry_failed_class(session, item=item)
    session.refresh(job)
    assert job.status == BatchProvisionJobStatus.completed
    assert task.status == BatchProvisionTaskStatus.completed
    assert [j.id for j in activated[0]] == [job.id]


def test_retry_recovered_topology_failure_raises(session, monkeypatch):
    item = _class(session, TeachingClassStatus.partial_failed)
    job = _job(session, item, status=BatchProvisionJobStatus.completed, total=1)
    _node(session, item, job_id=job.id)
    _task(session, job, uuid.uuid4(), status=BatchProvisionTaskStatus.completed)
    monkeypatch.setattr(class_status_service, "jobs_all_ready", lambda n, j: True)
    monkeypatch.setattr(
        class_status_service, "activate_class", lambda s, i, jobs: ["edge a-b"]
    )
    with pytest.raises(BadRequestError):
        svc.retry_failed_class(session, item=item)


def test_retry_partial_not_ready_goes_provisioning(session, monkeypatch):
    item = _class(session, TeachingClassStatus.partial_failed)
    job = _job(session, item, status=BatchProvisionJobStatus.running, total=2)
    _node(session, item, job_id=job.id)
    _task(session, job, uuid.uuid4(), status=BatchProvisionTaskStatus.running)
    monkeypatch.setattr(class_status_service, "jobs_all_ready", lambda n, j: False)
    svc.retry_failed_class(session, item=item)
    assert item.status == TeachingClassStatus.provisioning


def test_retry_provisioning_without_work_raises(session):
    item = _class(session, TeachingClassStatus.provisioning)
    with pytest.raises(BadRequestError):
        svc.retry_failed_class(session, item=item)


# ----------------------------------------------------------- select_course


def _environment(session, *, usage_scope="course"):
    env = CourseEnvironment(
        owner_id=uuid.uuid4(),
        name="env",
        usage_scope=usage_scope,
        created_at=get_datetime_utc(),
        updated_at=get_datetime_utc(),
    )
    session.add(env)
    session.commit()
    session.refresh(env)
    return env


def _version(session, env_id, *, status=CourseEnvironmentVersionStatus.published):
    version = CourseEnvironmentVersion(
        environment_id=env_id,
        version=1,
        status=status,
        created_at=get_datetime_utc(),
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


@pytest.fixture
def allow_teaching(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        authorizers,
        "require_teaching_access",
        lambda user, owner_id: calls.append(owner_id),
    )
    return calls


def test_select_course_rejects_locked_class(session):
    item = _class(session, locked=True)
    with pytest.raises(BadRequestError):
        svc.select_course(
            session, item=item, course_version_id=uuid.uuid4(), current_user=None
        )


def test_select_course_requires_published_version(session):
    item = _class(session)
    env = _environment(session)
    draft = _version(session, env.id, status=CourseEnvironmentVersionStatus.draft)
    for version_id in (uuid.uuid4(), draft.id):
        with pytest.raises(BadRequestError):
            svc.select_course(
                session, item=item, course_version_id=version_id, current_user=None
            )


def test_select_course_requires_environment(session):
    item = _class(session)
    version = _version(session, uuid.uuid4())
    with pytest.raises(NotFoundError):
        svc.select_course(
            session, item=item, course_version_id=version.id, current_user=None
        )


def test_select_course_rejects_practice_only_environment(session):
    item = _class(session)
    env = _environment(session, usage_scope="quick_practice")
    version = _version(session, env.id)
    with pytest.raises(BadRequestError):
        svc.select_course(
            session, item=item, course_version_id=version.id, current_user=None
        )


def test_select_course_requires_machines(session, allow_teaching):
    item = _class(session)
    env = _environment(session)
    version = _version(session, env.id)
    with pytest.raises(BadRequestError):
        svc.select_course(
            session, item=item, course_version_id=version.id, current_user=None
        )
    assert allow_teaching == [env.owner_id]


def test_select_course_rebuilds_nodes_and_weeks(session, allow_teaching, monkeypatch):
    item = _class(session)
    _node(session, item, sort_order=5)
    env = _environment(session, usage_scope="both")
    version = _version(session, env.id)
    session.add(
        CourseEnvironmentNode(
            version_id=version.id,
            node_key="web",
            source_type="custom",
            custom_image_ref="img",
            name="web",
            role="server",
            resource_type="lxc",
            cpu=2,
            memory_mb=2048,
            disk_gb=10,
            sort_order=0,
        )
    )
    keep = TeachingClassWeek(
        class_id=item.id,
        week_number=1,
        session_date=date(2026, 9, 2),
        target_node_key="web",
    )
    drop = TeachingClassWeek(
        class_id=item.id,
        week_number=2,
        session_date=date(2026, 9, 9),
        target_node_key="db",
    )
    session.add(keep)
    session.add(drop)
    session.commit()
    monkeypatch.setattr(
        svc.provisioning_service, "clone_source_disk_gb", lambda s, node: 20
    )
    svc.select_course(
        session, item=item, course_version_id=version.id, current_user=None
    )
    nodes = session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.class_id == item.id
        )
    ).all()
    assert [(n.node_key, n.disk_gb) for n in nodes] == [("web", 20)]
    session.refresh(keep)
    session.refresh(drop)
    assert keep.target_node_key == "web"
    assert drop.target_node_key is None
    assert item.course_version_id == version.id


# ----------------------------------------------------------- provision_class


def test_provision_class_guards(session):
    item = _class(session)
    node = _node(session, item)
    student = _student(session, item)
    with pytest.raises(BadRequestError):
        svc.provision_class(session, item=item, nodes=[], students=[student])
    locked = _class(session, locked=True, course_version_id=uuid.uuid4())
    with pytest.raises(BadRequestError):
        svc.provision_class(session, item=locked, nodes=[node], students=[student])
    with pytest.raises(BadRequestError):
        svc.provision_class(session, item=item, nodes=[node], students=[student])


def test_provision_class_submits_missing_jobs(session, capacity, submitted):
    item = _class(session, course_version_id=uuid.uuid4())
    existing = uuid.uuid4()
    done = _node(session, item, sort_order=0, job_id=existing)
    todo = _node(session, item, sort_order=1)
    student = _student(session, item)
    svc.provision_class(session, item=item, nodes=[done, todo], students=[student])
    assert item.status == TeachingClassStatus.pending_review
    assert item.locked_at is not None
    assert done.batch_job_id == existing
    assert todo.batch_job_id is not None
    assert len(submitted) == 1
    assert capacity["reserve"][0]["class_id"] == item.id


# ---------------------------------------------------------- reset / reconcile


def test_reset_failed_class_requires_partial_failed(session):
    item = _class(session, TeachingClassStatus.active)
    with pytest.raises(BadRequestError):
        svc.reset_failed_class(session, item=item)


def test_reconcile_skips_archived(session, monkeypatch):
    item = _class(session, TeachingClassStatus.archived)
    called: list = []
    monkeypatch.setattr(
        class_status_service, "recompute", lambda **kw: called.append(kw)
    )
    svc.reconcile_class(session, item=item)
    assert called == []


def test_reconcile_writes_mappings_and_recomputes(session, monkeypatch):
    item = _class(session, TeachingClassStatus.provisioning)
    _node(session, item, sort_order=0)  # no job
    _node(session, item, sort_order=1, job_id=uuid.uuid4())  # job missing
    job = _job(session, item, status=BatchProvisionJobStatus.completed, total=2)
    node = _node(session, item, sort_order=2, job_id=job.id)
    enrollment = _student(session, item)
    task = _task(
        session, job, enrollment.user_id, status=BatchProvisionTaskStatus.completed
    )
    _task(
        session,
        job,
        uuid.uuid4(),
        status=BatchProvisionTaskStatus.completed,
        index=1,
    )
    called: list = []
    monkeypatch.setattr(
        class_status_service, "recompute", lambda **kw: called.append(kw)
    )
    svc.reconcile_class(session, item=item)
    assert called == [{"session": session, "class_id": item.id}]
    mapping = session.exec(select(TeachingClassStudentMachine)).one()
    assert (mapping.machine_node_id, mapping.batch_task_id) == (node.id, task.id)
