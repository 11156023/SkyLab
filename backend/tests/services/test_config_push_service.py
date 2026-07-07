"""配置分發編排測試（mock guest 寫入與權限）。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import AppError, BadRequestError, PermissionDeniedError
from app.services.teaching import config_push_service as svc

TEACHER = SimpleNamespace(id=uuid.uuid4(), email="t@campus.edu")


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> dict:
    calls: dict = {"qemu": [], "lxc": []}
    monkeypatch.setattr(
        svc,
        "_resolve_targets",
        lambda session, vmids, user: [
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    monkeypatch.setattr(svc, "_max_concurrency", lambda: 4)
    monkeypatch.setattr(
        svc.guest,
        "write_file_qemu",
        lambda node, vmid, path, content: calls["qemu"].append(vmid),
    )
    monkeypatch.setattr(
        svc.guest,
        "write_file_lxc",
        lambda node, vmid, path, content: calls["lxc"].append(vmid),
    )
    return calls


def test_start_push_rejects_oversize(harness: dict) -> None:
    big = b"x" * (svc.guest.MAX_CONFIG_FILE_BYTES + 1)
    with pytest.raises(AppError) as excinfo:
        svc.start_push(
            None, content=big, file_name="a.conf",
            target_path="/etc/a.conf", vmids=[101], user=TEACHER,
        )
    assert excinfo.value.status_code == 413


def test_start_push_rejects_empty_vmids(harness: dict) -> None:
    with pytest.raises(BadRequestError):
        svc.start_push(
            None, content=b"data", file_name="a.conf",
            target_path="/etc/a.conf", vmids=[], user=TEACHER,
        )


def test_run_push_fans_out_and_records_results(harness: dict) -> None:
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    asyncio.run(
        svc._run_push(task.id, b"data", "/etc/a.conf", concurrency=2)
    )
    stored = svc._tasks.get(task.id)
    assert stored is not None
    assert {i.status for i in stored.items.values()} == {"ok"}
    assert harness["qemu"] == [101]
    assert harness["lxc"] == [102]


def test_run_push_single_failure_does_not_stop_batch(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(node, vmid, path, content):
        raise RuntimeError("agent down")

    monkeypatch.setattr(svc.guest, "write_file_qemu", _fail)
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[
            {"vmid": 101, "node": "pve1", "type": "qemu"},
            {"vmid": 102, "node": "pve1", "type": "lxc"},
        ],
    )
    asyncio.run(svc._run_push(task.id, b"data", "/etc/a.conf", concurrency=2))
    stored = svc._tasks.get(task.id)
    assert stored.items[101].status == "error"
    assert "agent down" in (stored.items[101].error or "")
    assert stored.items[102].status == "ok"


def test_get_push_status_requires_owner_or_admin(harness: dict) -> None:
    task = svc._new_task(
        requested_by=TEACHER.id, file_name="a.conf", target_path="/etc/a.conf",
        targets=[{"vmid": 101, "node": "pve1", "type": "qemu"}],
    )
    stranger = SimpleNamespace(id=uuid.uuid4(), is_superuser=False, role="student")
    with pytest.raises(PermissionDeniedError):
        svc.get_push_status(task.id, stranger)
