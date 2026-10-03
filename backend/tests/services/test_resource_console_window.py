"""Console access must follow the personal resource's usage deadline."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.exceptions import PermissionDeniedError
from app.services.resource import access, resource_service


def _setup(monkeypatch, *, resource, reason=None, admin=False):
    monkeypatch.setattr(access, "require_resource_use", lambda **_: None)
    monkeypatch.setattr(access, "can_bypass_resource_ownership", lambda _: admin)
    monkeypatch.setattr(
        access.resource_repo, "get_resource_by_vmid", lambda **_: resource
    )
    monkeypatch.setattr(
        resource_service,
        "start_window_state",
        lambda **_: (reason, None, None),
    )


def test_ended_usage_window_denies_new_console_connection(monkeypatch) -> None:
    _setup(
        monkeypatch,
        resource=SimpleNamespace(teaching_class_id=None, expiry_date=None),
        reason="window_ended",
    )
    with pytest.raises(PermissionDeniedError):
        access.require_resource_console_access(
            session=object(), user=object(), vmid=203
        )


def test_active_usage_window_allows_console_connection(monkeypatch) -> None:
    _setup(
        monkeypatch,
        resource=SimpleNamespace(teaching_class_id=None, expiry_date=None),
    )
    access.require_resource_console_access(session=object(), user=object(), vmid=203)


def test_expired_ttl_denies_console_connection(monkeypatch) -> None:
    _setup(
        monkeypatch,
        resource=SimpleNamespace(
            teaching_class_id=None,
            expiry_date=(datetime.now(UTC) - timedelta(days=1)).date(),
        ),
    )
    with pytest.raises(PermissionDeniedError):
        access.require_resource_console_access(
            session=object(), user=object(), vmid=203
        )


def test_administrator_can_access_expired_console_for_recovery(monkeypatch) -> None:
    _setup(monkeypatch, resource=None, admin=True)
    access.require_resource_console_access(session=object(), user=object(), vmid=203)


def test_class_resource_uses_class_access_policy(monkeypatch) -> None:
    _setup(
        monkeypatch,
        resource=SimpleNamespace(teaching_class_id="class-1", expiry_date=None),
        reason="window_ended",
    )
    access.require_resource_console_access(session=object(), user=object(), vmid=203)
