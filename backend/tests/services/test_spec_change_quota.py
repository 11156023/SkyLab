"""規格調整的配額：送單就檢查、尚未套用的增量要預約、核准／套用時排除自己。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError
from app.services.resource import quota_service
from app.services.resource.quota_policy import QuotaUsage
from app.services.vm import spec_change_service

USER_ID = uuid.uuid4()


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _SplitSession:
    """依查詢的資料表回不同的列：申請單與規格調整各自一份。"""

    def __init__(self, vm_requests: list[object], spec_requests: list[object]) -> None:
        self.vm_requests = vm_requests
        self.spec_requests = spec_requests
        self.statements: list[str] = []

    def exec(self, statement: object) -> _FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if "spec_change_requests" in sql:
            return _FakeResult(self.spec_requests)
        return _FakeResult(self.vm_requests)

    def spec_sql(self) -> str:
        return next(s for s in self.statements if "spec_change_requests" in s)


def _spec_request(**overrides: object) -> SimpleNamespace:
    values: dict = {
        "id": uuid.uuid4(),
        "user_id": USER_ID,
        "current_cpu": 2,
        "requested_cpu": 4,
        "current_memory": 2048,
        "requested_memory": None,
        "current_disk": 20,
        "requested_disk": 50,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_spec_change_delta_only_counts_growth() -> None:
    assert quota_service.spec_change_delta(_spec_request()) == (2, 0, 30)
    # 調小不會退還額度，也不會變成負數
    assert quota_service.spec_change_delta(
        _spec_request(requested_cpu=1, requested_memory=1024, requested_disk=None)
    ) == (0, 0, 0)


def test_get_usage_counts_unapplied_spec_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """尚未套用的規格調整也要預約增量，否則每台各送一張調大的單就能繞過配額。"""
    monkeypatch.setattr(quota_service, "_owned_vmids", lambda session, user_id: [101])
    cluster = [
        {"vmid": 101, "maxcpu": 2, "maxmem": 2 * 1024**3, "maxdisk": 20 * 1024**3}
    ]
    session = _SplitSession(
        vm_requests=[],
        spec_requests=[_spec_request(), _spec_request(requested_memory=4096)],
    )

    usage = quota_service.get_usage(
        session, USER_ID, cluster_resources=cluster  # type: ignore[arg-type]
    )

    # 機器 2C/2048MB/20GB + 兩張調整 (+2C,+30GB) 與 (+2C,+2048MB,+30GB)；台數不變
    assert usage == QuotaUsage(cpu_cores=6, memory_mb=4096, disk_gb=80, instances=1)
    # 只算待審／已核准、還沒套用、機器還在、非延長到期日的單
    spec_sql = session.spec_sql()
    assert "applied_at IS NULL" in spec_sql
    assert "resource_vmid IS NOT NULL" in spec_sql
    assert "change_type !=" in spec_sql


def test_get_usage_excludes_the_spec_request_being_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(quota_service, "_owned_vmids", lambda session, user_id: [])
    session = _SplitSession(vm_requests=[], spec_requests=[])

    quota_service.get_usage(
        session,  # type: ignore[arg-type]
        USER_ID,
        cluster_resources=[],
        exclude_spec_request_id=uuid.uuid4(),
    )

    assert "spec_change_requests.id !=" in session.spec_sql()


def _stub_spec_create(monkeypatch: pytest.MonkeyPatch, captured: list[dict]) -> None:
    monkeypatch.setattr(
        spec_change_service,
        "_check_ownership_and_get_info",
        lambda **kwargs: {"node": "pve1", "type": "qemu", "vmid": 101, "name": "vm"},
    )
    monkeypatch.setattr(
        spec_change_service, "_reject_fixed_spec_resource", lambda **kwargs: None
    )
    monkeypatch.setattr(
        spec_change_service.spec_request_repo,
        "get_open_spec_change_request_by_vmid",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        spec_change_service,
        "proxmox_service",
        SimpleNamespace(
            get_current_specs=lambda *a, **k: {"cpu": 2, "memory": 2048, "disk": 20},
        ),
    )

    def _deny(session, user_id, **kwargs):
        captured.append(kwargs)
        raise ConflictError("配額不足")

    monkeypatch.setattr(spec_change_service.quota_service, "check_quota", _deny)


def test_spec_change_create_blocked_by_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """送單時就檢查剩餘配額，不要等管理員核准才被擋；只算增量。"""
    captured: list[dict] = []
    _stub_spec_create(monkeypatch, captured)
    request_in = SimpleNamespace(
        vmid=101,
        change_type=spec_change_service.SpecChangeType.combined,
        reason="Need more resources",
        requested_cpu=6,
        requested_memory=1024,  # 調小：不佔額度
        requested_disk=50,
        requested_expiry_date=None,
    )
    user = SimpleNamespace(id=uuid.uuid4(), email="stu@campus.edu")

    with pytest.raises(ConflictError):
        spec_change_service.create(session=None, request_in=request_in, user=user)

    assert captured == [{"delta_cores": 4, "delta_memory_mb": 0, "delta_disk_gb": 30}]


def test_spec_change_review_excludes_its_own_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核准時這張單已在預約裡，要排除自己再用增量檢查，否則會被算兩次。"""
    captured: list[dict] = []

    def _capture(session, user_id, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(spec_change_service.quota_service, "check_quota", _capture)
    db_request = _spec_request(requested_cpu=8, requested_disk=None)

    spec_change_service._check_quota_delta(None, db_request)  # type: ignore[arg-type]

    assert captured == [{
        "delta_cores": 6,
        "delta_memory_mb": 0,
        "delta_disk_gb": 0,
        "exclude_spec_request_id": db_request.id,
    }]
