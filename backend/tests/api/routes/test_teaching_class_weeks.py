"""每週教材的存檔規則：檔案只能以 id 指定，移除時連磁碟檔一起清掉。

以前 ``PUT /weeks`` 直接把 client 送來的 ``storage_key`` 寫進資料庫，等於
任何老師都可以把別的班級的檔案掛進自己的週次；而且整批刪掉重建的寫法
會把磁碟上的舊檔留在原地。
"""

import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.routes import teaching_classes as routes
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    TeachingClass,
    TeachingClassStatus,
    TeachingClassTaskFile,
    TeachingClassWeek,
)

TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassWeek.__table__,  # type: ignore[arg-type]
            TeachingClassTaskFile.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _task_file_root(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "TASK_FILE_ROOT", tmp_path)
    monkeypatch.setattr(routes, "_serialize", lambda _session, item: {"id": str(item.id)})
    return tmp_path


def _class(db: Session, *, status=TeachingClassStatus.active) -> TeachingClass:
    item = TeachingClass(
        owner_id=TEACHER.id,
        name="Linux",
        code=f"cls-{uuid.uuid4().hex[:8]}",
        term="115-1",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 15),
        weekday=1,
        start_time=time(13, 10),
        end_time=time(16, 0),
        status=status,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _week(db: Session, item: TeachingClass, number: int, day: date) -> TeachingClassWeek:
    week = TeachingClassWeek(class_id=item.id, week_number=number, session_date=day)
    db.add(week)
    db.commit()
    db.refresh(week)
    return week


def _file(db: Session, week: TeachingClassWeek, root, name: str) -> TeachingClassTaskFile:
    storage_key = f"{uuid.uuid4().hex}.task"
    (root / storage_key).write_bytes(b"lab")
    row = TeachingClassTaskFile(
        week_id=week.id, filename=name, storage_key=storage_key
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _body(week: TeachingClassWeek, *, files=(), title="Linux 權限"):
    return [
        routes.WeekIn(
            week_number=week.week_number,
            session_date=week.session_date,
            title=title,
            status="published",
            files=[routes.WeekFileIn(id=file_id) for file_id in files],
        )
    ]


def test_week_content_is_updated_in_place_and_keeps_the_file(db, _task_file_root):
    item = _class(db)
    week = _week(db, item, 1, date(2026, 9, 8))
    task_file = _file(db, week, _task_file_root, "lab1.pdf")

    routes.replace_weeks(item.id, _body(week, files=[task_file.id]), db, TEACHER)

    rows = list(db.exec(select(TeachingClassWeek)).all())
    assert [(row.id, row.title) for row in rows] == [(week.id, "Linux 權限")]
    stored = db.exec(select(TeachingClassTaskFile)).one()
    assert stored.id == task_file.id
    assert (_task_file_root / stored.storage_key).exists()


def test_dropping_a_file_from_the_week_also_removes_it_from_disk(db, _task_file_root):
    item = _class(db)
    week = _week(db, item, 1, date(2026, 9, 8))
    task_file = _file(db, week, _task_file_root, "lab1.pdf")
    blob = _task_file_root / task_file.storage_key

    routes.replace_weeks(item.id, _body(week), db, TEACHER)

    assert db.exec(select(TeachingClassTaskFile)).all() == []
    assert not blob.exists()


def test_a_file_from_another_class_cannot_be_attached(db, _task_file_root):
    item = _class(db)
    week = _week(db, item, 1, date(2026, 9, 8))
    other = _class(db)
    other_week = _week(db, other, 1, date(2026, 9, 8))
    stolen = _file(db, other_week, _task_file_root, "secret.pdf")

    with pytest.raises(NotFoundError):
        routes.replace_weeks(item.id, _body(week, files=[stolen.id]), db, TEACHER)


def test_week_dates_must_still_match_the_schedule(db, _task_file_root):
    item = _class(db)
    week = _week(db, item, 1, date(2026, 9, 8))
    body = _body(week)
    body[0].session_date = date(2026, 9, 9)

    with pytest.raises(BadRequestError):
        routes.replace_weeks(item.id, body, db, TEACHER)


def test_client_cannot_choose_the_storage_key():
    """WeekFileIn 只認 id；storage_key 連欄位都不存在。"""
    file_in = routes.WeekFileIn.model_validate(
        {"id": str(uuid.uuid4()), "storage_key": "../../.env", "filename": "x.pdf"}
    )
    assert not hasattr(file_in, "storage_key")
