"""Coverage tests for app.services.network.platform_entry_service error paths."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError
from app.repositories import platform_entry as repo
from app.services.network import (
    cloudflare_service,
    platform_entry_service,
    reverse_proxy_service,
)


def test_upstream_probe_requires_a_host() -> None:
    with pytest.raises(BadRequestError):
        platform_entry_service.test_upstream(object(), "   ", 8082)


def test_restore_previous_stops_when_db_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rolled_back: list[bool] = []
    synced: list[Any] = []

    def broken_upsert(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(repo, "upsert_platform_entry_config", broken_upsert)
    monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", synced.append)
    session = SimpleNamespace(rollback=lambda: rolled_back.append(True))

    platform_entry_service._restore_previous(session, {"domain": "a"}, resync=True)

    assert rolled_back == [True]
    assert synced == []


def test_restore_previous_logs_when_resync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restored: list[dict[str, Any]] = []
    monkeypatch.setattr(
        repo,
        "upsert_platform_entry_config",
        lambda _session, **kwargs: restored.append(kwargs),
    )

    def broken_sync(_session: Any) -> None:
        raise RuntimeError("nginx -t failed")

    monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", broken_sync)

    # 沒有 rollback 的 session 也要能還原
    platform_entry_service._restore_previous(object(), {"domain": "a"}, resync=True)

    assert restored == [{"domain": "a"}]


def test_dns_status_reports_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_find(**_kwargs: Any) -> None:
        raise RuntimeError("cloudflare 500")

    monkeypatch.setattr(cloudflare_service, "find_dns_record", broken_find)
    config = SimpleNamespace(
        dns_record_id="rec", domain="skylab.example.edu", dns_zone_id="zone"
    )

    status = platform_entry_service._dns_status(object(), config)  # type: ignore[arg-type]

    assert set(status) == {"dns_detail"}
    assert "cloudflare 500" in status["dns_detail"]


def test_managed_dns_zone_is_empty_without_domain() -> None:
    assert platform_entry_service._managed_dns_zone(object(), "") == ""
