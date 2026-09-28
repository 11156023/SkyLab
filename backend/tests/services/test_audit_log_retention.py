"""回歸：刪除資源不能連帶清掉該 VMID 的操作紀錄。

audit_logs.resource_vmid 的外鍵是 ON DELETE SET NULL，資源刪掉後紀錄只會
解除連結、保留下來；VMID 被回收前前任擁有者的紀錄更不能被一起刪掉。
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import AuditAction, AuditLog, Resource, User, UserRole
from app.services.resource import resource_service


def _free_vmid(session: Session) -> int:
    while True:
        vmid = random.randint(900_000, 999_999)
        taken = session.exec(select(AuditLog).where(AuditLog.vmid == vmid)).first()
        if taken is None and session.get(Resource, vmid) is None:
            return vmid


def test_orphan_cleanup_keeps_audit_rows_for_vmid(db: Session) -> None:
    owner = User(
        email=f"test-b17-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="x",
        role=UserRole.student,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)

    vmid = _free_vmid(db)
    db.add(
        Resource(
            vmid=vmid,
            user_id=owner.id,
            environment_type="b17",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    linked = AuditLog(
        user_id=owner.id,
        vmid=vmid,
        resource_vmid=vmid,
        action=AuditAction.resource_start,
        details="b17 linked row",
        created_at=datetime.now(UTC),
    )
    # 前任擁有者留下的紀錄：當時那台已刪除，resource_vmid 早就是 NULL
    previous_owner = AuditLog(
        user_id=None,
        vmid=vmid,
        resource_vmid=None,
        action=AuditAction.vm_create,
        details="b17 earlier owner row",
        created_at=datetime.now(UTC),
    )
    db.add(linked)
    db.add(previous_owner)
    db.commit()
    linked_id, previous_id = linked.id, previous_owner.id

    resource_service.delete_orphan_db_record(
        session=db, vmid=vmid, user_id=owner.id
    )
    db.expire_all()

    assert db.get(Resource, vmid) is None

    kept_linked = db.get(AuditLog, linked_id)
    kept_previous = db.get(AuditLog, previous_id)
    assert kept_linked is not None
    assert kept_linked.resource_vmid is None
    assert kept_linked.vmid == vmid
    assert kept_previous is not None
    assert kept_previous.vmid == vmid

    delete_rows = db.exec(
        select(AuditLog).where(
            AuditLog.vmid == vmid,
            AuditLog.action == AuditAction.resource_delete,
        )
    ).all()
    assert len(delete_rows) == 1
