import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from app.api.deps.proxmox import check_resource_ownership
from app.api.routes import course_admin, teaching_classes
from app.exceptions import BadRequestError, PermissionDeniedError
from app.models import (
    BatchProvisionJob,
    BatchProvisionTaskStatus,
    TeachingClass,
    TeachingClassStatus,
    UserRole,
)
from app.services.resource import quota_service, resource_service
from app.services.resource.access import require_resource_management
from app.services.scheduling.recurrence import compute_active_or_next_window
from app.services.scheduling.recurrence_scheduler import (
    _class_expired,
    _class_reclaim_retry_due,
    _class_schedule_enabled,
)
from app.services.teaching import (
    class_capacity_service,
    class_lifecycle_service,
    class_provision_service,
)
from app.services.vm import batch_provision_service


class _Result:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return self.value


class _Session:
    def __init__(self, *, reservation=None, teaching_class=None, job=None):
        self.reservation = reservation
        self.teaching_class = teaching_class
        self.job = job

    def exec(self, _statement):
        return _Result(self.reservation)

    def get(self, model, _key):
        if model is TeachingClass:
            return self.teaching_class
        if model is BatchProvisionJob:
            return self.job
        return None

    def add(self, _value):
        return None

    def commit(self):
        return None


def _user(role: UserRole, user_id: uuid.UUID | None = None):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        role=role,
        is_superuser=False,
    )


def test_class_machine_serialization_hides_internal_error_details():
    internal_error = "psycopg traceback at D:\\secret\\service.py password=hidden"
    machine = SimpleNamespace(
        model_dump=lambda: {
            "vmid": None,
            "status": "failed",
            "error": internal_error,
        }
    )

    result = teaching_classes._public_machine_dump(machine)

    assert result["error"] == teaching_classes.PUBLIC_PROVISION_ERROR
    assert internal_error not in result["error"]


def test_class_capacity_preview_hides_caught_exception_details(monkeypatch):
    internal_error = "database traceback password=hidden"
    monkeypatch.setattr(
        class_capacity_service.ip_management_service,
        "get_ip_stats",
        lambda _session: {"available": 10},
    )

    def fail_placement():
        raise RuntimeError(internal_error)

    monkeypatch.setattr(
        class_capacity_service.provisioning_service,
        "get_lxc_target_node",
        fail_placement,
    )
    result = class_capacity_service.preview(
        _Session(),
        nodes=[
            SimpleNamespace(
                network="lab-net",
                cpu=1,
                memory_mb=512,
                disk_gb=10,
                id=uuid.uuid4(),
                name="custom-lab",
                source_type="custom",
                resource_type="lxc",
                custom_image_ref=None,
            )
        ],
        students=[SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())],
        check_cluster=True,
    )

    assert len(result["issues"]) == 1
    assert "custom-lab" in result["issues"][0]
    assert all(internal_error not in issue for issue in result["issues"])


def test_class_member_cannot_manage_class_resource(monkeypatch):
    teacher_id = uuid.uuid4()
    student = _user(UserRole.student)
    teaching_class = SimpleNamespace(owner_id=teacher_id)
    resource = SimpleNamespace(
        user_id=student.id,
        teaching_class_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "app.services.resource.access.resource_repo.get_resource_by_vmid",
        lambda **_kwargs: resource,
    )

    with pytest.raises(PermissionDeniedError):
        require_resource_management(
            session=_Session(teaching_class=teaching_class),
            user=student,
            vmid=501,
        )


def test_class_teacher_can_manage_class_resource(monkeypatch):
    teacher = _user(UserRole.teacher)
    teaching_class = SimpleNamespace(owner_id=teacher.id)
    resource = SimpleNamespace(
        user_id=uuid.uuid4(),
        teaching_class_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        "app.services.resource.access.resource_repo.get_resource_by_vmid",
        lambda **_kwargs: resource,
    )

    require_resource_management(
        session=_Session(teaching_class=teaching_class),
        user=teacher,
        vmid=502,
    )


