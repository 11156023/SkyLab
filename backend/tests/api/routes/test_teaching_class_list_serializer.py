"""班級列表只回摘要，而且查詢次數不隨班級數／週次數成長。

原本 list 對每一班跑一次 _serialize：每週一次檔案查詢、每班一次容量預估
（會把整張 IP 表撈進記憶體），十幾班就足以讓首屏卡住好幾秒。這裡固定住
兩件事：欄位仍夠列表頁算出進度與設定完成度，以及查詢次數是常數。
"""

import uuid
from datetime import date, time

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, col, create_engine, select

from app.api.routes.teaching_classes import _serialize_list
from app.models import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassWeek,
)


@pytest.fixture(name="engine")
def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="session")
def _session(engine):
    with Session(engine) as session:
        yield session


def _make_class(
    session,
    *,
    name: str,
    weeks: int,
    nodes: int,
    students: int,
    ready: int,
) -> TeachingClass:
    item = TeachingClass(
        owner_id=uuid.uuid4(),
        name=name,
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="114-1",
        start_date=date(2026, 9, 1),
        end_date=date(2027, 1, 15),
        weekday=2,
        start_time=time(13, 20),
        end_time=time(16, 10),
        status=TeachingClassStatus.active,
    )
    session.add(item)
    session.flush()

    for index in range(weeks):
        session.add(
            TeachingClassWeek(
                class_id=item.id,
                week_number=index + 1,
                session_date=date(2026, 9, 2),
                title=f"第 {index + 1} 週",
            )
        )

    node_rows = []
    for index in range(nodes):
        node = TeachingClassMachineNode(
            class_id=item.id,
            node_key=f"n{index}",
            name=f"node-{index}",
            role="target",
            resource_type="lxc",
            cpu=2,
            memory_mb=2048,
            disk_gb=8,
            sort_order=index,
        )
        session.add(node)
        node_rows.append(node)
    session.flush()

    remaining_ready = ready
    for _ in range(students):
        enrollment = TeachingClassStudent(class_id=item.id, user_id=uuid.uuid4())
        session.add(enrollment)
        session.flush()
        for node in node_rows:
            done = remaining_ready > 0
            if done:
                remaining_ready -= 1
            session.add(
                TeachingClassStudentMachine(
                    class_student_id=enrollment.id,
                    machine_node_id=node.id,
                    vmid=1000 + remaining_ready if done else None,
                    status="completed" if done else "pending",
                )
            )
    session.flush()
    return item


def test_summary_fields_cover_the_list_page(session):
    item = _make_class(
        session, name="雲端概論", weeks=3, nodes=2, students=4, ready=5
    )

    (row,) = _serialize_list(session, [item])

    assert row["name"] == "雲端概論"
    assert row["member_count"] == 4
    assert len(row["machine_nodes"]) == 2
    assert [week["title"] for week in row["weeks"]] == [
        "第 1 週",
        "第 2 週",
        "第 3 週",
    ]
    assert row["ready_machines"] == 5
    assert row["total_machines"] == 8
    assert row["course_environment"] is None


def test_empty_class_reports_zeroes_not_missing_keys(session):
    item = _make_class(session, name="空班", weeks=0, nodes=0, students=0, ready=0)

    (row,) = _serialize_list(session, [item])

    assert row["member_count"] == 0
    assert row["machine_nodes"] == []
    assert row["weeks"] == []
    assert row["ready_machines"] == 0
    assert row["total_machines"] == 0


def test_counts_stay_scoped_to_each_class(session):
    first = _make_class(session, name="A 班", weeks=2, nodes=1, students=3, ready=2)
    second = _make_class(session, name="B 班", weeks=5, nodes=2, students=1, ready=1)

    rows = {row["name"]: row for row in _serialize_list(session, [first, second])}

    assert rows["A 班"]["member_count"] == 3
    assert rows["A 班"]["ready_machines"] == 2
    assert rows["A 班"]["total_machines"] == 3
    assert len(rows["A 班"]["weeks"]) == 2
    assert rows["B 班"]["member_count"] == 1
    assert rows["B 班"]["ready_machines"] == 1
    assert rows["B 班"]["total_machines"] == 2
    assert len(rows["B 班"]["weeks"]) == 5


def test_query_count_does_not_grow_with_classes_or_weeks(session, engine):
    for index in range(2):
        _make_class(session, name=f"少-{index}", weeks=2, nodes=1, students=2, ready=1)
    for index in range(12):
        _make_class(
            session, name=f"多-{index}", weeks=18, nodes=3, students=30, ready=40
        )
    session.commit()

    def _load(prefix: str) -> list[TeachingClass]:
        # 比照 list_classes：物件都是剛從 DB 撈出來的，屬性已載入。
        return list(
            session.exec(
                select(TeachingClass).where(
                    col(TeachingClass.name).like(f"{prefix}%")
                )
            ).all()
        )

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        few = _load("少-")
        many = _load("多-")
        statements.clear()
        _serialize_list(session, few)
        few_count = len(statements)
        statements.clear()
        _serialize_list(session, many)
        many_count = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert few_count == many_count, (
        f"查詢次數隨資料量成長：2 班/2 週用了 {few_count} 次，"
        f"12 班/18 週用了 {many_count} 次"
    )
    assert many_count <= 5
