"""回歸測試：班級路由的時區驗證、建機送出失敗的收尾、CSV 匯入編碼、週次教材清檔。"""

import io
import uuid
from datetime import date, time
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

from app.api.routes import teaching_classes as routes
from app.exceptions import BadRequestError
from app.models import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassTaskFile,
    TeachingClassWeek,
    VMTemplate,
    VMTemplateStatus,
)
from app.services.teaching import class_lifecycle_service
from app.services.user import roster_csv

TEACHER = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="teacher")


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            TeachingClass.__table__,  # type: ignore[arg-type]
            TeachingClassMachineNode.__table__,  # type: ignore[arg-type]
            TeachingClassWeek.__table__,  # type: ignore[arg-type]
            TeachingClassTaskFile.__table__,  # type: ignore[arg-type]
            VMTemplate.__table__,  # type: ignore[arg-type]
        ],
    )
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _no_serialize(monkeypatch):
    monkeypatch.setattr(
        routes, "_serialize", lambda _session, item: {"id": str(item.id)}
    )


def _class(db: Session, **overrides) -> TeachingClass:
    fields = {
        "owner_id": TEACHER.id,
        "name": "Linux",
        "code": f"cls-{uuid.uuid4().hex[:8]}",
        "term": "115-1",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 15),
        "weekday": 1,
        "start_time": time(13, 10),
        "end_time": time(16, 0),
        "status": TeachingClassStatus.planning,
        "course_version_id": uuid.uuid4(),
    }
    fields.update(overrides)
    item = TeachingClass(**fields)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _node(
    db: Session, item: TeachingClass, key: str, **overrides
) -> TeachingClassMachineNode:
    fields = {
        "class_id": item.id,
        "node_key": key,
        "source_type": "custom",
        "custom_image_ref": "local:vztmpl/debian.tar.zst",
        "name": key,
        "role": "target",
        "resource_type": "lxc",
        "cpu": 1,
        "memory_mb": 512,
        "disk_gb": 8,
        "sort_order": 0,
    }
    fields.update(overrides)
    node = TeachingClassMachineNode(**fields)
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


# --- 時區必須是 IANA 名稱 ---------------------------------------------

_CREATE_BASE = {
    "name": "Linux",
    "term": "115-1",
    "start_date": "2026-09-01",
    "end_date": "2026-12-31",
    "weekday": 1,
    "start_time": "13:10",
    "end_time": "16:00",
}


@pytest.mark.parametrize("value", ["Taipei", "UTC+8", "Mars/Base", "../etc/passwd"])
def test_class_create_rejects_unknown_timezone(value: str) -> None:
    with pytest.raises(ValidationError):
        routes.ClassCreate.model_validate({**_CREATE_BASE, "timezone": value})


def test_class_create_accepts_iana_timezone_and_default() -> None:
    assert routes.ClassCreate.model_validate(_CREATE_BASE).timezone == "Asia/Taipei"
    body = routes.ClassCreate.model_validate(
        {**_CREATE_BASE, "timezone": "Europe/Berlin"}
    )
    assert body.timezone == "Europe/Berlin"


def test_class_patch_rejects_unknown_timezone_and_bad_lengths() -> None:
    with pytest.raises(ValidationError):
        routes.ClassPatch.model_validate({"timezone": "Mars/Base"})
    with pytest.raises(ValidationError):
        routes.ClassPatch.model_validate({"timezone": "A" * 65})
    with pytest.raises(ValidationError):
        routes.ClassPatch.model_validate({"name": ""})
    with pytest.raises(ValidationError):
        routes.ClassPatch.model_validate({"name": "x" * 256})
    with pytest.raises(ValidationError):
        routes.ClassPatch.model_validate({"term": "x" * 81})
    assert routes.ClassPatch.model_validate({}).timezone is None
    assert routes.ClassPatch.model_validate({"timezone": "Asia/Tokyo"}).timezone == (
        "Asia/Tokyo"
    )


# --- 送出批次 job 失敗時不能把班級鎖死 ---------------------------------


@pytest.fixture
def provision_env(monkeypatch):
    calls: dict[str, list] = {"reserve": [], "release": [], "submit": []}
    monkeypatch.setattr(
        routes, "_students", lambda _s, _cid: [SimpleNamespace(user_id=uuid.uuid4())]
    )
    monkeypatch.setattr(
        routes.class_capacity_service,
        "reserve",
        lambda _session, **kwargs: calls["reserve"].append(kwargs),
    )
    monkeypatch.setattr(
        routes.class_capacity_service,
        "release",
        lambda _session, **kwargs: calls["release"].append(kwargs) or 0,
    )
    return calls


