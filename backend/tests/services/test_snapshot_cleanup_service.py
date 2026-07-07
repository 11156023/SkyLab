"""快照自動清理掃描測試（mock DB / PVE / email）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.governance import snapshot_cleanup_service as svc

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def _resource(vmid: int) -> SimpleNamespace:
    return SimpleNamespace(
        vmid=vmid,
        user_id=uuid.uuid4(),
        user=SimpleNamespace(email="s@campus.edu", full_name="學生"),
    )


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"deleted": [], "emails": []}
    config = SimpleNamespace(
        snapshot_cleanup_enabled=True, snapshot_retention_days=7
    )
    monkeypatch.setattr(svc, "_utc_now", lambda: NOW)
    monkeypatch.setattr(svc, "_get_config", lambda session: config)
    monkeypatch.setattr(
        svc, "_list_scan_batch", lambda session, cursor, limit: [_resource(101)]
    )
    monkeypatch.setattr(
        svc,
        "_pve_resource_map",
        lambda: {101: {"vmid": 101, "node": "pve1", "type": "qemu"}},
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: [
            {"name": "old", "snaptime": _ts(10)},
            {"name": "fresh", "snaptime": _ts(1)},
            {"name": "skylab-init", "snaptime": _ts(30)},
        ],
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "delete_snapshot",
        lambda node, vmid, rtype, snapname: calls["deleted"].append(snapname),
    )
    monkeypatch.setattr(svc, "_audit_and_notify", lambda *a, **k: calls[
        "emails"
    ].append(a[1] if len(a) > 1 else None))
    monkeypatch.setattr(svc, "_reset_cursor", lambda: None)
    return calls


def test_only_eligible_snapshots_deleted(harness: dict) -> None:
    deleted = svc.process_snapshot_cleanup()
    assert deleted == 1
    assert harness["deleted"] == ["old"]


def test_disabled_config_noop(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda session: SimpleNamespace(
            snapshot_cleanup_enabled=False, snapshot_retention_days=7
        ),
    )
    assert svc.process_snapshot_cleanup() == 0
    assert harness["deleted"] == []
