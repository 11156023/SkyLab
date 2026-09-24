"""刪除請求改走 arq：只對 pending 單入列，取消不再依賴行程內 runner。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import DeletionRequest, DeletionRequestStatus
from app.services.resource import deletion_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


def _request(status: DeletionRequestStatus) -> DeletionRequest:
    return DeletionRequest(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        vmid=150,
        name="ct-150",
        node="pve1",
        resource_type="lxc",
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def test_enqueue_processing_enqueues_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_enqueue(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(deletion_service, "enqueue_task_sync", fake_enqueue)
    req = _request(DeletionRequestStatus.pending)

    deletion_service.enqueue_processing(session=object(), req=req)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0]["task_type"] == deletion_service.TASK_DELETE
    assert calls[0]["user_id"] == req.user_id
    assert calls[0]["payload"] == {"request_id": str(req.id), "vmid": 150}


def test_enqueue_processing_skips_deduplicated_running_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        deletion_service, "enqueue_task_sync", lambda **kwargs: calls.append(kwargs)
    )

    deletion_service.enqueue_processing(
        session=object(),  # type: ignore[arg-type]
        req=_request(DeletionRequestStatus.running),
    )

    assert calls == []


def test_run_delete_task_unpacks_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(deletion_service, "process_one_request", seen.append)
    request_id = uuid.uuid4()

    result = deletion_service.run_delete_task(
        uuid.uuid4(), {"request_id": str(request_id), "vmid": 150}
    )

    assert seen == [request_id]
    assert result == {"request_id": str(request_id), "vmid": 150}

