"""刪除單不可刪到「之後拿到同一個 VMID」的新機器。

原本的機器被別的途徑刪掉後，刪除單的 resource_vmid 會被 SET NULL；
VMID 若又配給新機器，舊單只剩快照，執行時必須拒絕。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from app.models import DeletionRequest, DeletionRequestStatus
from app.services.resource import deletion_service


class _FirstResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Session:
    def __init__(self, resource: Any) -> None:
        self._resource = resource
        self.committed = 0

    def exec(self, _stmt: Any) -> _FirstResult:
        return _FirstResult(self._resource)

    def add(self, _obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.committed += 1


def _request(*, resource_vmid: int | None, created_at: datetime) -> DeletionRequest:
    return DeletionRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        vmid=150,
        resource_vmid=resource_vmid,
        name="ct-150",
        node="pve1",
        resource_type="lxc",
        status=DeletionRequestStatus.running,
        created_at=created_at,
    )


def test_refuses_when_vmid_now_belongs_to_newer_resource() -> None:
    requested_at = datetime.now(timezone.utc) - timedelta(hours=1)
    req = _request(resource_vmid=None, created_at=requested_at)
    newer = SimpleNamespace(
        vmid=150,
        user_id=req.user_id,  # 同一個人的新機器也不能被舊單刪掉
        created_at=requested_at + timedelta(minutes=30),
    )

    deletion_service._execute_deletion(_Session(newer), req)

    assert req.status == DeletionRequestStatus.failed
    assert "different resource" in (req.error_message or "")