def test_archived_class_member_cannot_use_assigned_resource(monkeypatch):
    student = _user(UserRole.student)
    student.email = "student@example.com"
    resource = SimpleNamespace(
        user_id=student.id,
        teaching_class_id=uuid.uuid4(),
    )
    teaching_class = SimpleNamespace(
        owner_id=uuid.uuid4(),
        status=TeachingClassStatus.archived,
    )
    monkeypatch.setattr(
        "app.api.deps.proxmox.resource_repo.get_resource_by_vmid",
        lambda **_kwargs: resource,
    )

    with pytest.raises(PermissionDeniedError, match="not available"):
        check_resource_ownership(
            vmid=503,
            current_user=student,
            session=_Session(teaching_class=teaching_class),
        )


def test_orphaned_class_resource_never_becomes_student_owned(monkeypatch):
    student = _user(UserRole.student)
    student.email = "student@example.com"
    resource = SimpleNamespace(
        user_id=student.id,
        teaching_class_id=None,
        allocation_scope="teaching_class",
    )
    monkeypatch.setattr(
        "app.api.deps.proxmox.resource_repo.get_resource_by_vmid",
        lambda **_kwargs: resource,
    )

    with pytest.raises(PermissionDeniedError, match="administrator must reclaim"):
        check_resource_ownership(
            vmid=504,
            current_user=student,
            session=_Session(),
        )


def test_personal_quota_query_excludes_teaching_class_resources():
    class _CaptureSession:
        statement = None

        def exec(self, statement):
            self.statement = statement
            return _Result([101])

    session = _CaptureSession()
    assert quota_service._owned_vmids(session, uuid.uuid4()) == [101]
    assert "resources.allocation_scope" in str(session.statement)


def test_generic_ttl_query_excludes_teaching_class_resources():
    class _CaptureSession:
        statement = None

        def exec(self, statement):
            self.statement = statement
            return _Result([])

    session = _CaptureSession()
    assert resource_service.resource_repo.list_resources_with_expiry(
        session=session
    ) == []
    assert "resources.allocation_scope" in str(session.statement)


def test_teacher_cannot_manage_another_teachers_course_path(monkeypatch):
    owner_id = uuid.uuid4()
    teacher = _user(UserRole.teacher)
    monkeypatch.setattr(
        course_admin.course_service,
        "get_path_or_404",
        lambda *_args, **_kwargs: SimpleNamespace(created_by=owner_id),
    )

    with pytest.raises(PermissionDeniedError):
        course_admin._require_path(object(), teacher, uuid.uuid4())


def test_active_or_next_window_recovers_current_occurrence():
    now = datetime(2026, 8, 25, 5, 30, tzinfo=UTC)  # 13:30 Asia/Taipei
    window = compute_active_or_next_window(
        rule="FREQ=WEEKLY;BYDAY=TU;BYHOUR=13;BYMINUTE=0",
        duration_minutes=120,
        timezone="Asia/Taipei",
        now=now,
    )

    assert window is not None
    assert window[0] == datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
    assert window[1] == datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


def test_archived_or_ended_class_schedule_is_disabled():
    job = SimpleNamespace(schedule_timezone="Asia/Taipei")
    active = SimpleNamespace(
        status=TeachingClassStatus.active,
        end_date=date(2026, 8, 25),
    )
    archived = SimpleNamespace(
        status=TeachingClassStatus.archived,
        end_date=date(2026, 12, 31),
    )

    assert _class_schedule_enabled(
        active, job, datetime(2026, 8, 25, 17, 0, tzinfo=UTC)
    ) is False
    assert _class_schedule_enabled(
        archived, job, datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
    ) is False


def test_class_expiry_triggers_immediate_reclaim_and_later_retry():
    now = datetime(2026, 8, 25, 16, 30, tzinfo=UTC)
    ended = SimpleNamespace(
        timezone="Asia/Taipei",
        end_date=date(2026, 8, 25),
        end_time=time(15, 0),
        shutdown_grace_minutes=30,
    )
    retry = SimpleNamespace(
        status=TeachingClassStatus.archived,
        resources_reclaimed_at=None,
        reclaim_requested_at=now - timedelta(minutes=16),
    )
    in_progress = SimpleNamespace(
        status=TeachingClassStatus.archived,
        resources_reclaimed_at=None,
        reclaim_requested_at=now - timedelta(minutes=5),
    )

    assert _class_expired(
        ended, datetime(2026, 8, 25, 6, 59, tzinfo=UTC)
    ) is False
    assert _class_expired(
        ended, datetime(2026, 8, 25, 7, 0, tzinfo=UTC)
    ) is True
    assert _class_reclaim_retry_due(retry, now) is True
    assert _class_reclaim_retry_due(in_progress, now) is False


