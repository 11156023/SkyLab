"""Coverage tests for app.services.resource.quota_service (global quota timestamp, listing)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError
from app.models import QuotaConfig
from app.services.resource import quota_service


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, config: Any = None, rows: list[Any] | None = None) -> None:
        self.config = config
        self.rows = rows or []
        self.commits = 0

    def get(self, _model: Any, _key: Any) -> Any:
        return self.config

    def exec(self, _stmt: Any) -> _Rows:
        return _Rows(self.rows)

    def add(self, obj: Any) -> None:
        self.config = obj

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _obj: Any) -> None:
        return None


def test_update_global_quota_keeps_updated_at_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    config = QuotaConfig(id=1, updated_at=previous)
    # clock reads the same instant as the last update (coarse Windows clock)
    monkeypatch.setattr(quota_service, "get_datetime_utc", lambda: previous)
    session = _FakeSession(config)

    out = quota_service.update_global_quota(
        session,  # type: ignore[arg-type]
        {"max_instances": 7, "unknown_field": 1, "max_cpu_cores": None},
    )

    assert out.updated_at == previous + timedelta(microseconds=1)
    assert out.max_instances == 7
    assert session.commits == 1


def test_list_user_quotas_returns_list() -> None:
    rows = [object(), object()]
    assert quota_service.list_user_quotas(_FakeSession(rows=rows)) == rows  # type: ignore[arg-type]


def test_owned_vmids_casts_to_int() -> None:
    assert quota_service._owned_vmids(
        _FakeSession(rows=["101", 102]), uuid.uuid4()
    ) == [  # type: ignore[arg-type]
        101,
        102,
    ]


def test_reserved_by_requests_excludes_given_request() -> None:
    rows = [
        SimpleNamespace(resource_type="lxc", rootfs_size=None, cores=2, memory=1024),
        SimpleNamespace(resource_type="qemu", disk_size=40, cores=None, memory=None),
    ]
    out = quota_service._reserved_by_requests(
        _FakeSession(rows=rows),  # type: ignore[arg-type]
        uuid.uuid4(),
        exclude_request_id=uuid.uuid4(),
    )
    assert out == (2, 1024, 8 + 40, 2)


def test_check_quota_for_existing_resource_converts_units(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        quota_service, "check_quota", lambda _s, _u, **kw: seen.append(kw)
    )
    quota_service.check_quota_for_existing_resource(
        _FakeSession(),  # type: ignore[arg-type]
        uuid.uuid4(),
        {"maxcpu": 2, "maxmem": 2 * 1024**3, "maxdisk": 32 * 1024**3},
    )
    assert seen == [
        {
            "delta_cores": 2,
            "delta_memory_mb": 2048,
            "delta_disk_gb": 32,
            "delta_instances": 1,
        }
    ]


def _provision_request() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        resource_type="qemu",
        disk_size=20,
        cores=2,
        memory=2048,
    )


def test_check_quota_for_provision_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("pve down")

    monkeypatch.setattr(quota_service, "get_effective_quota", _boom)
    quota_service.check_quota_for_provision(_FakeSession(), _provision_request())  # type: ignore[arg-type]


@pytest.mark.parametrize("violations", [[], ["cpu"]])
def test_check_quota_for_provision_enforces(
    monkeypatch: pytest.MonkeyPatch, violations: list[str]
) -> None:
    req = _provision_request()
    usage_calls: list[Any] = []
    monkeypatch.setattr(quota_service, "get_effective_quota", lambda _s, _u: "quota")
    monkeypatch.setattr(
        quota_service,
        "get_usage",
        lambda _s, _u, **kw: usage_calls.append(kw) or "usage",
    )
    deltas: list[dict[str, Any]] = []

    def _check(usage: Any, quota: Any, **kw: Any) -> list[str]:
        deltas.append(kw)
        return violations

    monkeypatch.setattr(quota_service, "check_quota_delta", _check)
    if violations:
        with pytest.raises(ConflictError):
            quota_service.check_quota_for_provision(_FakeSession(), req)  # type: ignore[arg-type]
    else:
        quota_service.check_quota_for_provision(_FakeSession(), req)  # type: ignore[arg-type]
    assert usage_calls == [{"exclude_request_id": req.id}]
    assert deltas[0] == {
        "delta_cores": 2,
        "delta_memory_mb": 2048,
        "delta_disk_gb": 20,
        "delta_instances": 1,
    }
