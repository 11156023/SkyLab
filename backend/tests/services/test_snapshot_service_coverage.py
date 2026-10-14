"""Coverage tests for app.services.network.snapshot_service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.network import snapshot_service

INFO = {"node": "pve1", "type": "qemu"}
STUDENT = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")


class _FakeSession:
    def add(self, obj) -> None:
        """測試替身：不需實作。"""

    def commit(self) -> None:
        """測試替身：不需實作。"""


def test_snapshot_max_count_reads_governance_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        snapshot_service.governance_repo,
        "get_governance_config",
        lambda *, session: SimpleNamespace(student_snapshot_max_count="4"),
    )
    assert snapshot_service._snapshot_max_count(_FakeSession()) == 4


def test_list_snapshots_hides_current_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "list_snapshots",
        lambda node, vmid, rtype: [
            {"name": "current"},
            {"name": "before-lab", "description": "d", "snaptime": 1, "vmstate": 1},
        ],
    )

    snapshots = snapshot_service.list_snapshots(vmid=101, resource_info=INFO)

    assert snapshots == [
        {"name": "before-lab", "description": "d", "snaptime": 1, "vmstate": 1}
    ]


def test_create_snapshot_passes_description_and_vmstate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict] = []
    monkeypatch.setattr(snapshot_service, "require_snapshot_available", lambda *a: None)
    monkeypatch.setattr(snapshot_service, "_is_admin", lambda user: False)
    monkeypatch.setattr(snapshot_service, "_snapshot_max_count", lambda session: 3)
    monkeypatch.setattr(
        snapshot_service.proxmox_service, "list_snapshots", lambda *a: []
    )
    monkeypatch.setattr(
        snapshot_service.proxmox_service,
        "create_snapshot",
        lambda node, vmid, rtype, **params: created.append(params) or "UPID:x",
    )
    monkeypatch.setattr(
        snapshot_service.audit_service, "log_action", lambda **kwargs: None
    )

    snapshot_service.create_snapshot(
        session=_FakeSession(),
        vmid=101,
        snapname="lab1",
        description="before lab",
        vmstate=True,
        resource_info=INFO,
        user=STUDENT,
    )

    assert created == [{"snapname": "lab1", "description": "before lab", "vmstate": 1}]
