"""重置重複入列檢查要能讀 ORM 回傳的 dict payload。

TaskRecord.payload 是 JSON 欄位，真實資料庫讀回來是 dict；
舊的字串形式仍要相容，壞掉的 payload 只跳過不拋錯。
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError
from app.models.task_record import TaskRecord, TaskRecordStatus
from app.services.resource import reset_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Session:
    def __init__(self, records: list[TaskRecord]) -> None:
        self.records = records
        self.rolled_back = False

    def exec(self, stmt: Any) -> Any:
        return SimpleNamespace(all=lambda: self.records)

    def add(self, obj: Any) -> None:
        """測試替身。"""

    def commit(self) -> None:
        """測試替身。"""

    def rollback(self) -> None:
        self.rolled_back = True


def _record(
    payload: Any, status: TaskRecordStatus = TaskRecordStatus.queued
) -> TaskRecord:
    return TaskRecord(
        task_type=reset_service.TASK_RESET,
        user_id=uuid.uuid4(),
        payload=payload,
        status=status,
    )


def test_dict_payload_is_detected() -> None:
    session = _Session([_record({"vmid": 101, "node": "pve1", "rtype": "qemu"})])

    assert reset_service._has_active_reset(session, 101) is True  # type: ignore[arg-type]
    assert reset_service._has_active_reset(session, 102) is False  # type: ignore[arg-type]


def test_legacy_string_payload_is_still_detected() -> None:
    session = _Session([_record(json.dumps({"vmid": 7}))])

    assert reset_service._has_active_reset(session, 7) is True  # type: ignore[arg-type]


def test_malformed_payloads_are_skipped_without_error() -> None:
    session = _Session(
        [
            _record({}),
            _record({"vmid": "x"}),
            _record("not json"),
            _record(json.dumps([7])),
            _record({"vmid": 9}, TaskRecordStatus.running),
        ]
    )

    assert reset_service._has_active_reset(session, 9) is True  # type: ignore[arg-type]
    assert reset_service._has_active_reset(session, 7) is False  # type: ignore[arg-type]


def test_start_reset_rejects_second_request_for_same_vmid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reset_service, "_has_init_snapshot", lambda *_: True)
    monkeypatch.setattr(reset_service.audit_service, "log_action", lambda **_: None)
    enqueued: list[Any] = []
    monkeypatch.setattr(
        reset_service, "enqueue_task_sync", lambda **kw: enqueued.append(kw)
    )
    session = _Session([_record({"vmid": 101, "node": "pve1", "rtype": "qemu"})])

    with pytest.raises(ConflictError):
        reset_service.start_reset(
            session,  # type: ignore[arg-type]
            vmid=101,
            resource_info={"node": "pve1", "type": "qemu"},
            user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert enqueued == []
    assert session.rolled_back is True
