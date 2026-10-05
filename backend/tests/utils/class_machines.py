"""建立「學生的班級機器」測試資料。

機器的 vmid／狀態／錯誤屬於建它的 batch task，對應列只指向 task；
測試要有一台已建好的機器，就得連 job、task（以及 vmid 對應的 resource）一起建。
"""

from __future__ import annotations

from sqlmodel import Session

from app.models import (
    BatchProvisionJob,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
    Resource,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.base import get_datetime_utc


def add_student_machine(
    session: Session,
    *,
    enrollment: TeachingClassStudent,
    node: TeachingClassMachineNode,
    vmid: int | None = None,
    status: str = "completed",
    error: str | None = None,
) -> TeachingClassStudentMachine:
    """加一筆學生機器對應（含 job/task；vmid 沒有 resource 時補一筆）。只 flush。"""
    job = BatchProvisionJob(
        teaching_class_id=enrollment.class_id,
        resource_type=node.resource_type,
        hostname_prefix="test",
        template_params={},
        created_at=get_datetime_utc(),
    )
    session.add(job)
    session.flush()
    if vmid is not None and session.get(Resource, vmid) is None:
        session.add(
            Resource(
                vmid=vmid,
                user_id=enrollment.user_id,
                teaching_class_id=enrollment.class_id,
                allocation_scope="teaching_class",
                environment_type="teaching_class",
                created_at=get_datetime_utc(),
            )
        )
        session.flush()
    task = BatchProvisionTask(
        job_id=job.id,
        user_id=enrollment.user_id,
        member_index=0,
        vmid=vmid,
        status=BatchProvisionTaskStatus(status),
        error=error,
    )
    session.add(task)
    session.flush()
    machine = TeachingClassStudentMachine(
        class_student_id=enrollment.id,
        machine_node_id=node.id,
        batch_task_id=task.id,
    )
    session.add(machine)
    session.flush()
    return machine
