"""還在建立中的快速練習被結束／回收時，不能留下沒人回收的機器。"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    IpAllocation,
    QuickPracticeSession,
    Resource,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
)
from app.services import quick_practice
from tests.services.test_quick_practice_service import _session_graph


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
    engine.dispose()


def _unprovisioned_session(
    db: Session, *, provisioning_status: VMProvisioningStatus, started_minutes_ago: int = 1
) -> tuple[QuickPracticeSession, list[VMRequest]]:
    practice, requests = _session_graph(db)
    # _session_graph 會建好兩台「已完成」的機器，這裡改成還沒 clone 完的樣子
    for allocation in db.exec(select(IpAllocation)).all():
        db.delete(allocation)
    for index, request in enumerate(requests):
        request.vmid = None
        request.actual_node = None
        request.provisioning_status = provisioning_status
        request.provisioning_started_at = datetime.now(UTC) - timedelta(
            minutes=started_minutes_ago
        )
        db.add(request)
        db.add(
            IpAllocation(
                ip_address=f"10.20.0.{50 + index}",
                purpose="quick_practice",
                reservation_key=quick_practice._ip_reservation_key(
                    practice.id, f"node{index}"
                ),
            )
        )
    db.commit()
    return practice, requests


def _reservations(db: Session, practice_id: uuid.UUID) -> list[IpAllocation]:
    prefix = quick_practice._ip_reservation_prefix(practice_id)
    return [
        row
        for row in db.exec(select(IpAllocation)).all()
        if (row.reservation_key or "").startswith(prefix)
    ]


def test_ending_while_cloning_keeps_session_reclaiming_and_ips_reserved(
    db: Session,
) -> None:
    practice, requests = _unprovisioned_session(
        db, provisioning_status=VMProvisioningStatus.running
    )
    student_id = practice.user_id

    ended = quick_practice.end_session(
        db, user=SimpleNamespace(id=student_id), practice_id=practice.id
    )

    assert ended.status == "reclaiming"
    assert ended.reclaimed_at is None
    assert len(_reservations(db, practice.id)) == 2
    for request in requests:
        db.refresh(request)
        # 正在 clone 的不能取消（會變成孤兒），等建好再走刪除
        assert request.status == VMRequestStatus.approved


def test_ending_before_clone_starts_cancels_the_requests(db: Session) -> None:
    practice, requests = _unprovisioned_session(
        db, provisioning_status=VMProvisioningStatus.pending
    )

    ended = quick_practice.end_session(
        db, user=SimpleNamespace(id=practice.user_id), practice_id=practice.id
    )

    assert ended.status == "reclaimed"
    assert ended.reclaimed_at is not None
    assert _reservations(db, practice.id) == []
    for request in requests:
        db.refresh(request)
        assert request.status == VMRequestStatus.cancelled


def test_stale_running_clone_does_not_block_reclaim_forever(db: Session) -> None:
    practice, requests = _unprovisioned_session(
        db,
        provisioning_status=VMProvisioningStatus.running,
        started_minutes_ago=24 * 60,
    )

    ended = quick_practice.end_session(
        db, user=SimpleNamespace(id=practice.user_id), practice_id=practice.id
    )

    assert ended.status == "reclaimed"
    for request in requests:
        db.refresh(request)
        assert request.status == VMRequestStatus.cancelled


def test_failed_machine_without_vmid_counts_as_done(db: Session) -> None:
    practice, requests = _unprovisioned_session(
        db, provisioning_status=VMProvisioningStatus.failed
    )

    ended = quick_practice.end_session(
        db, user=SimpleNamespace(id=practice.user_id), practice_id=practice.id
    )

    assert ended.status == "reclaimed"


def test_lifecycle_deletes_the_machine_once_its_clone_lands(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    practice, requests = _unprovisioned_session(
        db, provisioning_status=VMProvisioningStatus.running
    )
    practice_id = practice.id
    quick_practice.end_session(
        db, user=SimpleNamespace(id=practice.user_id), practice_id=practice_id
    )

    # worker 把 clone 做完：vmid、completed 與 Resource 同一筆交易寫入
    for index, request in enumerate(requests):
        db.refresh(request)
        request.vmid = 9300 + index
        request.provisioning_status = VMProvisioningStatus.completed
        db.add(request)
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

    from app.services.proxmox import proxmox_service
    from app.services.resource import deletion_service

    monkeypatch.setattr("app.core.db.engine", db.get_bind())
    monkeypatch.setattr(
        deletion_service, "list_active_for_vmids", lambda **_kwargs: {}
    )
    monkeypatch.setattr(
        proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "node": "pve1", "type": "qemu"},
    )
    deleted: list[int] = []
    monkeypatch.setattr(
        deletion_service,
        "create_deletion_request",
        lambda **kwargs: deleted.append(kwargs["vmid"]) or SimpleNamespace(id=1),
    )
    monkeypatch.setattr(
        deletion_service, "enqueue_processing", lambda *, session, req: None
    )

    quick_practice.process_lifecycle()

    assert sorted(deleted) == [9300, 9301]
    db.expire_all()
    refreshed = db.get(QuickPracticeSession, practice_id)
    assert refreshed is not None
    assert refreshed.status == "reclaiming"
    assert refreshed.reclaimed_at is None