def _submit_sequence(monkeypatch, calls, outcomes):
    outcomes = list(outcomes)

    def fake_submit(**kwargs):
        calls["submit"].append(kwargs["node"].node_key)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(routes.class_provision_service, "submit_node_job", fake_submit)


def test_first_submit_failure_releases_capacity_and_unlocks(
    db: Session, monkeypatch, provision_env
) -> None:
    item = _class(db)
    _node(db, item, "web")
    _submit_sequence(monkeypatch, provision_env, [BadRequestError("template busy")])

    with pytest.raises(BadRequestError):
        routes.provision_class(item.id, db, TEACHER)

    db.refresh(item)
    assert item.locked_at is None
    assert item.status == TeachingClassStatus.planning
    assert provision_env["release"] == [{"class_id": item.id}]

    # 解鎖之後可以直接重送
    job_id = uuid.uuid4()
    _submit_sequence(monkeypatch, provision_env, [job_id])
    routes.provision_class(item.id, db, TEACHER)
    db.refresh(item)
    assert item.status == TeachingClassStatus.pending_review
    assert item.locked_at is not None


def test_partial_submit_failure_hands_over_to_review(
    db: Session, monkeypatch, provision_env
) -> None:
    item = _class(db)
    first = _node(db, item, "web", sort_order=0)
    _node(db, item, "db", sort_order=1)
    job_id = uuid.uuid4()
    _submit_sequence(monkeypatch, provision_env, [job_id, RuntimeError("boom")])

    with pytest.raises(RuntimeError):
        routes.provision_class(item.id, db, TEACHER)

    db.refresh(item)
    db.refresh(first)
    assert first.batch_job_id == job_id
    # 已經有 job 了：交給審核／退回流程收尾，不在這裡放掉容量
    assert item.status == TeachingClassStatus.pending_review
    assert provision_env["release"] == []


def test_template_not_ready_is_rejected_before_any_side_effect(
    db: Session, monkeypatch, provision_env
) -> None:
    item = _class(db)
    template = VMTemplate(
        pve_vmid=9001,
        name="Ubuntu",
        node="pve",
        status=VMTemplateStatus.updating,
    )
    db.add(template)
    db.commit()
    _node(
        db,
        item,
        "web",
        source_type="template",
        source_template_id=template.id,
        custom_image_ref=None,
        resource_type="qemu",
    )
    _submit_sequence(monkeypatch, provision_env, [])

    with pytest.raises(BadRequestError):
        routes.provision_class(item.id, db, TEACHER)

    db.refresh(item)
    assert provision_env["reserve"] == []
    assert provision_env["submit"] == []
    assert item.locked_at is None


# --- CSV 匯入先試 UTF-8（含 BOM），再退回 cp950 -----------------------


@pytest.fixture
def captured_import(monkeypatch):
    captured: list[list[str]] = []
    item = SimpleNamespace(status=TeachingClassStatus.planning)
    monkeypatch.setattr(routes, "_get_class", lambda *_args: item)
    monkeypatch.setattr(
        routes,
        "add_students",
        lambda _cid, body, _session, _user: captured.append(body.emails) or {},
    )
    monkeypatch.setattr(
        roster_csv, "student_email_domain", lambda _session: "school.edu.tw"
    )
    return captured


def _upload(raw: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(raw), filename="students.csv")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbfemail\nstu1@x.edu\n",
        b"\xef\xbb\xbfstu1@x.edu\n",
    ],
)
async def test_utf8_bom_csv_keeps_the_first_cell(captured_import, raw: bytes) -> None:
    await routes.import_students(uuid.uuid4(), None, TEACHER, _upload(raw))
    assert captured_import == [["stu1@x.edu"]]


@pytest.mark.asyncio
async def test_big5_csv_still_decodes_and_skips_the_header(captured_import) -> None:
    raw = "學號\n10000001\n".encode("cp950")
    await routes.import_students(uuid.uuid4(), None, TEACHER, _upload(raw))
    assert captured_import == [["10000001@school.edu.tw"]]


@pytest.mark.asyncio
async def test_student_ids_need_a_configured_domain(
    captured_import, monkeypatch
) -> None:
    monkeypatch.setattr(roster_csv, "student_email_domain", lambda _session: None)
    with pytest.raises(BadRequestError) as caught:
        await routes.import_students(
            uuid.uuid4(), None, TEACHER, _upload(b"a@x.edu\n10000001\n")
        )
    assert "10000001" in caught.value.message
    assert "學號信箱網域" in caught.value.message
    assert captured_import == []


