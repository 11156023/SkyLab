"""核准重排時，正在 clone 的申請要釘在開始 clone 的節點（SQLite + mock 選點）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.vm import vm_request_placement_service, vm_request_service

NOW = datetime.now(UTC)


def _request(hostname: str, **fields: Any) -> VMRequest:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "reason": "replan",
        "resource_type": "lxc",
        "hostname": hostname,
        "cores": 2,
        "memory": 2048,
        "password": "encrypted",
        "storage": "local-lvm",
        "environment_type": "Test",
        "rootfs_size": 8,
        "start_at": NOW - timedelta(minutes=5),
        "end_at": NOW + timedelta(hours=2),
        "created_at": NOW - timedelta(hours=1),
    }
    values.update(fields)
    return VMRequest(**values)


def _cloning(**fields: Any) -> VMRequest:
    return _request(
        "cloning",
        status=VMRequestStatus.approved,
        assigned_node="pve-a",
        desired_node="pve-a",
        placement_strategy_used="original-strategy",
        provisioning_status=VMProvisioningStatus.running,
        provisioning_started_at=NOW - timedelta(minutes=1),
        reviewed_at=NOW - timedelta(hours=1),
        **fields,
    )


def _fake_select(seen: list[tuple[str, list[str]]], node: str = "pve-b"):
    def select(*, db_request: VMRequest, reserved_requests: list[VMRequest], **_k: Any):
        seen.append(
            (db_request.hostname, [str(item.assigned_node) for item in reserved_requests])
        )
        return SimpleNamespace(
            node=node,
            strategy="new-strategy",
            plan=SimpleNamespace(feasible=True, summary="", rationale=[], warnings=[]),
        )

    return select


def test_rebuild_keeps_cloning_request_on_its_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloning = _cloning()
    later = _request(
        "later",
        status=VMRequestStatus.approved,
        assigned_node="pve-a",
        start_at=NOW,
    )
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "app.services.vm.placement_service.select_reserved_target_node",
        _fake_select(seen),
    )

    selections = vm_request_placement_service.rebuild_reserved_assignments(
        session=None, requests=[later, cloning]
    )

    # 只替還沒開始 clone 的申請重新選點；clone 中的仍算進預留
    assert seen == [("later", ["pve-a"])]
    assert cloning.id not in selections
    assert cloning.assigned_node == "pve-a"
    assert cloning.desired_node == "pve-a"
    assert selections[later.id].node == "pve-b"


def test_rebuild_still_replans_requests_that_are_not_cloning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiting = _request(
        "waiting",
        status=VMRequestStatus.approved,
        assigned_node="pve-a",
        provisioning_status=VMProvisioningStatus.idle,
    )
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "app.services.vm.placement_service.select_reserved_target_node",
        _fake_select(seen),
    )

    selections = vm_request_placement_service.rebuild_reserved_assignments(
        session=None, requests=[waiting]
    )

    assert seen == [("waiting", [])]
    assert selections[waiting.id].node == "pve-b"
    assert waiting.assigned_node == "pve-b"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_approval_replan_does_not_move_cloning_request(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloning = _cloning()
    pending = _request("new", status=VMRequestStatus.pending)
    db.add(cloning)
    db.add(pending)
    db.commit()
    db.refresh(cloning)
    db.refresh(pending)
    monkeypatch.setattr(
        vm_request_service.vm_request_repo,
        "lock_overlapping_vm_requests_for_window",
        lambda **_k: [cloning, pending],
    )
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "app.services.vm.placement_service.select_reserved_target_node",
        _fake_select(seen),
    )

    selection = vm_request_service._approve_and_place(
        session=db, db_request=pending, reviewer_id=uuid.uuid4()
    )

    assert selection is not None and selection.node == "pve-b"
    assert seen == [("new", ["pve-a"])]
    assert cloning.assigned_node == "pve-a"
    assert cloning.desired_node == "pve-a"
    assert cloning.placement_strategy_used == "original-strategy"
    assert cloning.provisioning_status == VMProvisioningStatus.running
    assert pending.status == VMRequestStatus.approved
    assert pending.assigned_node == "pve-b"


def test_review_preview_shows_cloning_request_on_its_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloning = _cloning()
    pending = _request("new", status=VMRequestStatus.pending)
    db.add(cloning)
    db.add(pending)
    db.commit()
    db.refresh(cloning)
    db.refresh(pending)
    repo = vm_request_service.vm_request_repo
    monkeypatch.setattr(
        repo,
        "get_approved_vm_requests_overlapping_window",
        lambda **_k: [cloning],
    )
    monkeypatch.setattr(repo, "list_active_approved_vm_requests", lambda **_k: [])
    monkeypatch.setattr(
        vm_request_service.proxmox_service, "list_nodes", lambda: [{"node": "pve-a"}]
    )
    monkeypatch.setattr(
        vm_request_service.proxmox_service, "list_all_resources", lambda: []
    )
    monkeypatch.setattr(
        vm_request_placement_service, "get_preview_node_scores", lambda **_k: []
    )
    seen: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "app.services.vm.placement_service.select_reserved_target_node",
        _fake_select(seen),
    )
    monkeypatch.setattr(vm_request_service, "require_vm_request_review", lambda _u: None)

    context = vm_request_service.get_review_context(
        session=db, request_id=pending.id, current_user=SimpleNamespace()
    )

    assert context.projected_node == "pve-b"
    by_host = {item.hostname: item for item in context.overlapping_approved_requests}
    assert by_host["cloning"].projected_node == "pve-a"
    assert by_host["cloning"].assigned_node == "pve-a"
