from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine

from app.api.routes.courses import list_practice_machines
from app.models.course import CoursePath, CoursePathStatus
from app.models.teaching_class import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_student_gets_every_assigned_course_machine_in_role_order(monkeypatch) -> None:
    session = _session()
    teacher_id = uuid.uuid4()
    student_id = uuid.uuid4()
    teaching_class = TeachingClass(
        name="Linux A 班",
        code="linux-a",
        term="2026-1",
        owner_id=teacher_id,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 12, 31),
        weekday=1,
        start_time=time(9),
        end_time=time(11),
    )
    path = CoursePath(
        title="Linux",
        status=CoursePathStatus.published,
        created_by=teacher_id,
        teaching_class_id=teaching_class.id,
    )
    session.add(path)
    session.add(teaching_class)
    session.commit()

    enrollment = TeachingClassStudent(
        class_id=teaching_class.id,
        user_id=student_id,
    )
    main_node = TeachingClassMachineNode(
        class_id=teaching_class.id,
        node_key="main",
        name="操作主機",
        role="主要練習機",
        resource_type="qemu",
        cpu=2,
        memory_mb=2048,
        disk_gb=20,
        sort_order=1,
    )
    database_node = TeachingClassMachineNode(
        class_id=teaching_class.id,
        node_key="database",
        name="資料庫主機",
        role="資料庫驗證",
        resource_type="lxc",
        cpu=1,
        memory_mb=1024,
        disk_gb=10,
        sort_order=2,
    )
    session.add(enrollment)
    session.add(main_node)
    session.add(database_node)
    session.commit()
    session.add(
        TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=main_node.id,
            vmid=218,
            status="completed",
        )
    )
    session.add(
        TeachingClassStudentMachine(
            class_student_id=enrollment.id,
            machine_node_id=database_node.id,
            vmid=None,
            status="pending",
        )
    )
    session.commit()
    monkeypatch.setattr(
        "app.api.routes.courses.course_publication_service.public_urls_by_vmid",
        lambda _session, vmids: {218: "https://linux-a-sabc-main.lab.example.edu"}
        if 218 in vmids
        else {},
    )

    machines = list_practice_machines(
        session=session,
        current_user=SimpleNamespace(id=student_id),  # type: ignore[arg-type]
        path_id=path.id,
    )

    assert [machine.name for machine in machines] == ["操作主機", "資料庫主機"]
    assert machines[0].vmid == 218
    assert machines[0].public_url == "https://linux-a-sabc-main.lab.example.edu"
    assert machines[1].vmid is None
    assert machines[1].status == "pending"
