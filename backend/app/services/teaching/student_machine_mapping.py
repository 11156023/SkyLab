"""「這位學生的這台班級機器」對應列的寫入。

建機 worker（``batch_provision_service._sync_class_machine_mapping``）、
老師按 reconcile、以及重試時復原殘留機器三條路徑共用同一套 upsert。
本模組刻意只依賴 ``app.models``，讓 ``services/vm`` 與 ``services/teaching``
兩邊都能 import 而不形成循環。
"""

from __future__ import annotations

import uuid

from sqlmodel import Session, select

from app.models import TeachingClassStudentMachine


def upsert_student_machine_mapping(
    session: Session,
    *,
    enrollment_id: uuid.UUID,
    node_id: uuid.UUID,
    task_id: uuid.UUID,
    vmid: int | None,
    status: str,
    error: str | None = None,
) -> TeachingClassStudentMachine:
    """寫入（或建立）學生在某個機器節點上的對應；只 ``session.add``。

    不 flush、不 commit —— 交易邊界由呼叫端決定。狀態字串也由呼叫端推導。
    """
    mapping = session.exec(
        select(TeachingClassStudentMachine).where(
            TeachingClassStudentMachine.class_student_id == enrollment_id,
            TeachingClassStudentMachine.machine_node_id == node_id,
        )
    ).first()
    if mapping is None:
        mapping = TeachingClassStudentMachine(
            class_student_id=enrollment_id,
            machine_node_id=node_id,
        )
    mapping.batch_task_id = task_id
    mapping.vmid = vmid
    mapping.status = status
    mapping.error = error
    session.add(mapping)
    return mapping


__all__ = ["upsert_student_machine_mapping"]
