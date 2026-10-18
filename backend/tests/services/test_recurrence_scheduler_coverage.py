"""app.services.scheduling.recurrence_scheduler 的分支覆蓋測試（假 session／monkeypatch）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import NotFoundError, ProxmoxError
from app.models import (
    BatchProvisionJob,
    BatchProvisionTask,
    TeachingClass,
    TeachingClassStatus,
    VMRequest,
)
from app.services.scheduling import recurrence_scheduler as rs
from app.services.scheduling import support as scheduling_support
from app.services.scheduling.recurrence import SchedulePolicy

NOW = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


class _FakeSession:
    """依 select 的主體 model 回傳固定資料；``get`` 依 id 查表。"""

    def __init__(
        self,
        rows: dict[type, list] | None = None,
        objects: dict[Any, Any] | None = None,
    ) -> None:
        self.rows = rows or {}
        self.objects = objects or {}
        self.added: list = []
        self.commits = 0

    def exec(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        rows = list(self.rows.get(entity, []))
        return SimpleNamespace(all=lambda: rows)

    def get(self, _model, key):
        return self.objects.get(key)

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


def _use_session(monkeypatch, session: _FakeSession) -> _FakeSession:
    monkeypatch.setattr(rs, "Session", lambda engine: session)
    return session


def _class(**overrides) -> SimpleNamespace:
    defaults: dict = {
        "id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "status": TeachingClassStatus.active,
        "timezone": "Asia/Taipei",
        "start_date": date(2026, 9, 1),
        "start_time": time(8, 0),
        "end_date": date(2027, 1, 31),
        "end_time": time(18, 0),
        "resources_reclaimed_at": None,
        "reclaim_requested_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _job(**overrides) -> SimpleNamespace:
    defaults: dict = {
        "id": uuid.uuid4(),
        "teaching_class_id": uuid.uuid4(),
        "recurrence_rule": "FREQ=WEEKLY;BYDAY=WE;BYHOUR=9;BYMINUTE=0",
        "recurrence_duration_minutes": 120,
        "schedule_timezone": None,
        "next_window_start": None,
        "next_window_end": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# process_recurrence_windows
# ---------------------------------------------------------------------------


def test_recurrence_windows_refresh_requests(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_utc_now", lambda: NOW)
    monkeypatch.setattr(rs, "_process_expired_class_lifecycle", lambda *, now: None)
    window = (NOW + timedelta(days=1), NOW + timedelta(days=1, hours=2))
    valid = SimpleNamespace(
        id=1, recurrence_rule="valid", next_window_end=NOW + timedelta(hours=1)
    )
    exhausted = SimpleNamespace(
        id=2,
        recurrence_rule="exhausted",
        recurrence_duration_minutes=60,
        schedule_timezone=None,
        next_window_start=NOW - timedelta(days=1),
        next_window_end=NOW - timedelta(hours=1),
    )
    broken = SimpleNamespace(
        id=3,
        recurrence_rule="broken",
        recurrence_duration_minutes=60,
        schedule_timezone="Bad/Zone",
        next_window_end=None,
    )
    fresh = SimpleNamespace(
        id=4,
        recurrence_rule="fresh",
        recurrence_duration_minutes=None,
        schedule_timezone=None,
        next_window_start=None,
        next_window_end=None,
    )

    def _next(*, rule, duration_minutes, timezone, after):
        assert after == NOW
        if rule == "broken":
            raise ValueError("bad rrule")
        return None if rule == "exhausted" else window

    monkeypatch.setattr(rs, "compute_next_window", _next)
    session = _use_session(
        monkeypatch,
        _FakeSession({VMRequest: [valid, exhausted, broken, fresh]}),
    )

    rs.process_recurrence_windows()

    assert exhausted.next_window_start is None
    assert exhausted.next_window_end is None
    assert (fresh.next_window_start, fresh.next_window_end) == window
    assert session.added == [exhausted, fresh]
    assert session.commits == 1


def test_recurrence_windows_refresh_batch_jobs(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_utc_now", lambda: NOW)
    monkeypatch.setattr(rs, "_process_expired_class_lifecycle", lambda *, now: None)
    window = (NOW + timedelta(hours=2), NOW + timedelta(hours=4))
    active_class = _class()
    archived_class = _class(status=TeachingClassStatus.archived)

    no_class_with_window = _job(
        next_window_start=NOW - timedelta(hours=2),
        next_window_end=NOW - timedelta(hours=1),
    )
    archived_no_window = _job(teaching_class_id=archived_class.id)
    still_valid = _job(
        teaching_class_id=active_class.id,
        next_window_end=NOW + timedelta(minutes=30),
    )
    broken = _job(teaching_class_id=active_class.id, recurrence_rule="broken")
    fresh = _job(teaching_class_id=active_class.id, recurrence_rule="fresh")
    exhausted = _job(
        teaching_class_id=active_class.id,
        recurrence_rule="exhausted",
        recurrence_duration_minutes=None,
        next_window_start=NOW - timedelta(days=7),
        next_window_end=NOW - timedelta(days=7),
    )
    references: list[datetime] = []

    def _active(*, rule, duration_minutes, timezone, now):
        references.append(now)
        if rule == "broken":
            raise ValueError("bad rrule")
        return None if rule == "exhausted" else window

    monkeypatch.setattr(rs, "compute_active_or_next_window", _active)
    session = _use_session(
        monkeypatch,
        _FakeSession(
            {
                BatchProvisionJob: [
                    no_class_with_window,
                    archived_no_window,
                    still_valid,
                    broken,
                    fresh,
                    exhausted,
                ]
            },
            {active_class.id: active_class, archived_class.id: archived_class},
        ),
    )

    rs.process_recurrence_windows()

    assert no_class_with_window.next_window_start is None
    assert no_class_with_window.next_window_end is None
    assert (fresh.next_window_start, fresh.next_window_end) == window
    assert (exhausted.next_window_start, exhausted.next_window_end) == (None, None)
    assert session.added == [no_class_with_window, fresh, exhausted]
    assert session.commits == 1
    # 班級開課時間早於 now，參考時間取 now
    assert references == [NOW, NOW, NOW]


def test_class_schedule_reference_without_class_is_now() -> None:
    assert rs._class_schedule_reference(None, _job(), NOW) == NOW


# ---------------------------------------------------------------------------
# process_scheduled_boot
# ---------------------------------------------------------------------------


def _policy(**overrides) -> SchedulePolicy:
    values = {
        "boot_batch_size": 1,
        "boot_batch_interval_seconds": 7,
        "boot_lead_time_minutes": 5,
        "window_grace_minutes": 30,
        "practice_session_hours": 3,
        "practice_warning_minutes": 30,
        "expiry_warning_hours": 24,
    }
    values.update(overrides)
    return SchedulePolicy(**values)


def _session_policy(*, session: Any) -> SchedulePolicy:
    return _policy()


def test_scheduled_boot_batches_and_survives_failures(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_utc_now", lambda: NOW)
    monkeypatch.setattr(rs, "get_schedule_policy", _session_policy)
    end = NOW + timedelta(hours=1)
    first = SimpleNamespace(
        id=uuid.uuid4(),
        vmid=101,
        actual_node=None,
        assigned_node="pve1",
        next_window_end=end,
        resource_type="lxc",
    )
    no_vmid = SimpleNamespace(id=uuid.uuid4(), vmid=None)
    batch_spec = rs._BootSpec(
        request_id=uuid.uuid4(),
        vmid=202,
        node="pve2",
        window_end=end,
        resource_type="qemu",
        grace_already_in_window=True,
    )
    _use_session(monkeypatch, _FakeSession({VMRequest: [first, no_vmid]}))
    monkeypatch.setattr(
        rs,
        "_filter_due_for_boot",
        lambda *, session, requests, grace_minutes: requests,
    )
    monkeypatch.setattr(rs, "_batch_boot_specs", lambda *, session, now: [batch_spec])
    booted: list[rs._BootSpec] = []

    def _boot(*, spec, grace):
        booted.append(spec)
        assert grace == timedelta(minutes=30)
        if spec.vmid == 101:
            raise RuntimeError("pve refused")

    monkeypatch.setattr(rs, "_boot_one", _boot)
    sleeps: list[float] = []
    monkeypatch.setattr(rs.time, "sleep", sleeps.append)

    rs.process_scheduled_boot()

    assert [s.vmid for s in booted] == [101, 202]
    assert booted[0].node == "pve1"
    assert booted[0].resource_type == "lxc"
    assert sleeps == [7]


# ---------------------------------------------------------------------------
# process_auto_stops
# ---------------------------------------------------------------------------


def test_auto_stops_prunes_stale_keys_and_logs_failures(monkeypatch) -> None:
    monkeypatch.setattr(rs, "_utc_now", lambda: NOW)
    _use_session(monkeypatch, _FakeSession())
    resource = SimpleNamespace(
        vmid=5, auto_stop_at=NOW - timedelta(minutes=1), auto_stop_reason="x"
    )
    tracked = {(99, None): 1.0, (5, resource.auto_stop_at): 2.0}
    monkeypatch.setattr(rs, "_shutdown_requested_at", tracked)
    monkeypatch.setattr(
        rs.resource_repo, "list_due_auto_stops", lambda *, session, now: [resource]
    )
    stopped: list = []

    def _stop(*, resource):
        stopped.append(resource.vmid)
        raise RuntimeError("pve down")

    monkeypatch.setattr(rs, "_stop_one", _stop)

    rs.process_auto_stops()

    assert stopped == [5]
    assert tracked == {(5, resource.auto_stop_at): 2.0}


# ---------------------------------------------------------------------------
# _process_expired_class_lifecycle
# ---------------------------------------------------------------------------


def test_expired_class_lifecycle_archives_and_retries(monkeypatch) -> None:
    past = date(2026, 10, 1)
    expired_ok = _class(end_date=past)
    expired_gone = _class(end_date=past)
    expired_already_archived = _class(end_date=past)
    expired_boom = _class(end_date=past)
    broken = _class(end_date=None)
    not_expired = _class()
    retry_ok = _class(status=TeachingClassStatus.archived, end_date=past)
    retry_gone = _class(status=TeachingClassStatus.archived, end_date=past)
    retry_boom = _class(
        status=TeachingClassStatus.archived,
        end_date=past,
        reclaim_requested_at=NOW - timedelta(hours=1),
    )
    retry_recent = _class(
        status=TeachingClassStatus.archived,
        end_date=past,
        reclaim_requested_at=NOW - timedelta(minutes=1),
    )
    retry_now_done = _class(status=TeachingClassStatus.archived, end_date=past)
    reclaimed = _class(
        status=TeachingClassStatus.archived,
        end_date=past,
        resources_reclaimed_at=NOW - timedelta(days=1),
    )
    candidates = [
        expired_ok,
        expired_gone,
        expired_already_archived,
        expired_boom,
        broken,
        not_expired,
        retry_ok,
        retry_gone,
        retry_boom,
        retry_recent,
        retry_now_done,
        reclaimed,
    ]
    # 第二階段重讀時：gone 已不存在、already_archived 已封存、now_done 已回收完
    objects = {
        expired_ok.id: expired_ok,
        expired_already_archived.id: _class(
            id=expired_already_archived.id, status=TeachingClassStatus.archived
        ),
        expired_boom.id: expired_boom,
        retry_ok.id: retry_ok,
        retry_boom.id: retry_boom,
        retry_now_done.id: _class(
            id=retry_now_done.id,
            status=TeachingClassStatus.archived,
            resources_reclaimed_at=NOW,
        ),
    }
    _use_session(monkeypatch, _FakeSession({TeachingClass: candidates}, objects))
    archived: list = []
    retried: list = []

    def _archive(*, session, item, requested_by, force):
        assert force is True
        assert requested_by == item.owner_id
        if item is expired_boom:
            raise RuntimeError("archive failed")
        archived.append(item.id)
        return {"queued_vmids": [1, 2], "failed": []}

    def _queue(*, session, item, requested_by, force):
        if item is retry_boom:
            raise RuntimeError("queue failed")
        retried.append(item.id)
        return {"queued_vmids": [3], "in_progress_vmids": [], "failed": [4]}

    monkeypatch.setattr(rs.class_lifecycle_service, "archive_and_reclaim", _archive)
    monkeypatch.setattr(rs.class_lifecycle_service, "queue_reclaim", _queue)

    rs._process_expired_class_lifecycle(now=NOW)

    assert archived == [expired_ok.id]
    assert retried == [retry_ok.id]


# ---------------------------------------------------------------------------
# _batch_boot_specs
# ---------------------------------------------------------------------------


def test_batch_boot_specs_filters_tasks(monkeypatch) -> None:
    window_end = NOW + timedelta(hours=1)
    teaching_class = _class()
    disabled_job = _job()  # 找不到班級 → 不排程
    job = _job(
        teaching_class_id=teaching_class.id,
        next_window_start=NOW - timedelta(minutes=5),
        next_window_end=window_end,
    )
    tasks = [SimpleNamespace(vmid=v) for v in (None, 1, 2, 3, 4, 5, 6, 7, 8)]
    resources = {
        2: SimpleNamespace(teaching_class_id=uuid.uuid4(), auto_stop_at=None),
        3: SimpleNamespace(
            teaching_class_id=teaching_class.id,
            auto_stop_at=window_end + timedelta(minutes=1),
        ),
    }
    own = SimpleNamespace(teaching_class_id=teaching_class.id, auto_stop_at=None)
    for vmid in (4, 5, 6, 7, 8):
        resources[vmid] = own
    monkeypatch.setattr(
        rs.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resources.get(vmid),
    )

    def _info(*, vmid):
        if vmid == 4:
            raise ProxmoxError("connection down")
        return {
            5: None,
            6: {"status": "running", "node": "pve1", "type": "qemu"},
            7: {"status": "stopped", "node": "pve2", "type": "lxc"},
            8: {"status": "stopped", "node": "pve3", "type": "qemu"},
        }[vmid]

    monkeypatch.setattr(rs, "_resource_info", _info)
    grace_stops: list[dict] = []
    monkeypatch.setattr(
        rs.resource_repo, "set_auto_stop", lambda **kw: grace_stops.append(kw)
    )
    session = _FakeSession(
        {BatchProvisionJob: [disabled_job, job], BatchProvisionTask: tasks},
        {teaching_class.id: teaching_class},
    )

    specs = rs._batch_boot_specs(session=session, now=NOW)

    assert [(s.vmid, s.node, s.resource_type) for s in specs] == [
        (7, "pve2", "lxc"),
        (8, "pve3", "qemu"),
    ]
    assert all(s.grace_already_in_window for s in specs)
    assert all(s.request_id == job.id for s in specs)
    assert [(g["vmid"], g["auto_stop_at"]) for g in grace_stops] == [(6, window_end)]


# ---------------------------------------------------------------------------
# _filter_due_for_boot
# ---------------------------------------------------------------------------


def test_filter_due_for_boot(monkeypatch) -> None:
    window_end = NOW + timedelta(hours=1)

    def _req(vmid):
        return SimpleNamespace(
            vmid=vmid,
            actual_node=None,
            assigned_node="pve1" if vmid != 6 else None,
            next_window_end=window_end,
            resource_type="vm",
        )

    reqs = {v: _req(v) for v in (None, 1, 2, 3, 4, 5, 6)}
    resources = {
        2: SimpleNamespace(auto_stop_at=window_end),
        3: SimpleNamespace(auto_stop_at=None),
        4: SimpleNamespace(auto_stop_at=NOW - timedelta(days=1)),
        5: SimpleNamespace(auto_stop_at=None),
        6: SimpleNamespace(auto_stop_at=None),
    }
    monkeypatch.setattr(
        rs.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resources.get(vmid),
    )

    def _status(node, vmid, rtype):
        assert rtype == "qemu"
        if vmid == 4:
            raise ProxmoxError("status failed")
        if vmid == 6:
            assert node == ""
        return {"status": "running" if vmid == 3 else "stopped"}

    monkeypatch.setattr(rs.proxmox_service, "get_status", _status)
    grace_stops: list[dict] = []
    monkeypatch.setattr(
        rs.resource_repo, "set_auto_stop", lambda **kw: grace_stops.append(kw)
    )

    due = rs._filter_due_for_boot(
        session=_FakeSession(), requests=list(reqs.values()), grace_minutes=10
    )

    assert [r.vmid for r in due] == [4, 5, 6]
    assert [(g["vmid"], g["auto_stop_at"]) for g in grace_stops] == [
        (3, window_end + timedelta(minutes=10))
    ]


# ---------------------------------------------------------------------------
# _boot_one / _write_window_grace_stop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("in_window", [False, True])
def test_boot_one_qemu_sets_auto_stop(monkeypatch, in_window: bool) -> None:
    _use_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(rs.proxmox_service, "control", lambda *a: None)
    stops: list[dict] = []
    monkeypatch.setattr(
        rs.resource_repo, "set_auto_stop", lambda **kw: stops.append(kw)
    )
    end = NOW + timedelta(hours=1)
    rs._boot_one(
        spec=rs._BootSpec(
            request_id=uuid.uuid4(),
            vmid=3,
            node="pve1",
            window_end=end,
            resource_type="qemu",
            grace_already_in_window=in_window,
        ),
        grace=timedelta(minutes=5),
    )
    expected = end if in_window else end + timedelta(minutes=5)
    assert stops[0]["auto_stop_at"] == expected
    assert stops[0]["auto_stop_reason"] == "window_grace"


def test_write_window_grace_stop_without_window_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(
        rs.resource_repo,
        "set_auto_stop",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no write")),
    )
    rs._write_window_grace_stop(
        session=_FakeSession(), vmid=1, window_end=None, grace_minutes=5
    )


# ---------------------------------------------------------------------------
# _stop_one / _resource_info / _chunk
# ---------------------------------------------------------------------------


@pytest.fixture
def stop_env(monkeypatch) -> dict[str, Any]:
    env: dict[str, Any] = {"info": None, "actions": [], "cleared": [], "fail": set()}
    monkeypatch.setattr(rs, "_shutdown_requested_at", {})
    monkeypatch.setattr(rs, "_resource_info", lambda *, vmid: env["info"])
    _use_session(monkeypatch, _FakeSession())

    def _control(node, vmid, rtype, action):
        env["actions"].append(action)
        if action in env["fail"]:
            raise ProxmoxError(f"{action} failed")

    monkeypatch.setattr(rs.proxmox_service, "control", _control)
    monkeypatch.setattr(
        rs.resource_repo,
        "set_auto_stop",
        lambda **kw: env["cleared"].append(kw["vmid"]),
    )
    monkeypatch.setattr(rs.time, "monotonic", lambda: 500.0)
    return env


def _resource(vmid: int = 9) -> SimpleNamespace:
    return SimpleNamespace(vmid=vmid, auto_stop_at=NOW, auto_stop_reason="window_grace")


def test_stop_one_clears_schedule_when_machine_gone(stop_env) -> None:
    rs._stop_one(resource=_resource())
    assert stop_env["cleared"] == [9]
    assert stop_env["actions"] == []


def test_stop_one_gives_up_when_hard_stop_also_fails(stop_env) -> None:
    stop_env["info"] = {"node": "pve1", "type": "lxc", "status": "running"}
    stop_env["fail"] = {"shutdown", "stop"}
    rs._stop_one(resource=_resource())
    assert stop_env["actions"] == ["shutdown", "stop"]
    assert rs._shutdown_requested_at == {}


def test_resource_info_returns_none_for_missing_or_empty(monkeypatch) -> None:
    monkeypatch.setattr(scheduling_support, "find_resource_strict", lambda vmid: {})
    assert rs._resource_info(vmid=1) is None

    def _gone(_vmid):
        raise NotFoundError("gone")

    monkeypatch.setattr(scheduling_support, "find_resource_strict", _gone)
    assert rs._resource_info(vmid=1) is None

    monkeypatch.setattr(
        scheduling_support, "find_resource_strict", lambda vmid: {"node": "pve1"}
    )
    assert rs._resource_info(vmid=1) == {"node": "pve1"}


def test_chunk_sizes() -> None:
    assert rs._chunk([1, 2, 3], 0) == [[1, 2, 3]]
    assert rs._chunk([1, 2, 3], 2) == [[1, 2], [3]]