@pytest.mark.asyncio
async def test_csv_import_runs_db_work_off_the_event_loop(monkeypatch) -> None:
    """名單每筆都要查一次帳號：權限檢查與加入學生都不能在 event loop 上跑。"""
    import threading

    threads: list[threading.Thread] = []
    item = SimpleNamespace(status=TeachingClassStatus.planning)

    def fake_get_class(*_args):
        threads.append(threading.current_thread())
        return item

    def fake_add_students(_cid, _body, _session, _user):
        threads.append(threading.current_thread())
        return {}

    def fake_domain(_session):
        threads.append(threading.current_thread())
        return "school.edu.tw"

    monkeypatch.setattr(routes, "_get_class", fake_get_class)
    monkeypatch.setattr(routes, "add_students", fake_add_students)
    monkeypatch.setattr(roster_csv, "student_email_domain", fake_domain)

    await routes.import_students(uuid.uuid4(), None, TEACHER, _upload(b"a@x.edu\n"))

    assert len(threads) == 3
    assert all(th is not threading.main_thread() for th in threads)


@pytest.mark.asyncio
async def test_oversized_csv_is_rejected(captured_import) -> None:
    raw = b"a@x.edu\n" * (routes.MAX_STUDENT_CSV_BYTES // 8 + 1)
    with pytest.raises(BadRequestError) as caught:
        await routes.import_students(uuid.uuid4(), None, TEACHER, _upload(raw))
    assert caught.value.message == "學生名單 CSV 不可超過 1 MB"
    assert captured_import == []


# --- 縮短課程時被刪掉的週次，教材檔要一起從磁碟清掉 ------------


def test_shortening_weeks_is_rejected_and_keeps_task_files(
    db: Session, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(routes.weekly_task_service, "TASK_FILE_ROOT", tmp_path)
    item = _class(db, start_date=date(2026, 9, 1), end_date=date(2026, 9, 15))
    weeks = []
    for number, day in [
        (1, date(2026, 9, 1)),
        (2, date(2026, 9, 8)),
        (3, date(2026, 9, 15)),
    ]:
        week = TeachingClassWeek(class_id=item.id, week_number=number, session_date=day)
        db.add(week)
        weeks.append(week)
    db.commit()
    blobs = {}
    for week in weeks:
        storage_key = f"{uuid.uuid4().hex}.task"
        (tmp_path / storage_key).write_bytes(b"lab")
        db.add(
            TeachingClassTaskFile(
                week_id=week.id, filename="lab.txt", storage_key=storage_key
            )
        )
        blobs[week.week_number] = tmp_path / storage_key
    db.commit()

    item.end_date = date(2026, 9, 8)
    db.add(item)
    db.commit()
    with pytest.raises(BadRequestError):
        class_lifecycle_service.generate_weeks(db, item, preserve=True)

    # 週次只增不減：第 3 週與它的教材都還在
    assert all(blob.exists() for blob in blobs.values())
    assert db.get(TeachingClassWeek, weeks[2].id) is not None


# --- 封存一律回收機器（不再提供保留機器的選項） ------------------------------


def test_class_archive_rejects_keeping_resources() -> None:
    for value in (False, 0, "false", None):
        with pytest.raises(ValidationError):
            routes.ClassArchive.model_validate({"reclaim_resources": value})
    # 舊版用戶端送 true 照常接受；選項本身已不在 schema 裡
    body = routes.ClassArchive.model_validate({"reclaim_resources": True, "force": True})
    assert body.force is True
    assert "reclaim_resources" not in routes.ClassArchive.model_fields
    assert routes.ClassArchive.model_validate({}).force is False


def test_archive_route_always_queues_reclaim(db: Session, monkeypatch) -> None:
    item = _class(db, status=TeachingClassStatus.active)
    queued: list[dict] = []
    monkeypatch.setattr(class_lifecycle_service, "cancel_jobs", lambda *_a: None)
    monkeypatch.setattr(
        class_lifecycle_service, "clear_schedule_windows", lambda *_a: None
    )
    monkeypatch.setattr(
        class_lifecycle_service.class_capacity_service,
        "release",
        lambda *_a, **_k: None,
    )

    def fake_queue_reclaim(**kwargs):
        queued.append(kwargs)
        return {
            "queued_vmids": [101],
            "in_progress_vmids": [],
            "cleaned_vmids": [],
            "failed": [],
        }

    monkeypatch.setattr(class_lifecycle_service, "queue_reclaim", fake_queue_reclaim)

    result = routes.archive_class(
        item.id,
        routes.ClassArchive.model_validate({"force": False}),
        db,
        TEACHER,
    )

    assert len(queued) == 1
    assert queued[0]["force"] is False
    assert result["reclaim"]["queued_vmids"] == [101]
    db.refresh(item)
    assert item.status == TeachingClassStatus.archived
