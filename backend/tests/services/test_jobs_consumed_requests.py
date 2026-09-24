"""被消耗申請單（資源刪除 / 轉範本）在 jobs 顯示層的轉譯測試。

mark_linked_request_consumed 會把申請單標成 provisioning failed + marker，
讓排程器停手；顯示層必須把它轉譯為「已結案」而非失敗，且不得累計
排程超時訊息。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.domain.resource_markers import (
    RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    RESOURCE_DELETED_BY_USER_MARKER,
)
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.schemas.jobs import JobStatus
from app.services.jobs import jobs_service


def make_request(**overrides: object) -> VMRequest:
    defaults: dict[str, object] = dict(
        user_id=uuid.uuid4(),
        reason="course lab",
        resource_type="vm",
        hostname="test-windows",
        password="pw",
        status=VMRequestStatus.approved,
        vmid=101,
        # start_at 早已過期：正常情況會觸發超時訊息
        start_at=datetime.now(timezone.utc) - timedelta(hours=41),
    )
    defaults.update(overrides)
    return VMRequest(**defaults)


def test_converted_to_template_shows_closed_not_failed() -> None:
    req = make_request(
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error=RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
        review_comment=RESOURCE_CONVERTED_TO_TEMPLATE_MARKER,
    )

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.completed
    assert item.message == "母機已轉為範本，申請單已結案"
    assert "超時" not in (item.message or "")
    assert item.meta["overdue"] is False
    assert item.meta["consumed"] is True


def test_deleted_by_user_shows_closed() -> None:
    req = make_request(
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error=RESOURCE_DELETED_BY_USER_MARKER,
    )

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.completed
    assert item.message == "資源已由使用者刪除，申請單已結案"
    assert item.meta["consumed"] is True


def test_real_provisioning_failure_stays_failed_with_overdue() -> None:
    req = make_request(
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error="clone failed: storage full",
        vmid=None,
    )

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.failed
    assert "storage full" in (item.message or "")
    assert "超時" in (item.message or "")
    assert item.meta["consumed"] is False


def test_consumed_message_helper_matches_only_markers() -> None:
    assert (
        jobs_service._consumed_request_message(
            make_request(provisioning_error="some real error")
        )
        is None
    )
    assert (
        jobs_service._consumed_request_message(
            make_request(review_comment=RESOURCE_CONVERTED_TO_TEMPLATE_MARKER)
        )
        == "母機已轉為範本，申請單已結案"
    )
