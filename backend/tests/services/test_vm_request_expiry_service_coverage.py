"""Coverage tests for app.services.vm.vm_request_expiry_service (no DB)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models import VMProvisioningStatus
from app.services.vm import vm_request_expiry_service as svc

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_unprovisioned_expiry_returns_zero_when_only_inflight_clones(
    monkeypatch,
) -> None:
    inflight = SimpleNamespace(
        id="req-1",
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=NOW - timedelta(minutes=1),
    )
    monkeypatch.setattr(svc, "_utc_now", lambda: NOW)
    monkeypatch.setattr(svc, "Session", lambda _engine: _FakeSession())
    monkeypatch.setattr(
        svc.vm_request_repo,
        "list_expired_unprovisioned_approved_vm_requests",
        lambda **_kw: [inflight],
    )
    monkeypatch.setattr(
        svc.scheduling_policy, "is_provisioning_stale", lambda *_a, **_kw: False
    )

    def _must_not_mark(**_kw):
        raise AssertionError("in-flight clone must not be expired")

    monkeypatch.setattr(svc.vm_request_repo, "mark_vm_request_expired", _must_not_mark)

    assert svc.process_expired_unprovisioned_requests() == 0
