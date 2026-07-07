"""批次規格調整測試（mock PVE / quota）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ConflictError
from app.services.teaching import batch_spec_service as svc

TEACHER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")


def _targets() -> list[dict]:
    return [
        {"vmid": 101, "node": "pve1", "type": "qemu", "owner_id": uuid.uuid4()},
        {"vmid": 102, "node": "pve1", "type": "lxc", "owner_id": uuid.uuid4()},
    ]


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"config": [], "status": "running"}
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_current_specs",
        lambda node, vmid, rtype: {"cpu": 2, "memory": 2048, "disk": 20},
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "update_config",
        lambda node, vmid, rtype, **params: calls["config"].append((vmid, params)),
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": calls["status"]},
    )
    monkeypatch.setattr(svc, "_check_quota_for_owner", lambda owner_id, **d: None)
    return calls


def test_start_requires_targets() -> None:
    with pytest.raises(BadRequestError):
        svc.start_batch_spec(
            None, vmids=None, group_id=None, cores=4, memory_mb=None, user=TEACHER
        )


def test_start_requires_some_change() -> None:
    with pytest.raises(BadRequestError):
        svc.start_batch_spec(
            None, vmids=[101], group_id=None, cores=None, memory_mb=None,
            user=TEACHER,
        )


def test_qemu_running_marks_needs_restart(harness: dict) -> None:
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=4, memory_mb=4096, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "needs_restart"  # qemu running
    assert stored.items[102].status == "ok"             # lxc 即時生效
    assert (101, {"cores": 4, "memory": 4096}) in harness["config"]


def test_quota_exceeded_marked_and_skips_apply(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _deny(owner_id, **deltas):
        raise ConflictError("配額不足")

    monkeypatch.setattr(svc, "_check_quota_for_owner", _deny)
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=8, memory_mb=None, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert {i.status for i in stored.items.values()} == {"quota_exceeded"}
    assert harness["config"] == []


def test_single_error_does_not_stop_batch(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _update(node, vmid, rtype, **params):
        if vmid == 101:
            raise RuntimeError("pve error")
        harness["config"].append((vmid, params))

    monkeypatch.setattr(svc.proxmox_service, "update_config", _update)
    task = svc._new_task(requested_by=TEACHER.id, targets=_targets())
    asyncio.run(svc._run_batch(task.id, cores=4, memory_mb=None, concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "error"
    assert stored.items[102].status == "ok"


def test_group_id_resolution_filters_none_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """group_id= 路徑：get_member_vmids 回傳 {user_id: vmid|None}，需過濾
    None 並去重，僅剩的 vmid 才進入 targets（間接證明 start_batch_spec 走到
    _resolve_targets 的 group_id 分支並正確解析）。"""
    from app.repositories import group as group_repo

    member_vmids = {uuid.uuid4(): 101, uuid.uuid4(): None, uuid.uuid4(): 101}
    monkeypatch.setattr(
        group_repo, "get_member_vmids", lambda *, session, group_id: member_vmids
    )
    monkeypatch.setattr(
        svc, "require_vm_teaching_access", lambda session, user, vmid: SimpleNamespace(
            user_id=uuid.uuid4()
        )
    )
    monkeypatch.setattr(
        svc.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "type": "qemu"},
    )
    monkeypatch.setattr(svc, "_max_concurrency", lambda: 2)
    submitted: list = []
    monkeypatch.setattr(
        svc.background_tasks,
        "submit_factory",
        lambda coro_factory, **kwargs: submitted.append(kwargs) or "task-id",
    )

    task_id = svc.start_batch_spec(
        None,
        vmids=None,
        group_id=uuid.uuid4(),
        cores=4,
        memory_mb=None,
        user=TEACHER,
    )

    task = svc._tasks.get(task_id)
    assert list(task.items.keys()) == [101]
    assert len(submitted) == 1
