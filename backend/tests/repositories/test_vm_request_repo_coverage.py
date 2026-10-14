"""app.repositories.vm_request 的建立、查詢、狀態更新與結案判斷覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.domain.resource_markers import RESOURCE_DELETED_MARKERS
from app.models import Resource, VMProvisioningStatus, VMRequest, VMRequestStatus
from app.repositories import resource as resource_repo
from app.repositories import vm_request as repo

NOW = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _req(session: Session, **kw) -> VMRequest:
    kw.setdefault("user_id", uuid.uuid4())
    kw.setdefault("reason", "coverage test request")
    kw.setdefault("resource_type", "lxc")
    kw.setdefault("hostname", f"h-{uuid.uuid4().hex[:8]}")
    kw.setdefault("created_at", NOW)
    r = VMRequest(**kw)
    session.add(r)
    session.commit()
    session.refresh(r)
    return r


def _create_in(**kw) -> SimpleNamespace:
    base = dict(
        reason="please create this",
        resource_type="lxc",
        hostname="測試主機",
        cores=2,
        memory=2048,
        storage="local-lvm",
        environment_type="Custom",
        os_info=None,
        expiry_date=None,
        start_at=None,
        end_at=None,
        ostemplate=None,
        rootfs_size=None,
        template_id=None,
        disk_size=None,
        username=None,
        gpu_mapping_id=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("commit", [True, False])
def test_create_vm_request(session: Session, commit: bool) -> None:
    r = repo.create_vm_request(
        session=session,
        vm_request_in=_create_in(mode="quick_template"),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        encrypted_password=None,
        commit=commit,
    )
    assert r.request_kind == "quick_template"
    assert r.hostname.startswith("xn--")
    assert r.status == VMRequestStatus.pending
    assert r.provisioning_status == VMProvisioningStatus.idle


def test_create_vm_request_explicit_kind_and_default_mode(session: Session) -> None:
    r = repo.create_vm_request(
        session=session,
        vm_request_in=_create_in(hostname="plain"),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        encrypted_password="enc",
    )
    assert r.request_kind == "research"
    r2 = repo.create_vm_request(
        session=session,
        vm_request_in=_create_in(hostname="plain2"),  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        encrypted_password=None,
        request_kind="course",
    )
    assert r2.request_kind == "course"


def test_get_vm_request_by_id(session: Session) -> None:
    r = _req(session)
    assert repo.get_vm_request_by_id(session=session, request_id=r.id).id == r.id
    assert (
        repo.get_vm_request_by_id(
            session=session, request_id=r.id, for_update=True, skip_locked=True
        ).id
        == r.id
    )
    assert repo.get_vm_request_by_id(session=session, request_id=uuid.uuid4()) is None


def test_get_vm_requests_by_user_paginates(session: Session) -> None:
    uid = uuid.uuid4()
    old = _req(session, user_id=uid, created_at=NOW - timedelta(days=1))
    new = _req(session, user_id=uid, created_at=NOW)
    _req(session)

    rows, count = repo.get_vm_requests_by_user(session=session, user_id=uid)
    assert count == 2
    assert [r.id for r in rows] == [new.id, old.id]
    rows, count = repo.get_vm_requests_by_user(
        session=session, user_id=uid, skip=1, limit=1
    )
    assert count == 2
    assert [r.id for r in rows] == [old.id]


def test_get_all_vm_requests_with_and_without_status(session: Session) -> None:
    p = _req(session, created_at=NOW - timedelta(hours=1))
    a = _req(session, status=VMRequestStatus.approved, created_at=NOW)

    rows, count = repo.get_all_vm_requests(session=session)
    assert count == 2
    assert [r.id for r in rows] == [a.id, p.id]
    rows, count = repo.get_all_vm_requests(
        session=session, status=VMRequestStatus.pending
    )
    assert count == 1
    assert [r.id for r in rows] == [p.id]


def test_get_approved_vm_requests_overlapping_window(session: Session) -> None:
    hit = _req(
        session,
        status=VMRequestStatus.approved,
        start_at=NOW,
        end_at=NOW + timedelta(hours=2),
        desired_node="pve1",
    )
    _req(  # no desired node
        session,
        status=VMRequestStatus.approved,
        start_at=NOW,
        end_at=NOW + timedelta(hours=2),
    )
    rows = repo.get_approved_vm_requests_overlapping_window(
        session=session,
        window_start=NOW + timedelta(minutes=30),
        window_end=NOW + timedelta(hours=1),
    )
    assert [r.id for r in rows] == [hit.id]


def test_get_unprovisioned_gpu_requests_overlapping_window(session: Session) -> None:
    a = _req(
        session,
        status=VMRequestStatus.approved,
        gpu_mapping_id="gpu0",
        start_at=NOW,
        end_at=None,
    )
    b = _req(
        session,
        status=VMRequestStatus.approved,
        gpu_mapping_id="gpu0",
        start_at=NOW + timedelta(minutes=10),
        end_at=NOW + timedelta(hours=3),
    )
    _req(  # already provisioned
        session,
        status=VMRequestStatus.approved,
        gpu_mapping_id="gpu0",
        vmid=500,
        start_at=NOW,
        end_at=NOW + timedelta(hours=3),
    )
    kwargs = dict(
        session=session,
        gpu_mapping_id="gpu0",
        window_start=NOW,
        window_end=NOW + timedelta(hours=1),
    )
    assert [
        r.id for r in repo.get_unprovisioned_gpu_requests_overlapping_window(**kwargs)
    ] == [a.id, b.id]
    assert [
        r.id
        for r in repo.get_unprovisioned_gpu_requests_overlapping_window(
            **kwargs, exclude_request_id=a.id
        )
    ] == [b.id]


def test_lock_overlapping_vm_requests_for_window(session: Session) -> None:
    p = _req(session, start_at=NOW, end_at=NOW + timedelta(hours=1))
    _req(
        session,
        start_at=NOW,
        end_at=NOW + timedelta(hours=1),
        provisioning_status=VMProvisioningStatus.failed,
    )
    rows = repo.lock_overlapping_vm_requests_for_window(
        session=session, window_start=NOW, window_end=NOW + timedelta(minutes=5)
    )
    assert [r.id for r in rows] == [p.id]


@pytest.mark.parametrize("commit", [True, False])
def test_update_vm_request_status_all_fields(session: Session, commit: bool) -> None:
    r = _req(session)
    reviewer = uuid.uuid4()
    out = repo.update_vm_request_status(
        session=session,
        db_request=r,
        status=VMRequestStatus.approved,
        reviewer_id=reviewer,
        review_comment="ok",
        vmid=321,
        assigned_node="pve1",
        desired_node="pve2",
        actual_node="pve3",
        placement_strategy_used="balanced",
        provisioning_status=VMProvisioningStatus.running,
        provisioning_error="none",
        commit=commit,
    )
    assert out.status == VMRequestStatus.approved
    assert out.reviewer_id == reviewer
    assert out.vmid == 321
    assert out.assigned_node == "pve1"
    assert out.desired_node == "pve2"
    assert out.actual_node == "pve3"
    assert out.placement_strategy_used == "balanced"
    assert out.provisioning_status == VMProvisioningStatus.running
    assert out.provisioning_error == "none"
    assert out.reviewed_at is not None


def test_update_vm_request_status_minimal_keeps_fields(session: Session) -> None:
    r = _req(session, vmid=7, assigned_node="keep")
    out = repo.update_vm_request_status(
        session=session,
        db_request=r,
        status=VMRequestStatus.rejected,
        reviewer_id=uuid.uuid4(),
    )
    assert out.vmid == 7
    assert out.assigned_node == "keep"
    assert out.provisioning_error is None


@pytest.mark.parametrize("commit", [True, False])
def test_update_vm_request_provisioning(session: Session, commit: bool) -> None:
    r = _req(session)
    out = repo.update_vm_request_provisioning(
        session=session,
        db_request=r,
        vmid=600,
        assigned_node="pve1",
        actual_node="pve9",
        placement_strategy_used="spread",
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error=None,
        commit=commit,
    )
    assert out.vmid == 600
    assert out.desired_node == "pve1"
    assert out.actual_node == "pve9"
    assert out.provisioning_status == VMProvisioningStatus.completed

    out = repo.update_vm_request_provisioning(
        session=session,
        db_request=r,
        vmid=None,
        assigned_node="pve1",
        desired_node="pve2",
    )
    assert out.vmid == 600
    assert out.desired_node == "pve2"
    assert out.actual_node == "pve9"
    assert out.provisioning_status == VMProvisioningStatus.completed


@pytest.mark.parametrize("commit", [True, False])
def test_clear_vm_request_provisioning_removes_stale_resource(
    session: Session, commit: bool
) -> None:
    resource_repo.create_resource(
        session=session, vmid=700, user_id=uuid.uuid4(), environment_type="vm"
    )
    r = _req(
        session,
        vmid=700,
        assigned_node="a",
        desired_node="b",
        actual_node="c",
        placement_strategy_used="x",
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error="boom",
    )
    out = repo.clear_vm_request_provisioning(
        session=session, db_request=r, commit=commit
    )
    assert out.vmid is None
    assert out.assigned_node is None
    assert out.desired_node is None
    assert out.actual_node is None
    assert out.placement_strategy_used is None
    assert out.provisioning_status == VMProvisioningStatus.idle
    assert out.provisioning_error is None
    assert session.get(Resource, 700) is None


def test_clear_vm_request_provisioning_without_vmid(session: Session) -> None:
    r = _req(session)
    out = repo.clear_vm_request_provisioning(session=session, db_request=r)
    assert out.vmid is None


def test_latest_approved_and_window_request(session: Session) -> None:
    closed_marker = sorted(RESOURCE_DELETED_MARKERS)[0]
    older = _req(
        session,
        status=VMRequestStatus.approved,
        vmid=800,
        reviewed_at=NOW - timedelta(days=2),
    )
    closed = _req(
        session,
        status=VMRequestStatus.approved,
        vmid=800,
        reviewed_at=NOW,
        resource_warning=closed_marker,
    )
    other = _req(session, status=VMRequestStatus.approved, vmid=801)

    assert (
        repo.get_latest_approved_vm_request_by_vmid(session=session, vmid=800).id
        == closed.id
    )
    # closed request is skipped; source request pointing to this vmid wins
    assert repo.get_window_request_for_vmid(session=session, vmid=800).id == older.id
    assert (
        repo.get_window_request_for_vmid(
            session=session, vmid=800, source_request_id=closed.id
        ).id
        == closed.id
    )
    # source points to another vmid / missing -> fall back to the query
    assert (
        repo.get_window_request_for_vmid(
            session=session, vmid=800, source_request_id=other.id
        ).id
        == older.id
    )
    assert (
        repo.get_window_request_for_vmid(
            session=session, vmid=800, source_request_id=uuid.uuid4()
        ).id
        == older.id
    )

    assert (
        repo.get_latest_approved_vm_requests_by_vmids(session=session, vmids=[None])
        == {}
    )
    latest = repo.get_latest_approved_vm_requests_by_vmids(
        session=session, vmids=[800, 801, None]
    )
    assert latest[800].id == closed.id
    assert latest[801].id == other.id
    latest = repo.get_latest_approved_vm_requests_by_vmids(
        session=session, vmids=[800], skip_closed=True
    )
    assert latest[800].id == older.id


def test_list_active_approved_vm_requests(session: Session) -> None:
    active = _req(
        session,
        status=VMRequestStatus.approved,
        start_at=NOW - timedelta(hours=1),
        end_at=None,
    )
    _req(  # ended
        session,
        status=VMRequestStatus.approved,
        start_at=NOW - timedelta(hours=3),
        end_at=NOW - timedelta(hours=1),
    )
    _req(  # not started
        session,
        status=VMRequestStatus.approved,
        start_at=NOW + timedelta(hours=1),
    )
    rows = repo.list_active_approved_vm_requests(session=session, at_time=NOW)
    assert [r.id for r in rows] == [active.id]


def test_list_expired_requests(session: Session) -> None:
    pending_expired = _req(session, end_at=NOW - timedelta(hours=1))
    _req(session, end_at=None)
    approved_expired = _req(
        session, status=VMRequestStatus.approved, end_at=NOW - timedelta(hours=1)
    )
    _req(
        session,
        status=VMRequestStatus.approved,
        vmid=900,
        end_at=NOW - timedelta(hours=1),
    )
    assert [
        r.id
        for r in repo.list_expired_pending_vm_requests(session=session, at_time=NOW)
    ] == [pending_expired.id]
    assert [
        r.id
        for r in repo.list_expired_unprovisioned_approved_vm_requests(
            session=session, at_time=NOW
        )
    ] == [approved_expired.id]


@pytest.mark.parametrize("commit", [True, False])
def test_mark_vm_request_expired(session: Session, commit: bool) -> None:
    r = _req(session)
    out = repo.mark_vm_request_expired(session=session, db_request=r, commit=commit)
    assert out.status == VMRequestStatus.expired
    assert out.reviewer_id is None
    assert out.reviewed_at is None