def test_class_reclaim_queues_idempotent_deletion_with_retries(monkeypatch):
    item = SimpleNamespace(
        id=uuid.uuid4(),
        reclaim_requested_at=None,
        resources_reclaimed_at=None,
    )
    resource = SimpleNamespace(vmid=801)
    session = _Session()
    monkeypatch.setattr(
        class_lifecycle_service.resource_repo,
        "get_resources_by_teaching_class",
        lambda **_kwargs: [resource],
    )
    monkeypatch.setattr(
        class_lifecycle_service.deletion_service,
        "list_active_for_vmids",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        class_lifecycle_service.proxmox_service,
        "find_resource",
        lambda _vmid: {"name": "class-vm", "node": "pve1", "type": "qemu"},
    )
    request_id = uuid.uuid4()
    monkeypatch.setattr(
        class_lifecycle_service.deletion_service,
        "create_deletion_request",
        lambda **_kwargs: SimpleNamespace(id=request_id),
    )
    submitted = []
    monkeypatch.setattr(
        class_lifecycle_service.deletion_service,
        "enqueue_processing",
        lambda *, session, req: submitted.append(req.id),
    )

    result = class_lifecycle_service.queue_reclaim(
        session=session,
        item=item,
        requested_by=uuid.uuid4(),
        force=True,
    )

    assert result["queued_vmids"] == [801]
    assert submitted == [request_id]



def test_class_reclaim_hides_infrastructure_exception(monkeypatch):
    internal_error = "PVE token secret-token rejected at internal-host"
    item = SimpleNamespace(
        id=uuid.uuid4(),
        reclaim_requested_at=None,
        resources_reclaimed_at=None,
    )
    monkeypatch.setattr(
        class_lifecycle_service.resource_repo,
        "get_resources_by_teaching_class",
        lambda **_kwargs: [SimpleNamespace(vmid=802)],
    )
    monkeypatch.setattr(
        class_lifecycle_service.deletion_service,
        "list_active_for_vmids",
        lambda **_kwargs: {},
    )

    def fail_lookup(_vmid):
        raise RuntimeError(internal_error)

    monkeypatch.setattr(
        class_lifecycle_service.proxmox_service,
        "find_resource",
        fail_lookup,
    )

    result = class_lifecycle_service.queue_reclaim(
        session=_Session(),
        item=item,
        requested_by=uuid.uuid4(),
        force=True,
    )

    assert result["failed"] == [
        {
            "vmid": 802,
            "error": "Resource reclaim could not be queued; retry later.",
        }
    ]
    assert internal_error not in result["failed"][0]["error"]


def test_retry_recovers_existing_resource_instead_of_cloning(monkeypatch):
    user_id = uuid.uuid4()
    class_id = uuid.uuid4()
    resource = SimpleNamespace(vmid=901)
    enrollment = SimpleNamespace(id=uuid.uuid4())
    results = iter([resource, enrollment, None])

    class _RecoverSession:
        def exec(self, _statement):
            return _Result(next(results))

        def add(self, _value):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(
        class_provision_service.proxmox_service,
        "find_resource",
        lambda _vmid: {"vmid": 901},
    )
    monkeypatch.setattr(
        class_provision_service.resource_repo,
        "assign_to_teaching_class",
        lambda **_kwargs: resource,
    )
    task = SimpleNamespace(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        user_id=user_id,
        status=BatchProvisionTaskStatus.failed,
        vmid=None,
        resource_vmid=None,
        error="worker interrupted",
        finished_at=None,
    )
    recovered = class_provision_service.recover_existing_task_resource(
        session=_RecoverSession(),
        item=SimpleNamespace(id=class_id, owner_id=uuid.uuid4()),
        node=SimpleNamespace(id=uuid.uuid4()),
        task=task,
    )

    assert recovered is True
    assert task.status == BatchProvisionTaskStatus.completed
    assert task.vmid == 901


