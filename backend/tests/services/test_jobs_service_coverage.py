"""jobs_service（任務聚合、清單與單筆明細）的分支覆蓋測試。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resource_markers import RESOURCE_DELETED_BY_USER_MARKER
from app.models import (
    DeletionRequest,
    DeletionRequestStatus,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
    TaskRecord,
    TaskRecordStatus,
    User,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
)
from app.schemas.jobs import JobItem, JobKind, JobStatus
from app.services.jobs import jobs_service

T0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)


def _admin() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")


def _student(user_id: uuid.UUID | None = None) -> Any:
    return SimpleNamespace(
        id=user_id or uuid.uuid4(), is_superuser=False, role="student"
    )


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _Session:
    """依 select 的實體回傳預設資料列；get() 從 objects 取。"""

    def __init__(
        self,
        rows: dict[Any, list[Any]] | None = None,
        objects: dict[tuple[Any, Any], Any] | None = None,
    ) -> None:
        self.rows = rows or {}
        self.objects = objects or {}
        self.entities: list[Any] = []
        self.rollbacks = 0

    def exec(self, stmt: Any) -> _Result:
        entity = stmt.column_descriptions[0]["entity"]
        self.entities.append(entity)
        return _Result(self.rows.get(entity, []))

    def get(self, cls: Any, key: Any) -> Any:
        return self.objects.get((cls, key))

    def rollback(self) -> None:
        self.rollbacks += 1


def _job(status: JobStatus, minutes: int) -> JobItem:
    return JobItem(
        id=f"x:{uuid.uuid4()}",
        kind=JobKind.deletion,
        title="t",
        status=status,
        created_at=T0,
        updated_at=T0 + timedelta(minutes=minutes),
    )


# ─── 規格變更 ─────────────────────────────────────────────────────────────


def _spec(**overrides: Any) -> SpecChangeRequest:
    data: dict[str, Any] = {
        "vmid": 101,
        "user_id": uuid.uuid4(),
        "change_type": SpecChangeType.cpu,
        "reason": "need more",
        "created_at": T0,
    }
    data.update(overrides)
    return SpecChangeRequest(**data)


@pytest.mark.parametrize(
    ("overrides", "expected_status", "expected_message"),
    [
        ({"status": SpecChangeRequestStatus.pending}, JobStatus.pending, None),
        ({"status": SpecChangeRequestStatus.rejected}, JobStatus.failed, None),
        ({"status": SpecChangeRequestStatus.cancelled}, JobStatus.cancelled, None),
        (
            {
                "status": SpecChangeRequestStatus.approved,
                "applied_at": T0,
                "apply_error": "auto start failed",
            },
            JobStatus.completed,
            "auto start failed",
        ),
        (
            {"status": SpecChangeRequestStatus.approved, "apply_error": "boom"},
            JobStatus.failed,
            "boom",
        ),
        (
            {"status": SpecChangeRequestStatus.approved, "apply_started_at": T0},
            JobStatus.running,
            "套用中：關機 → 改規格 → 開機",
        ),
        (
            {"status": SpecChangeRequestStatus.approved},
            JobStatus.blocked,
            "已核准，等待申請人按「套用」",
        ),
    ],
)
def test_spec_change_job_status(
    overrides: dict[str, Any], expected_status: JobStatus, expected_message: Any
) -> None:
    assert jobs_service._spec_change_job_status(_spec(**overrides)) == (
        expected_status,
        expected_message,
    )


# ─── 刪除 ─────────────────────────────────────────────────────────────────


def _deletion(**overrides: Any) -> DeletionRequest:
    data: dict[str, Any] = {"user_id": uuid.uuid4(), "vmid": 101, "created_at": T0}
    data.update(overrides)
    return DeletionRequest(**data)


@pytest.mark.parametrize(
    ("status", "progress", "completed"),
    [
        (DeletionRequestStatus.completed, 100, True),
        (DeletionRequestStatus.running, 50, False),
        (DeletionRequestStatus.pending, 0, False),
        (DeletionRequestStatus.failed, None, True),
    ],
)
def test_deletion_to_job_progress(
    status: DeletionRequestStatus, progress: int | None, completed: bool
) -> None:
    req = _deletion(
        status=status,
        name="vm-a",
        started_at=T0 + timedelta(minutes=1),
        completed_at=T0 + timedelta(minutes=2) if completed else None,
    )
    item = jobs_service._deletion_to_job(req, user_email="u@campus.edu")
    assert item.progress == progress
    assert item.title == "刪除 vm-a"
    assert item.user_email == "u@campus.edu"
    if completed:
        assert item.completed_at == T0 + timedelta(minutes=2)
        assert item.updated_at == T0 + timedelta(minutes=2)
    else:
        assert item.completed_at is None
        assert item.updated_at == T0 + timedelta(minutes=1)


def test_deletion_to_job_without_timestamps_falls_back_to_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_service, "_now", lambda: T0)
    req = _deletion(created_at=None)
    item = jobs_service._deletion_to_job(req)
    assert item.title == "刪除 VMID 101"
    assert item.created_at == T0
    assert item.updated_at == T0


def test_fetch_deletions_maps_user_email() -> None:
    with_user = _deletion(vmid=1)
    with_user.user = User(email="a@campus.edu", hashed_password="x")
    without_user = _deletion(vmid=2)
    session = _Session(rows={DeletionRequest: [with_user, without_user]})

    items = jobs_service._fetch_deletions(
        session,  # type: ignore[arg-type]
        user=_admin(),
        since=T0 - timedelta(days=1),
        own_only=True,
    )

    assert [i.user_email for i in items] == ["a@campus.edu", None]


def test_fetch_spec_changes_non_admin() -> None:
    session = _Session(rows={SpecChangeRequest: [_spec()]})
    items = jobs_service._fetch_spec_changes(
        session,  # type: ignore[arg-type]
        user=_student(),
        since=T0 - timedelta(days=1),
    )
    assert len(items) == 1
    assert items[0].kind == JobKind.spec_change


# ─── TaskRecord ───────────────────────────────────────────────────────────


def test_parse_json_variants() -> None:
    assert jobs_service._parse_json(None) == {}
    assert jobs_service._parse_json("") == {}
    assert jobs_service._parse_json({"a": 1}) == {"a": 1}
    assert jobs_service._parse_json('{"a": 1}') == {"a": 1}
    assert jobs_service._parse_json("[1, 2]") == {}
    assert jobs_service._parse_json("not json") == {}


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (JobKind.resource_reset, {"vmid": 101}, "重置：VMID 101"),
        (JobKind.resource_reset, {}, "重置"),
        (JobKind.batch_provision, {"job_id": "abcdef0123456789"}, "批次佈建：abcdef01"),
        (JobKind.batch_provision, {}, "批次佈建"),
        (JobKind.resource_backup, {"vmid": 7}, "備份：VMID 7"),
        (JobKind.resource_backup, {}, "備份"),
        (JobKind.resource_restore, {"vmid": 8}, "還原備份：VMID 8"),
        (JobKind.resource_restore, {}, "還原備份"),
        (JobKind.template, {}, "template"),
    ],
)
def test_queue_task_title(
    kind: JobKind, payload: dict[str, Any], expected: str
) -> None:
    assert jobs_service._queue_task_title(kind, payload) == expected


def _record(**overrides: Any) -> TaskRecord:
    data: dict[str, Any] = {
        "task_type": "template.convert",
        "user_id": uuid.uuid4(),
        "created_at": T0,
    }
    data.update(overrides)
    return TaskRecord(**data)


def test_task_record_to_job_template_titles() -> None:
    clone = jobs_service._task_record_to_job(
        _record(task_type="template.clone", payload={"hostname": "web-1"}),
        template_name="ubuntu",
    )
    assert clone.title == "克隆開通：web-1"
    assert clone.kind == JobKind.template

    named = jobs_service._task_record_to_job(
        _record(task_type="template.convert"), template_name="ubuntu"
    )
    assert named.title == "轉換範本：ubuntu"

    by_vmid = jobs_service._task_record_to_job(
        _record(task_type="template.delete", payload={"pve_vmid": 9000})
    )
    assert by_vmid.title == "刪除範本：VMID 9000"

    unknown = jobs_service._task_record_to_job(_record(task_type="template.weird"))
    assert unknown.title == "template.weird"


def test_fetch_task_records_queue_kind_for_student() -> None:
    owner_id = uuid.uuid4()
    template_id = uuid.uuid4()
    records = [
        _record(
            task_type="resource.reset",
            user_id=owner_id,
            template_id=template_id,
            payload={"vmid": 3},
        ),
        _record(task_type="resource.reset", user_id=owner_id),
    ]
    session = _Session(
        rows={
            TaskRecord: records,
            User: [SimpleNamespace(id=owner_id, email="o@campus.edu")],
            VMTemplate: [SimpleNamespace(id=template_id, name="tpl")],
        }
    )

    items = jobs_service._fetch_task_records(
        session,  # type: ignore[arg-type]
        user=_student(owner_id),
        since=T0 - timedelta(days=1),
        kind=JobKind.resource_reset,
    )

    assert [i.user_email for i in items] == ["o@campus.edu", "o@campus.edu"]
    assert items[0].meta["template_name"] == "tpl"
    assert items[1].meta["template_name"] is None
    assert session.entities == [TaskRecord, User, VMTemplate]


# ─── 聚合與清單 ───────────────────────────────────────────────────────────


def test_aggregate_skips_unknown_and_failed_fetchers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ok(session: Any, *, user: Any, since: Any, own_only: bool = False) -> list[Any]:
        return [_job(JobStatus.pending, 1), _job(JobStatus.completed, 5)]

    def broken(session: Any, **kw: Any) -> list[Any]:
        raise RuntimeError("query failed")

    monkeypatch.setattr(
        jobs_service,
        "_FETCHERS",
        {JobKind.vm_request: ok, JobKind.deletion: broken},
    )
    session = _Session()

    items = jobs_service._aggregate_jobs(
        session=session,  # type: ignore[arg-type]
        user=_admin(),
        kinds=[JobKind.vm_request, JobKind.spec_change, JobKind.deletion],
        since=T0,
    )

    assert [i.status for i in items] == [JobStatus.completed, JobStatus.pending]
    assert session.rollbacks == 1


@pytest.fixture()
def fixed_jobs(monkeypatch: pytest.MonkeyPatch) -> list[JobItem]:
    jobs = [
        _job(JobStatus.pending, 1),
        _job(JobStatus.running, 2),
        _job(JobStatus.completed, 3),
        _job(JobStatus.failed, 4),
    ]
    monkeypatch.setattr(
        jobs_service,
        "_aggregate_jobs",
        lambda **kw: sorted(jobs, key=lambda j: j.updated_at, reverse=True),
    )
    return jobs


def test_list_jobs_active_only(fixed_jobs: list[JobItem]) -> None:
    resp = jobs_service.list_jobs(
        session=None,  # type: ignore[arg-type]
        user=_admin(),
        active_only=True,
        statuses=[JobStatus.failed],
    )
    assert resp.total == 2
    assert {i.status for i in resp.items} == {JobStatus.pending, JobStatus.running}


def test_list_jobs_status_filter(fixed_jobs: list[JobItem]) -> None:
    resp = jobs_service.list_jobs(
        session=None,  # type: ignore[arg-type]
        user=_admin(),
        statuses=[JobStatus.failed, JobStatus.completed],
    )
    assert resp.total == 2
    assert resp.active_count == 2
    assert {i.status for i in resp.items} == {JobStatus.failed, JobStatus.completed}


# ─── 單筆明細 ─────────────────────────────────────────────────────────────


def test_coerce_aware_naive() -> None:
    assert jobs_service._coerce_aware(datetime(2026, 1, 1)) == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )


def _vm_request(**overrides: Any) -> VMRequest:
    data: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "reason": "lab",
        "resource_type": "lxc",
        "hostname": "lab-1",
        "status": VMRequestStatus.approved,
        "vmid": 101,
        "created_at": T0,
    }
    data.update(overrides)
    return VMRequest(**data)


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        ("vm_request", jobs_service._detail_vm_request),
        ("spec_change", jobs_service._detail_spec_change),
        ("deletion", jobs_service._detail_deletion),
        ("template", jobs_service._detail_task_record),
    ],
)
def test_detail_invalid_and_missing_ids(kind: str, detail: Any) -> None:
    with pytest.raises(jobs_service.JobNotFoundError):
        detail(_Session(), "not-a-uuid", _admin())
    with pytest.raises(jobs_service.JobNotFoundError):
        detail(_Session(), str(uuid.uuid4()), _admin())


def test_detail_vm_request_success_and_consumed() -> None:
    owner = uuid.uuid4()
    req = _vm_request(
        user_id=owner,
        expiry_date=date(2026, 12, 31),
        start_at=T0,
        end_at=T0 + timedelta(days=30),
        provisioning_status=VMProvisioningStatus.completed,
        provisioning_error="disk full",
    )
    detail = jobs_service._detail_vm_request(
        _Session(rows={VMRequest: [req]}),  # type: ignore[arg-type]
        str(req.id),
        _student(owner),
    )
    assert detail.error == "disk full"
    assert detail.extra["expiry_date"] == "2026-12-31"
    assert detail.extra["end_at"] == (T0 + timedelta(days=30)).isoformat()
    assert detail.extra["provisioning_status"] == "completed"

    consumed = _vm_request(provisioning_error=RESOURCE_DELETED_BY_USER_MARKER)
    detail2 = jobs_service._detail_vm_request(
        _Session(rows={VMRequest: [consumed]}),  # type: ignore[arg-type]
        str(consumed.id),
        _admin(),
    )
    assert detail2.error is None
    assert detail2.extra["expiry_date"] is None


def test_detail_spec_change_success() -> None:
    req = _spec(
        status=SpecChangeRequestStatus.approved,
        applied_at=T0,
        requested_cpu=4,
    )
    detail = jobs_service._detail_spec_change(
        _Session(rows={SpecChangeRequest: [req]}),  # type: ignore[arg-type]
        str(req.id),
        _admin(),
    )
    assert detail.error is None
    assert detail.extra["requested_cpu"] == 4
    assert detail.extra["applied_at"] == T0.isoformat()
    assert detail.item.status == JobStatus.completed


def test_detail_deletion_with_and_without_user() -> None:
    req = _deletion(
        status=DeletionRequestStatus.failed,
        error_message="locked",
        completed_at=T0,
    )
    req.user = User(email="d@campus.edu", hashed_password="x")
    detail = jobs_service._detail_deletion(
        _Session(rows={DeletionRequest: [req]}),  # type: ignore[arg-type]
        str(req.id),
        _admin(),
    )
    assert detail.error == "locked"
    assert detail.item.user_email == "d@campus.edu"
    assert detail.extra["completed_at"] == T0.isoformat()
    assert detail.extra["started_at"] is None

    bare = _deletion()
    detail2 = jobs_service._detail_deletion(
        _Session(rows={DeletionRequest: [bare]}),  # type: ignore[arg-type]
        str(bare.id),
        _admin(),
    )
    assert detail2.item.user_email is None


def test_detail_task_record_with_template_and_owner() -> None:
    owner_id = uuid.uuid4()
    template_id = uuid.uuid4()
    record = _record(
        user_id=owner_id,
        template_id=template_id,
        result={"ok": True},
        status=TaskRecordStatus.failed,
        error="clone failed",
        started_at=T0,
        finished_at=T0 + timedelta(minutes=3),
    )
    session = _Session(
        objects={
            (TaskRecord, record.id): record,
            (User, owner_id): SimpleNamespace(email="o@campus.edu"),
            (VMTemplate, template_id): SimpleNamespace(name="ubuntu"),
        }
    )
    detail = jobs_service._detail_task_record(
        session,  # type: ignore[arg-type]
        str(record.id),
        _student(owner_id),
    )
    assert detail.error == "clone failed"
    assert detail.item.user_email == "o@campus.edu"
    assert detail.extra["template_name"] == "ubuntu"
    assert detail.extra["template_id"] == str(template_id)
    assert detail.extra["result"] == {"ok": True}
    assert detail.extra["raw_status"] == "failed"


@pytest.mark.parametrize(
    ("minutes_ago", "comment", "expected"),
    [
        (30, None, "排程開機時間已超時 30 分鐘，仍未開始建立"),
        (150, "waiting", "waiting\n排程開機時間已超時 2 小時，仍未開始建立"),
    ],
)
def test_vm_request_to_job_overdue_message(
    monkeypatch: pytest.MonkeyPatch,
    minutes_ago: int,
    comment: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(jobs_service, "_now", lambda: T0)
    req = _vm_request(
        vmid=None,
        status=VMRequestStatus.pending,
        start_at=T0 - timedelta(minutes=minutes_ago),
        review_comment=comment,
    )
    item = jobs_service._vm_request_to_job(req)
    assert item.meta["overdue"] is True
    assert item.message == expected


def test_get_job_detail_dispatch_and_errors() -> None:
    with pytest.raises(jobs_service.JobNotFoundError):
        jobs_service.get_job_detail(session=_Session(), user=_admin(), job_id="nocolon")  # type: ignore[arg-type]
    with pytest.raises(jobs_service.JobNotFoundError):
        jobs_service.get_job_detail(
            session=_Session(),  # type: ignore[arg-type]
            user=_admin(),
            job_id="bogus:123",
        )

    record = _record(task_type="resource.restore", payload={"vmid": 9})
    session = _Session(objects={(TaskRecord, record.id): record})
    detail = jobs_service.get_job_detail(
        session=session,  # type: ignore[arg-type]
        user=_admin(),
        job_id=f"resource_restore:{record.id}",
    )
    assert detail.item.kind == JobKind.resource_restore
    assert detail.item.title == "還原備份：VMID 9"
