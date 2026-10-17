"""app.repositories.vm_request 的建立、查詢、狀態更新與結案判斷覆蓋（記憶體 SQLite）。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

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


@pytest.mark.parametrize("commit", [True, False])
def test_mark_vm_request_expired(session: Session, commit: bool) -> None:
    r = _req(session)
    out = repo.mark_vm_request_expired(session=session, db_request=r, commit=commit)
    assert out.status == VMRequestStatus.expired
    assert out.reviewer_id is None
    assert out.reviewed_at is None
