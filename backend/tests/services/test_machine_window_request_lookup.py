"""查機器使用時段要看機器自己的來源申請，不能拿到同 VMID 已結案的舊申請。"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.domain.resource_markers import RESOURCE_DELETED_BY_USER_MARKER
from app.models import (
    Resource,
    User,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
)
from app.repositories import vm_request as vm_request_repo
from app.services.resource import resource_service


def _db_fixture_available() -> bool:
    from tests.conftest import _assert_safe_pytest_database_target

    try:
        _assert_safe_pytest_database_target()
    except RuntimeError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_fixture_available(),
    reason="需要測試用資料庫（見 tests/conftest.py 的 _assert_safe_pytest_database_target）",
)

NOW = datetime.now(UTC)


def _user_id(db: Session) -> uuid.UUID:
    return db.exec(select(User).where(User.email == settings.FIRST_SUPERUSER)).one().id


def _vmid() -> int:
    return random.randint(800_000, 899_999)


def _request(
    user_id: uuid.UUID,
    vmid: int,
    *,
    reviewed_at: datetime,
    start_at: datetime,
    end_at: datetime,
    resource_warning: str | None = None,
) -> VMRequest:
    return VMRequest(
        user_id=user_id,
        reason="window lookup",
        resource_type="lxc",
        hostname=f"win-{uuid.uuid4().hex[:8]}",
        password="encrypted-placeholder",
        status=VMRequestStatus.approved,
        provisioning_status=(
            VMProvisioningStatus.failed
            if resource_warning
            else VMProvisioningStatus.completed
        ),
        resource_warning=resource_warning,
        vmid=vmid,
        start_at=start_at,
        end_at=end_at,
        reviewed_at=reviewed_at,
        created_at=reviewed_at - timedelta(hours=1),
    )


def test_machine_uses_its_own_source_request(db: Session) -> None:
    user_id = _user_id(db)
    vmid = _vmid()
    own = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=5),
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=1),
    )
    # 同 VMID 較晚核准、但已結案的舊申請（前一台機器已刪）
    closed = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=1),
        start_at=NOW - timedelta(days=3),
        end_at=NOW - timedelta(days=2),
        resource_warning=RESOURCE_DELETED_BY_USER_MARKER,
    )
    try:
        db.add_all([own, closed])
        db.flush()
        resource = Resource(
            vmid=vmid,
            user_id=user_id,
            environment_type="Test",
            request_id=own.id,
            created_at=NOW,
        )
        db.add(resource)
        db.flush()

        picked = vm_request_repo.get_window_request_for_vmid(
            session=db, vmid=vmid, source_request_id=own.id
        )
        assert picked is not None and picked.id == own.id

        reason, start_at, end_at = resource_service.start_window_state(
            session=db, vmid=vmid, db_resource=resource
        )
        assert reason is None
        assert end_at is not None and end_at > NOW
    finally:
        db.rollback()


def test_without_source_request_closed_requests_are_skipped(db: Session) -> None:
    user_id = _user_id(db)
    vmid = _vmid()
    older_open = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=10),
        start_at=NOW - timedelta(days=9),
        end_at=NOW - timedelta(days=8),
    )
    newer_closed = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=1),
        start_at=NOW + timedelta(days=1),
        end_at=NOW + timedelta(days=2),
        resource_warning=RESOURCE_DELETED_BY_USER_MARKER,
    )
    try:
        db.add_all([older_open, newer_closed])
        db.flush()

        picked = vm_request_repo.get_window_request_for_vmid(session=db, vmid=vmid)
        assert picked is not None and picked.id == older_open.id

        batch = vm_request_repo.get_latest_approved_vm_requests_by_vmids(
            session=db, vmids=[vmid], skip_closed=True
        )
        assert batch[vmid].id == older_open.id
        # 其他呼叫端的預設行為不變
        legacy = vm_request_repo.get_latest_approved_vm_requests_by_vmids(
            session=db, vmids=[vmid]
        )
        assert legacy[vmid].id == newer_closed.id
    finally:
        db.rollback()


def test_only_closed_requests_means_no_window(db: Session) -> None:
    user_id = _user_id(db)
    vmid = _vmid()
    closed = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=1),
        start_at=NOW + timedelta(days=1),
        end_at=NOW + timedelta(days=2),
        resource_warning=RESOURCE_DELETED_BY_USER_MARKER,
    )
    try:
        db.add(closed)
        db.flush()
        resource = Resource(
            vmid=vmid, user_id=user_id, environment_type="Test", created_at=NOW
        )
        db.add(resource)
        db.flush()

        assert vm_request_repo.get_window_request_for_vmid(session=db, vmid=vmid) is None
        # 新機器（無來源申請）沿用 VMID 時，不能被舊申請的「時段未開始」擋下
        assert resource_service.start_window_state(
            session=db, vmid=vmid, db_resource=resource
        ) == (None, None, None)
    finally:
        db.rollback()


def test_source_request_pointing_elsewhere_falls_back_to_lookup(db: Session) -> None:
    user_id = _user_id(db)
    vmid = _vmid()
    other = _request(
        user_id,
        vmid + 1,
        reviewed_at=NOW - timedelta(days=1),
        start_at=NOW - timedelta(days=1),
        end_at=NOW + timedelta(days=1),
    )
    own_by_vmid = _request(
        user_id,
        vmid,
        reviewed_at=NOW - timedelta(days=2),
        start_at=NOW - timedelta(days=2),
        end_at=NOW + timedelta(days=2),
    )
    try:
        db.add_all([other, own_by_vmid])
        db.flush()

        picked = vm_request_repo.get_window_request_for_vmid(
            session=db, vmid=vmid, source_request_id=other.id
        )
        assert picked is not None and picked.id == own_by_vmid.id
    finally:
        db.rollback()
