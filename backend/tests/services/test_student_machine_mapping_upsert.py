"""upsert_student_machine_mapping：只 add、不 flush/commit，缺列時建立。"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.models import TeachingClassStudentMachine
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
        vmid=123,
        status="completed",
    )

    assert row.class_student_id == enrollment_id
    assert row.machine_node_id == node_id
    assert row.batch_task_id == task_id
    assert row.vmid == 123
    assert row.status == "completed"
    assert row.error is None
    session.add.assert_called_once_with(row)
    session.flush.assert_not_called()
    session.commit.assert_not_called()


def test_updates_existing_row_in_place() -> None:
    existing = TeachingClassStudentMachine(
        class_student_id=uuid.uuid4(),
        machine_node_id=uuid.uuid4(),
        vmid=100,
        status="completed",
    )
    session = _session_returning(existing)
    task_id = uuid.uuid4()

    row = upsert_student_machine_mapping(
        session,
        enrollment_id=existing.class_student_id,
        node_id=existing.machine_node_id,
        task_id=task_id,
        vmid=None,
        status="failed",
        error="boom",
    )

    assert row is existing
    assert row.batch_task_id == task_id
    assert row.vmid is None
    assert row.status == "failed"
    assert row.error == "boom"
    session.add.assert_called_once_with(existing)
    session.commit.assert_not_called()
