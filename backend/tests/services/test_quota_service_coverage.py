"""Coverage tests for app.services.resource.quota_service (global quota timestamp, listing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

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
