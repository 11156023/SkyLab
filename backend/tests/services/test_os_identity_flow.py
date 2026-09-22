"""Guest OS 身份整合測試：machine_context 單欄位輸出與 Windows 擋板。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import script_executor_service
from app.ai.teacher_judge.machine_context import (
    format_machine_context,
    machine_context_entries,
)
from app.ai.teacher_judge.script_run_service import (
    _ensure_linux_executor_capability as ensure_linux_capability_http,
)
from app.models import (
    Resource,
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    User,
    UserRole,
)

_UBUNTU_OS = {
    "family": "linux",
    "id": "ubuntu",
    "version": "24.04",
    "pretty_name": "Ubuntu 24.04 LTS",
    "source": "qemu_guest_agent",
    "confidence": "high",
    "detected_at": "2026-09-19T00:00:00+00:00",
}

_DEBIAN_LXC_OS = {
    "family": "linux",
    "id": "debian",
    "version": None,
    "pretty_name": None,
    "source": "pve_ostype",
    "confidence": "medium",
    "detected_at": "2026-09-19T00:00:00+00:00",
}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,  # type: ignore[arg-type]
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassStudent.__table__,  # type: ignore[arg-type]
            TeachingClassMachineNode.__table__,  # type: ignore[arg-type]
            TeachingClassStudentMachine.__table__,  # type: ignore[arg-type]
            Resource.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


def _user(db: Session) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=UserRole.student,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _class_with_node(
    db: Session,
    owner: User,
    student: User,
    *,
    node_key: str,
    resource_type: str,
    vmid: int | None,
    guest_os: dict | None,
) -> uuid.UUID:
    teaching_class = TeachingClass(
        owner_id=owner.id,
        name=f"Class-{node_key}",
        code=f"CS-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 31),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
    )
    db.add(teaching_class)
    db.commit()
    db.refresh(teaching_class)
    db.add(TeachingClassStudent(class_id=teaching_class.id, user_id=student.id))
    db.commit()
    node = TeachingClassMachineNode(
        class_id=teaching_class.id,
        node_key=node_key,
        name=node_key.title(),
        role="student",
        resource_type=resource_type,
        cpu=2,
        memory_mb=2048,
        disk_gb=20,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    if vmid is not None:
        resource = Resource(
            vmid=vmid,
            user_id=student.id,
            teaching_class_id=teaching_class.id,
            environment_type="vm",
            created_at=datetime.now(UTC),
            guest_os=guest_os,
        )
        db.add(resource)
        db.commit()
        enrollment = db.exec(
            TeachingClassStudent.__table__.select().where(  # type: ignore[attr-defined]
                TeachingClassStudent.class_id == teaching_class.id,
                TeachingClassStudent.user_id == student.id,
            )
        ).first()
        assert enrollment is not None
        db.add(
            TeachingClassStudentMachine(
                class_student_id=enrollment.id,
                machine_node_id=node.id,
                vmid=vmid,
                status="completed",
            )
        )
        db.commit()
    return teaching_class.id


def test_machine_context_os_token_comes_from_resource_guest_os(
    db: Session,
) -> None:
    owner = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=UserRole.teacher,
    )
    student = _user(db)
    db.add(owner)
    db.commit()
    class_id = _class_with_node(
        db,
        owner,
        student,
        node_key="web",
        resource_type="qemu",
        vmid=601,
        guest_os=_UBUNTU_OS,
    )
    entries = machine_context_entries(db, class_id)
    assert len(entries) == 1
    assert entries[0]["os"] == "linux/ubuntu 24.04 (high)"
    context = format_machine_context(entries)
    assert "os=linux/ubuntu 24.04 (high)" in context
    # 不洩漏 VMID / IP
    assert "601" not in context
    assert "192.168." not in context


def test_machine_context_node_without_identity_is_unknown(db: Session) -> None:
    owner = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=UserRole.teacher,
    )
    student = _user(db)
    db.add(owner)
    db.commit()
    class_id = _class_with_node(
        db,
        owner,
        student,
        node_key="web",
        resource_type="qemu",
        vmid=None,
        guest_os=None,
    )
    entries = machine_context_entries(db, class_id)
    assert entries[0]["os"] == "unknown"


def test_machine_context_lxc_ostype_identity(db: Session) -> None:
    owner = User(
        email=f"{uuid.uuid4().hex[:12]}@example.com",
        hashed_password="x",
        role=UserRole.teacher,
    )
    student = _user(db)
    db.add(owner)
    db.commit()
    class_id = _class_with_node(
        db,
        owner,
        student,
        node_key="db",
        resource_type="lxc",
        vmid=701,
        guest_os=_DEBIAN_LXC_OS,
    )
    entries = machine_context_entries(db, class_id)
    assert entries[0]["os"] == "linux/debian (medium)"


def _resource_like(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "vmid": 101,
        "os_info": None,
        "environment_type": None,
        "guest_os": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_windows_rejection_uses_structured_identity() -> None:
    resource = _resource_like(
        guest_os={
            "family": "windows",
            "id": "mswindows",
            "version": "11",
            "pretty_name": "Windows 11",
            "source": "qemu_guest_agent",
            "confidence": "high",
        },
        # 即使 os_info 是 Linux 字串，結構化身份仍優先
        os_info="Ubuntu 24.04",
    )
    with pytest.raises(HTTPException) as exc:
        ensure_linux_capability_http(resource, 101)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "teacher_judge_unsupported_os"

    with pytest.raises(script_executor_service.TargetExecutionError) as exc2:
        script_executor_service._ensure_linux_executor_capability(resource, 101)
    assert exc2.value.reason_code == "unsupported_os"


def test_linux_identity_passes_and_legacy_fallback_still_blocks() -> None:
    linux_resource = _resource_like(
        os_info="Ubuntu 24.04",
        guest_os={"family": "linux", "id": "ubuntu"},
    )
    ensure_linux_capability_http(linux_resource, 101)
    script_executor_service._ensure_linux_executor_capability(linux_resource, 101)

    legacy_windows = _resource_like(os_info="Windows Server 2022", guest_os=None)
    with pytest.raises(HTTPException):
        ensure_linux_capability_http(legacy_windows, 101)
