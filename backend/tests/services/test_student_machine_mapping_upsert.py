"""upsert_student_machine_mapping：只 add、不 flush/commit，缺列時建立。

對應列只記「哪個 batch task 建了這台」；vmid／狀態／錯誤都從 task 讀。
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.models import (
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    TeachingClassStudentMachine,
)
from app.services.teaching.student_machine_mapping import (
    upsert_student_machine_mapping,
)


def _session_returning(existing: TeachingClassStudentMachine | None) -> MagicMock:
    session = MagicMock()
    session.exec.return_value.first.return_value = existing
    return session


def test_creates_row_when_missing_without_flush_or_commit() -> None:
    session = _session_returning(None)
    enrollment_id, node_id, task_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    row = upsert_student_machine_mapping(
        session,
        enrollment_id=enrollment_id,
        node_id=node_id,
        task_id=task_id,
    )

    assert row.class_student_id == enrollment_id
    assert row.machine_node_id == node_id
    assert row.batch_task_id == task_id
    session.add.assert_called_once_with(row)
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_updates_existing_row_in_place() -> None:
    existing = TeachingClassStudentMachine(
        class_student_id=uuid.uuid4(),
        machine_node_id=uuid.uuid4(),
        batch_task_id=uuid.uuid4(),
    )
    session = _session_returning(existing)
    task_id = uuid.uuid4()

    row = upsert_student_machine_mapping(
        session,
        enrollment_id=existing.class_student_id,
        node_id=existing.machine_node_id,
        task_id=task_id,
    )

    assert row is existing
    assert row.batch_task_id == task_id
    session.add.assert_called_once_with(existing)
    session.commit.assert_not_called()


def _task(*, status: BatchProvisionTaskStatus, vmid: int | None) -> BatchProvisionTask:
    return BatchProvisionTask(
        job_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        member_index=0,
        status=status,
        vmid=vmid,
        error="boom" if status == BatchProvisionTaskStatus.failed else None,
    )


def test_machine_state_is_read_from_its_batch_task() -> None:
    row = TeachingClassStudentMachine(
        class_student_id=uuid.uuid4(), machine_node_id=uuid.uuid4()
    )
    assert (row.vmid, row.status, row.error) == (None, "pending", None)

    row.batch_task = _task(status=BatchProvisionTaskStatus.completed, vmid=123)
    assert (row.vmid, row.status, row.error) == (123, "completed", None)

    row.batch_task = _task(status=BatchProvisionTaskStatus.failed, vmid=None)
    assert (row.vmid, row.status, row.error) == (None, "failed", "boom")


def test_completed_task_without_vmid_reads_as_reclaimed() -> None:
    """機器刪掉後 task 的 vmid 會被清空（FK SET NULL），對應就是已回收。"""
    row = TeachingClassStudentMachine(
        class_student_id=uuid.uuid4(), machine_node_id=uuid.uuid4()
    )
    row.batch_task = _task(status=BatchProvisionTaskStatus.completed, vmid=None)
    assert (row.vmid, row.status) == (None, "reclaimed")
