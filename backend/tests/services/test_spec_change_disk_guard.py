"""Regression tests for the disk-resize guard in ``_apply_spec_changes``.

Proxmox resize takes a DELTA. The old code treated an unknown
``current_disk`` (None) as 0, turning the requested TOTAL size into a delta —
over-growing the disk to current + requested. Non-positive deltas (possible
via ``combined`` requests, which skip the growth validation at create time)
must also be rejected.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.services.vm import spec_change_service as scs


@pytest.fixture()
def fake_proxmox(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(resize=[], update_config=[])

    fake = SimpleNamespace(
        find_resource=lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
        update_config=lambda *a, **k: calls.update_config.append((a, k)),
        resize_disk=lambda *a, **k: calls.resize.append((a, k)),
        get_current_specs=lambda *a, **k: {},
    )
    monkeypatch.setattr(scs, "proxmox_service", fake)
    return calls


def _request(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "vmid": 150,
        "requested_cpu": None,
        "requested_memory": None,
        "requested_disk": None,
        "current_cpu": 2,
        "current_memory": 2048,
        "current_disk": 20,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_unknown_current_disk_is_rejected(fake_proxmox: SimpleNamespace) -> None:
    req = _request(requested_disk=30, current_disk=None)

    with pytest.raises(ProxmoxError):
        scs._apply_spec_changes(db_request=req)

    assert fake_proxmox.resize == []  # nothing was applied


def test_non_positive_disk_delta_is_rejected(fake_proxmox: SimpleNamespace) -> None:
    req = _request(requested_disk=20, current_disk=20)

    with pytest.raises(ProxmoxError):
        scs._apply_spec_changes(db_request=req)

    assert fake_proxmox.resize == []


def test_valid_disk_growth_applies_delta(fake_proxmox: SimpleNamespace) -> None:
    req = _request(requested_disk=30, current_disk=20)

    changes = scs._apply_spec_changes(db_request=req)

    assert fake_proxmox.resize == [(("pve1", 150, "qemu", "scsi0", "+10G"), {})]
    assert any("Disk" in c for c in changes)