def test_class_provision_uses_reserved_capacity_not_personal_quota(monkeypatch):
    quota_calls = []
    monkeypatch.setattr(
        batch_provision_service.quota_service,
        "check_quota",
        lambda *_args, **_kwargs: quota_calls.append(True),
    )
    monkeypatch.setattr(
        batch_provision_service.clone_service,
        "run_clone_task",
        lambda *_args, **_kwargs: {"vmid": 601},
    )

    vmid = batch_provision_service._provision_one(
        session=_Session(reservation=SimpleNamespace(status="reserved")),
        resource_type="qemu",
        hostname="class-vm",
        user_id=uuid.uuid4(),
        params={"vm_template_id": str(uuid.uuid4())},
        teaching_class_id=uuid.uuid4(),
    )

    assert vmid == 601
    assert quota_calls == []


def test_class_provision_rejects_missing_capacity_reservation():
    with pytest.raises(BadRequestError, match="容量保留"):
        batch_provision_service._provision_one(
            session=_Session(reservation=None),
            resource_type="qemu",
            hostname="class-vm",
            user_id=uuid.uuid4(),
            params={"vm_template_id": str(uuid.uuid4())},
            teaching_class_id=uuid.uuid4(),
        )


def test_active_class_resource_can_be_started_for_after_class_practice(monkeypatch):
    class_id = uuid.uuid4()
    job_id = uuid.uuid4()
    resource = SimpleNamespace(
        teaching_class_id=class_id,
        batch_job_id=job_id,
    )
    teaching_class = SimpleNamespace(status=TeachingClassStatus.active)
    job = SimpleNamespace(
        recurrence_rule="FREQ=WEEKLY;BYDAY=TU;BYHOUR=13;BYMINUTE=0",
        recurrence_duration_minutes=120,
        schedule_timezone="Asia/Taipei",
        next_window_start=datetime(2026, 8, 25, 5, 0, tzinfo=UTC),
        next_window_end=datetime(2026, 8, 25, 7, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource,
    )
    monkeypatch.setattr(
        resource_service,
        "_utc_now",
        lambda: datetime(2026, 8, 27, 12, 30, tzinfo=UTC),
    )

    resource_service._enforce_start_window(
        session=_Session(teaching_class=teaching_class, job=job),
        vmid=701,
    )


def test_inactive_class_resource_cannot_be_started(monkeypatch):
    class_id = uuid.uuid4()
    resource = SimpleNamespace(
        teaching_class_id=class_id,
        batch_job_id=uuid.uuid4(),
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: resource,
    )

    with pytest.raises(BadRequestError, match="no longer active"):
        resource_service._enforce_start_window(
            session=_Session(
                teaching_class=SimpleNamespace(
                    status=TeachingClassStatus.archived,
                ),
            ),
            vmid=701,
        )


def test_student_can_extend_owned_class_resource(monkeypatch):
    user_id = uuid.uuid4()
    current_stop = datetime(2026, 8, 25, 7, 30, tzinfo=UTC)
    saved = {}
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: SimpleNamespace(
            user_id=user_id,
            teaching_class_id=uuid.uuid4(),
            request_id=None,
            auto_stop_at=current_stop,
            auto_stop_reason="window_grace",
        ),
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "set_auto_stop",
        lambda **kwargs: saved.update(kwargs),
    )
    monkeypatch.setattr(
        resource_service,
        "get_schedule_policy",
        lambda **_kwargs: SimpleNamespace(practice_session_hours=3),
    )
    monkeypatch.setattr(
        resource_service,
        "_utc_now",
        lambda: datetime(2026, 8, 25, 7, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        resource_service.audit_service,
        "log_action",
        lambda **_kwargs: None,
    )

    result = resource_service.extend_session(
        session=object(),
        vmid=702,
        user_id=user_id,
    )

    assert result.extended_minutes == 180
    assert result.auto_stop_at == current_stop + timedelta(hours=3)
    assert saved["auto_stop_reason"] == "practice_quota"


def test_quick_practice_session_keeps_fixed_time_limit(monkeypatch):
    user_id = uuid.uuid4()
    request = SimpleNamespace(request_kind="quick_template")

    class SessionWithRequest:
        def get(self, _model, _key):
            return request

    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda **_kwargs: SimpleNamespace(
            user_id=user_id,
            teaching_class_id=None,
            request_id=uuid.uuid4(),
            auto_stop_at=datetime(2026, 8, 25, 7, 30, tzinfo=UTC),
            auto_stop_reason="practice_quota",
        ),
    )

    with pytest.raises(BadRequestError, match="fixed time limit"):
        resource_service.extend_session(
            session=SessionWithRequest(),
            vmid=703,
            user_id=user_id,
        )
