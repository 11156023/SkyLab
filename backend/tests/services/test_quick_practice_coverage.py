"""Coverage tests for app.services.quick_practice."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CourseEnvironment,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    IpAllocation,
    QuickPracticeSession,
    QuickPracticeSessionMachine,
    Resource,
    User,
    UserRole,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
    VMTemplateStatus,
)
from app.services import quick_practice
from app.services.network import ip_management_service
from app.services.proxmox import proxmox_service
from app.services.resource import deletion_service, quota_service, resource_service
from app.services.teaching import course_publication_service
from app.services.vm import vm_request_service
from tests.services.test_quick_practice_service import _session_graph as _base_graph


def _session_graph(db: Session, **kwargs):
    """同一個 DB 裡可以建多組（基底 helper 的 IP 是寫死的）。"""
    result = _base_graph(db, **kwargs)
    for row in db.exec(select(IpAllocation)).all():
        db.delete(row)
    db.commit()
    return result


@pytest.fixture
def qdb():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _user(db: Session, role: UserRole = UserRole.student) -> User:
    user = User(
        email=f"u-{uuid.uuid4()}@example.edu",
        hashed_password="hash",
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def _env_with_version(
    db: Session, *, nodes: int = 1, max_sessions: int | None = None
) -> tuple[CourseEnvironment, CourseEnvironmentVersion, User]:
    teacher = _user(db, UserRole.teacher)
    environment = CourseEnvironment(
        owner_id=teacher.id,
        name="練習環境",
        usage_scope="quick_practice",
        max_concurrent_sessions=max_sessions,
    )
    db.add(environment)
    db.flush()
    version = CourseEnvironmentVersion(
        environment_id=environment.id,
        version=1,
        status=CourseEnvironmentVersionStatus.published,
        published_at=datetime.now(UTC),
    )
    db.add(version)
    db.flush()
    for index in range(nodes):
        db.add(
            CourseEnvironmentNode(
                version_id=version.id,
                node_key=f"n{index}",
                source_type="custom",
                custom_image_ref="local:vztmpl/debian.tar.zst",
                name=f"Node{index}",
                role="server",
                resource_type="lxc",
                cpu=1,
                memory_mb=512,
                disk_gb=8,
                sort_order=index,
            )
        )
    db.commit()
    return environment, version, teacher


# ---------------------------------------------------------------- lookups


def test_get_published_template_requires_a_published_version(qdb: Session) -> None:
    teacher = _user(qdb, UserRole.teacher)
    environment = CourseEnvironment(
        owner_id=teacher.id, name="未發佈", usage_scope="both"
    )
    qdb.add(environment)
    qdb.commit()

    with pytest.raises(NotFoundError):
        quick_practice.get_published_template(qdb, environment_id=environment.id)


def test_environment_for_version_missing_environment(qdb: Session) -> None:
    version = CourseEnvironmentVersion(environment_id=uuid.uuid4(), version=1)
    with pytest.raises(NotFoundError):
        quick_practice._environment_for_version(qdb, version)


# ---------------------------------------------------------------- reconcile


def test_reconcile_session_missing_or_reclaimed_returns_as_is(qdb: Session) -> None:
    assert quick_practice.reconcile_session(qdb, practice_id=uuid.uuid4()) is None

    practice, _requests = _session_graph(qdb)
    practice.status = "reclaiming"
    qdb.add(practice)
    qdb.commit()
    assert quick_practice.reconcile_session(qdb, practice_id=practice.id) is practice


def test_reconcile_session_without_machines_returns_unchanged(qdb: Session) -> None:
    _environment, version, _teacher = _env_with_version(qdb)
    student = _user(qdb)
    practice = QuickPracticeSession(
        user_id=student.id,
        environment_version_id=version.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    qdb.add(practice)
    qdb.commit()

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result is not None and result.status == "creating"


def test_reconcile_session_waits_for_unfinished_machines(qdb: Session) -> None:
    practice, requests = _session_graph(qdb)
    requests[0].vmid = None
    requests[0].provisioning_status = VMProvisioningStatus.running
    qdb.add(requests[0])
    practice.status = "partial_failed"
    qdb.add(practice)
    qdb.commit()

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result is not None and result.status == "creating"


def test_reconcile_session_already_applied_topology_is_ready(qdb: Session) -> None:
    practice, _requests = _session_graph(qdb)
    practice.topology_applied_at = datetime.now(UTC)
    practice.last_error = "old"
    qdb.add(practice)
    qdb.commit()

    result = quick_practice.reconcile_session(qdb, practice_id=practice.id)

    assert result is not None
    assert result.status == "ready"
    assert result.last_error is None


def test_reconcile_for_request_reconciles_owning_session(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _session_graph(qdb)
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(
        quick_practice,
        "reconcile_session",
        lambda session, *, practice_id: calls.append(practice_id),
    )

    quick_practice.reconcile_for_request(qdb, request_id=requests[0].id)
    quick_practice.reconcile_for_request(qdb, request_id=uuid.uuid4())

    assert calls == [practice.id]


# ---------------------------------------------------------------- settle


class _FakeExec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, locked):
        self.locked = locked
        self.added: list = []
        self.commits = 0

    def exec(self, _statement):
        return _FakeExec(self.locked)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def _req(**overrides):
    values = {
        "id": uuid.uuid4(),
        "vmid": None,
        "status": VMRequestStatus.approved,
        "provisioning_status": VMProvisioningStatus.pending,
        "provisioning_started_at": None,
        "review_comment": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_settle_unstarted_machines_handles_locked_and_changed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second, third, fourth = _req(), _req(), _req(), _req()
    rows = [(None, item, None) for item in (first, second, third, fourth)]
    monkeypatch.setattr(
        quick_practice, "_session_machine_rows", lambda session, *, practice_id: rows
    )
    # 鎖住後重讀：一台已經拿到 vmid、一台已經開始 clone、一台可取消；第四台被鎖住
    landed = _req(id=first.id, vmid=9001)
    cloning = _req(
        id=second.id,
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=datetime.now(UTC),
    )
    cancellable = _req(id=third.id)
    fake = _FakeSession([landed, cloning, cancellable])

    in_flight = quick_practice._settle_unstarted_machines(
        fake, practice_id=uuid.uuid4()
    )

    assert in_flight is True
    assert cancellable.status == VMRequestStatus.cancelled
    assert landed.status == VMRequestStatus.approved
    assert cloning.status == VMRequestStatus.approved
    assert fake.added == [cancellable]
    assert fake.commits == 1


# ---------------------------------------------------------------- reclaim queue


def _add_resources(db: Session, requests: list[VMRequest]) -> None:
    for request in requests:
        db.add(
            Resource(
                vmid=request.vmid,
                request_id=request.id,
                user_id=request.user_id,
                environment_type="快速練習",
                created_at=datetime.now(UTC),
            )
        )
    db.commit()


def test_queue_reclaim_skips_active_and_records_inspection_errors(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _session_graph(qdb)
    _add_resources(qdb, requests)
    monkeypatch.setattr(
        deletion_service,
        "list_active_for_vmids",
        lambda **_kwargs: {requests[0].vmid: object()},
    )

    def boom(vmid):
        raise RuntimeError("pve down")

    monkeypatch.setattr(proxmox_service, "find_resource", boom)

    queued = quick_practice._queue_session_reclaim(qdb, practice=practice)

    assert queued == 0
    qdb.refresh(practice)
    assert practice.status == "reclaiming"
    assert practice.last_error == f"VMID {requests[1].vmid} 無法排入回收"


def test_queue_reclaim_removes_orphans_and_marks_reclaimed(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _session_graph(qdb)
    _add_resources(qdb, requests)
    monkeypatch.setattr(deletion_service, "list_active_for_vmids", lambda **_kw: {})

    def not_found(vmid):
        raise NotFoundError("gone")

    monkeypatch.setattr(proxmox_service, "find_resource", not_found)
    orphans: list[int] = []

    def delete_orphan(*, session, vmid, user_id):
        orphans.append(vmid)
        row = session.exec(select(Resource).where(Resource.vmid == vmid)).one()
        session.delete(row)

    monkeypatch.setattr(resource_service, "delete_orphan_db_record", delete_orphan)

    queued = quick_practice._queue_session_reclaim(qdb, practice=practice)

    assert queued == 0
    assert sorted(orphans) == sorted(r.vmid for r in requests)
    qdb.refresh(practice)
    assert practice.status == "reclaimed"
    assert practice.reclaimed_at is not None


# ---------------------------------------------------------------- lifecycle


def _bind_engine(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    monkeypatch.setattr("app.core.db.engine", db.get_bind())


def test_lifecycle_skips_sessions_that_vanished(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_graph(qdb)
    _bind_engine(monkeypatch, qdb)

    class _NoGetSession(Session):
        def get(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(quick_practice, "Session", _NoGetSession)

    assert quick_practice.process_lifecycle() == 0


def test_lifecycle_reclaims_session_with_failed_machines(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _session_graph(qdb)
    requests[0].provisioning_status = VMProvisioningStatus.failed
    qdb.add(requests[0])
    qdb.commit()
    practice_id = practice.id
    _bind_engine(monkeypatch, qdb)

    assert quick_practice.process_lifecycle() == 1

    qdb.expire_all()
    refreshed = qdb.get(QuickPracticeSession, practice_id)
    assert refreshed is not None
    assert refreshed.status == "reclaimed"


def test_lifecycle_stops_then_reclaims_expired_sessions(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    grace, _ = _session_graph(qdb)
    grace.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    grace.status = "ready"
    late, _ = _session_graph(qdb)
    late.expires_at = datetime.now(UTC) - timedelta(hours=2)
    late.status = "ready"
    qdb.add_all([grace, late])
    qdb.commit()
    grace_id, late_id = grace.id, late.id
    _bind_engine(monkeypatch, qdb)

    assert quick_practice.process_lifecycle() == 2

    qdb.expire_all()
    assert qdb.get(QuickPracticeSession, grace_id).status == "stopping"
    assert qdb.get(QuickPracticeSession, late_id).status == "reclaimed"


def test_lifecycle_logs_and_continues_on_errors(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _session_graph(qdb)
    _bind_engine(monkeypatch, qdb)

    def boom(*_args, **_kwargs):
        raise RuntimeError("reconcile failed")

    monkeypatch.setattr(quick_practice, "reconcile_session", boom)

    assert quick_practice.process_lifecycle() == 0


# ---------------------------------------------------------------- machine request


def _node(**overrides) -> CourseEnvironmentNode:
    values = {
        "version_id": uuid.uuid4(),
        "node_key": "web",
        "source_type": "custom",
        "custom_image_ref": "9000",
        "name": "Web",
        "role": "server",
        "resource_type": "qemu",
        "cpu": 1,
        "memory_mb": 1024,
        "disk_gb": 10,
        "sort_order": 0,
    }
    values.update(overrides)
    return CourseEnvironmentNode(**values)


def _build_request(db: Session, node: CourseEnvironmentNode):
    now = datetime.now(UTC)
    return quick_practice._machine_request(
        session=db,
        node=node,
        environment=CourseEnvironment(owner_id=uuid.uuid4(), name="環境"),
        practice_session_id=uuid.uuid4(),
        now=now,
        expires_at=now + timedelta(hours=1),
    )


def test_machine_request_rejects_unready_template(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda session, node: 10)
    template = VMTemplate(
        pve_vmid=9100, name="t", node="pve1", status=VMTemplateStatus.creating
    )
    qdb.add(template)
    qdb.commit()

    with pytest.raises(BadRequestError):
        _build_request(
            qdb, _node(source_type="template", source_template_id=template.id)
        )


def test_machine_request_uses_ready_template(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda session, node: 12)
    vm_template = VMTemplate(
        pve_vmid=9200,
        name="vm",
        node="pve1",
        storage="ceph",
        status=VMTemplateStatus.ready,
    )
    lxc_template = VMTemplate(
        pve_vmid=9201,
        name="ct",
        node="pve1",
        resource_type="lxc",
        status=VMTemplateStatus.ready,
    )
    qdb.add_all([vm_template, lxc_template])
    qdb.commit()

    vm_request = _build_request(
        qdb, _node(source_type="template", source_template_id=vm_template.id)
    )
    lxc_request = _build_request(
        qdb,
        _node(
            source_type="template",
            source_template_id=lxc_template.id,
            resource_type="lxc",
            custom_storage="local-zfs",
        ),
    )

    assert vm_request.template_id == 9200
    assert vm_request.storage == "ceph"
    assert vm_request.username == "student"
    assert vm_request.disk_size == 12
    assert lxc_request.template_id == 9201
    assert lxc_request.storage == "local-zfs"
    assert lxc_request.username is None
    assert lxc_request.rootfs_size == 12


def test_machine_request_rejects_non_numeric_custom_template(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda session, node: 10)
    with pytest.raises(BadRequestError):
        _build_request(qdb, _node(custom_image_ref="not-a-number"))


# ---------------------------------------------------------------- launch


def test_launch_rejects_environment_without_machines(qdb: Session) -> None:
    environment, _version, _teacher = _env_with_version(qdb, nodes=0)
    student = _user(qdb)
    with pytest.raises(BadRequestError):
        quick_practice.launch(qdb, user=student, environment_id=environment.id)


def test_launch_rejects_unknown_user(qdb: Session) -> None:
    environment, _version, _teacher = _env_with_version(qdb)
    with pytest.raises(NotFoundError):
        quick_practice.launch(
            qdb, user=SimpleNamespace(id=uuid.uuid4()), environment_id=environment.id
        )


def test_launch_rejects_second_active_session(qdb: Session) -> None:
    environment, version, _teacher = _env_with_version(qdb)
    student = _user(qdb)
    practice = QuickPracticeSession(
        user_id=student.id,
        environment_version_id=version.id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        status="ready",
    )
    qdb.add(practice)
    qdb.flush()
    request = VMRequest(
        user_id=student.id,
        reason="quick practice",
        resource_type="lxc",
        hostname="practice-x",
        status=VMRequestStatus.approved,
        provisioning_status=VMProvisioningStatus.pending,
        created_at=datetime.now(UTC),
    )
    qdb.add(request)
    qdb.flush()
    qdb.add(
        QuickPracticeSessionMachine(
            session_id=practice.id, vm_request_id=request.id, node_key="n0"
        )
    )
    qdb.commit()

    with pytest.raises(BadRequestError):
        quick_practice.launch(qdb, user=student, environment_id=environment.id)


def test_launch_enforces_daily_limit(qdb: Session) -> None:
    environment, version, _teacher = _env_with_version(qdb)
    student = _user(qdb)
    for _ in range(quick_practice.MAX_SESSIONS_PER_24_HOURS):
        qdb.add(
            QuickPracticeSession(
                user_id=student.id,
                environment_version_id=version.id,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
                status="reclaimed",
            )
        )
    qdb.commit()

    with pytest.raises(BadRequestError):
        quick_practice.launch(qdb, user=student, environment_id=environment.id)


def test_launch_creates_requests_and_submits_provisioning(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment, _version, _teacher = _env_with_version(qdb, nodes=2, max_sessions=5)
    student = _user(qdb)
    qdb.commit()
    quotas: list[dict] = []
    monkeypatch.setattr(
        quota_service,
        "check_quota",
        lambda session, user_id, **kwargs: quotas.append(kwargs),
    )
    monkeypatch.setattr(quick_practice, "_node_disk_gb", lambda session, node: 8)
    # 叢集容量預檢另有專門的測試（test_quick_practice_publication_retry）
    monkeypatch.setattr(
        quick_practice, "_assert_cluster_capacity", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        quick_practice,
        "get_schedule_policy",
        lambda *, session: SimpleNamespace(practice_session_hours=2),
    )
    reserved: list[list[str]] = []
    monkeypatch.setattr(
        ip_management_service,
        "reserve_ips",
        lambda session, *, teaching_class_id, reservation_keys: reserved.append(
            reservation_keys
        ),
    )
    created: list = []

    def create_request(*, session, request_in, user, placement_group_id):
        created.append((request_in, placement_group_id))
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        vm_request_service, "create_quick_practice_request", create_request
    )
    submitted: list[uuid.UUID] = []
    monkeypatch.setattr(
        vm_request_service,
        "submit_course_provision",
        lambda session, *, request_id, user_id: submitted.append(request_id),
    )

    practice = quick_practice.launch(qdb, user=student, environment_id=environment.id)

    assert practice.status == "creating"
    assert quotas == [
        {
            "delta_cores": 2,
            "delta_memory_mb": 1024,
            "delta_disk_gb": 16,
            "delta_instances": 2,
        }
    ]
    assert len(reserved[0]) == 2
    assert all(group == practice.id for _req_in, group in created)
    assert created[0][0].resource_type == "lxc"
    assert len(submitted) == 2
    machines = qdb.exec(
        select(QuickPracticeSessionMachine).where(
            QuickPracticeSessionMachine.session_id == practice.id
        )
    ).all()
    assert {machine.node_key for machine in machines} == {"n0", "n1"}


# ---------------------------------------------------------------- end / list


def test_end_session_returns_already_reclaiming_session(qdb: Session) -> None:
    practice, _requests = _session_graph(qdb)
    practice.status = "reclaiming"
    qdb.add(practice)
    qdb.commit()

    ended = quick_practice.end_session(
        qdb, user=SimpleNamespace(id=practice.user_id), practice_id=practice.id
    )

    assert ended.status == "reclaiming"


def test_list_sessions_filters_by_user(qdb: Session) -> None:
    practice, _requests = _session_graph(qdb)
    other, _ = _session_graph(qdb)
    gone, _ = _session_graph(qdb)
    gone.status = "reclaimed"
    qdb.add(gone)
    qdb.commit()

    everyone = {item.id for item in quick_practice.list_sessions(qdb)}
    mine = quick_practice.list_sessions(qdb, user_id=practice.user_id)

    assert everyone == {practice.id, other.id}
    assert [item.id for item in mine] == [practice.id]


# ---------------------------------------------------------------- serialize


def test_serialize_session_requires_version(qdb: Session) -> None:
    practice = QuickPracticeSession(
        user_id=uuid.uuid4(),
        environment_version_id=uuid.uuid4(),
        expires_at=datetime.now(UTC),
    )
    with pytest.raises(NotFoundError):
        quick_practice.serialize_session(qdb, practice)


def test_serialize_session_reports_machine_and_group_status(
    qdb: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _session_graph(qdb)
    monkeypatch.setattr(
        course_publication_service,
        "public_urls_by_vmid",
        lambda session, vmids: {requests[0].vmid: "https://web.example.edu"},
    )
    monkeypatch.setattr(
        course_publication_service,
        "forward_endpoints_by_vmid",
        lambda session, vmids: {requests[0].vmid: [{"port": 2222}]},
    )
    monkeypatch.setattr(
        quick_practice.resource_repo,
        "get_cached_ip_address",
        lambda *, session, vmid: f"10.0.0.{vmid % 100}",
    )

    # 兩台都建好、拓樸還沒套：仍是 provisioning
    data = quick_practice.serialize_session(qdb, practice)
    assert data["status"] == "provisioning"
    assert [m["status"] for m in data["machines"]] == ["running", "running"]
    assert data["machines"][0]["public_url"] == "https://web.example.edu"
    assert data["machines"][0]["forward_endpoints"] == [{"port": 2222}]
    assert data["machines"][1]["public_url"] is None
    assert data["machines"][0]["ip_address"] == f"10.0.0.{requests[0].vmid % 100}"

    # 一台失敗、一台還在跑（有 vmid 但未完成）
    requests[0].provisioning_status = VMProvisioningStatus.running
    requests[1].vmid = None
    requests[1].provisioning_status = VMProvisioningStatus.failed
    qdb.add_all(requests)
    qdb.commit()
    data = quick_practice.serialize_session(qdb, practice)
    assert [m["status"] for m in data["machines"]] == ["provisioning", "failed"]
    assert data["status"] == "partial_failed"
    assert data["machines"][1]["ip_address"] is None
    assert data["machines"][1]["forward_endpoints"] == []

    # 還沒拿到 vmid、也沒失敗
    requests[1].provisioning_status = VMProvisioningStatus.pending
    qdb.add(requests[1])
    qdb.commit()
    data = quick_practice.serialize_session(qdb, practice)
    assert data["machines"][1]["status"] == "provisioning"

    # ready 一律回 running；其他狀態原樣
    practice.status = "ready"
    qdb.add(practice)
    qdb.commit()
    assert quick_practice.serialize_session(qdb, practice)["status"] == "running"
    practice.status = "stopping"
    qdb.add(practice)
    qdb.commit()
    assert quick_practice.serialize_session(qdb, practice)["status"] == "stopping"
