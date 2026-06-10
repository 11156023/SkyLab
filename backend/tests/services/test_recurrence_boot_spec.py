"""Regression tests for the scheduled-boot ``_BootSpec`` snapshot path.

``process_scheduled_boot`` snapshots VMRequest fields into plain ``_BootSpec``
values while the session is open, because commits inside
``_filter_due_for_boot`` expire the ORM objects (DetachedInstanceError after
the session closes). ``_boot_one`` therefore consumes only plain values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from app.services.scheduling import recurrence_scheduler as rs


class _FakeProxmox:
    def __init__(self) -> None:
        self.control_calls: list[tuple[str, int, str, str]] = []

    def control(self, node: str, vmid: int, rtype: str, action: str) -> None:
        self.control_calls.append((node, vmid, rtype, action))


class _FakeSessionCtx:
    """Stand-in for ``Session(engine)`` used as a context manager."""

    def __enter__(self) -> _FakeSessionCtx:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _spec(**overrides: Any) -> rs._BootSpec:
    defaults: dict[str, Any] = {
        "request_id": uuid4(),
        "vmid": 150,
        "node": "pve1",
        "window_end": datetime.now(UTC) + timedelta(hours=1),
        "resource_type": "qemu",
    }
    defaults.update(overrides)
    return rs._BootSpec(**defaults)


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    proxmox = _FakeProxmox()
    auto_stops: list[dict[str, Any]] = []

    def fake_set_auto_stop(
        *, session: Any, vmid: int, auto_stop_at: Any, auto_stop_reason: Any  # noqa: ARG001
    ) -> None:
        auto_stops.append(
            {"vmid": vmid, "at": auto_stop_at, "reason": auto_stop_reason}
        )

    monkeypatch.setattr(rs, "proxmox_service", proxmox)
    monkeypatch.setattr(rs, "Session", lambda engine: _FakeSessionCtx())
    monkeypatch.setattr(rs.resource_repo, "set_auto_stop", fake_set_auto_stop)
    return {"proxmox": proxmox, "auto_stops": auto_stops}


def test_boot_one_starts_vm_and_sets_grace_stop(fake_env: dict[str, Any]) -> None:
    window_end = datetime.now(UTC) + timedelta(hours=2)
    grace = timedelta(minutes=30)
    spec = _spec(window_end=window_end)

    rs._boot_one(spec=spec, grace=grace)

    assert fake_env["proxmox"].control_calls == [("pve1", 150, "qemu", "start")]
    assert fake_env["auto_stops"] == [
        {"vmid": 150, "at": window_end + grace, "reason": "window_grace"}
    ]


def test_boot_one_without_node_does_nothing(fake_env: dict[str, Any]) -> None:
    rs._boot_one(spec=_spec(node=None), grace=timedelta(minutes=30))

    assert fake_env["proxmox"].control_calls == []
    assert fake_env["auto_stops"] == []


def test_boot_one_without_window_end_skips_auto_stop(
    fake_env: dict[str, Any],
) -> None:
    rs._boot_one(spec=_spec(window_end=None), grace=timedelta(minutes=30))

    assert fake_env["proxmox"].control_calls == [("pve1", 150, "qemu", "start")]
    assert fake_env["auto_stops"] == []


def test_boot_spec_is_plain_data() -> None:
    """The snapshot must not hold ORM state — frozen dataclass of plain values."""
    spec = _spec()
    with pytest.raises(AttributeError):  # frozen
        spec.vmid = 999  # type: ignore[misc]
