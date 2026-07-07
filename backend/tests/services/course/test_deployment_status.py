"""deployment_service 狀態推導單元測試（純物件，不需 DB）。"""

import uuid
from datetime import UTC, datetime, timedelta

from app.models import (
    CourseDeployment,
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
)
from app.services.course.deployment_service import _derive_status

NOW = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)


def _deployment(expires_delta_hours: float = 3.0) -> CourseDeployment:
    return CourseDeployment(
        room_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        vm_request_id=uuid.uuid4(),
        created_at=NOW,
        expires_at=NOW + timedelta(hours=expires_delta_hours),
    )


def _vm_request(**overrides) -> VMRequest:
    defaults = dict(
        user_id=uuid.uuid4(),
        reason="Course lab deployment: test",
        resource_type="lxc",
        hostname="course-abc-def",
        password="x",
        status=VMRequestStatus.approved,
        migration_status=VMMigrationStatus.idle,
        vmid=None,
        migration_error=None,
        created_at=NOW,
    )
    defaults.update(overrides)
    return VMRequest(**defaults)


def test_provisioning_before_vmid_assigned():
    assert (
        _derive_status(_deployment(), _vm_request(), now=NOW) == "provisioning"
    )


def test_running_once_vmid_assigned():
    req = _vm_request(vmid=12345)
    assert _derive_status(_deployment(), req, now=NOW) == "running"


def test_failed_when_migration_failed_without_vmid():
    req = _vm_request(
        migration_status=VMMigrationStatus.failed,
        migration_error="Failed to plan provisioning",
    )
    assert _derive_status(_deployment(), req, now=NOW) == "failed"


def test_running_survives_failed_migration_after_provision():
    """provision 成功後的遷移失敗不是部署失敗——機器仍在跑。"""
    req = _vm_request(vmid=12345, migration_status=VMMigrationStatus.failed)
    assert _derive_status(_deployment(), req, now=NOW) == "running"


def test_expired_when_ttl_passed():
    req = _vm_request(vmid=12345)
    dep = _deployment(expires_delta_hours=-0.5)
    assert _derive_status(dep, req, now=NOW) == "expired"


def test_cancelled_request_is_failed():
    req = _vm_request(status=VMRequestStatus.cancelled)
    assert _derive_status(_deployment(), req, now=NOW) == "failed"


def test_naive_expires_at_treated_as_utc():
    dep = _deployment()
    dep.expires_at = dep.expires_at.replace(tzinfo=None) - timedelta(hours=9)
    req = _vm_request(vmid=1)
    assert _derive_status(dep, req, now=NOW) == "expired"
