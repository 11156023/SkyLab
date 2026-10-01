"""機器備份／還原：標記過濾、上限、互斥、任務編排與刪除清理（mock PVE）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError, NotFoundError, ProxmoxError
from app.models.task_record import TaskRecord, TaskRecordStatus
from app.services.resource import backup_service, reset_service

CREATED_AT = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
TOKEN = int(CREATED_AT.timestamp())
USER = SimpleNamespace(id=uuid.uuid4(), email="s@campus.edu")
QEMU = {"node": "pve1", "type": "qemu"}
LXC = {"node": "pve1", "type": "lxc"}
STORAGE = "pbs-main"


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Session:
    def __init__(self, records: list[TaskRecord] | None = None) -> None:
        self.records = records or []
        self.rolled_back = False

    def exec(self, stmt: Any) -> Any:
        return SimpleNamespace(all=lambda: self.records)

    def add(self, obj: Any) -> None:
        """測試替身。"""

    def commit(self) -> None:
        """測試替身。"""

    def rollback(self) -> None:
        self.rolled_back = True


def _backup(volid: str, notes: str | None, ctime: int = 1_790_000_000) -> dict[str, Any]:
    item: dict[str, Any] = {
        "volid": volid,
        "vmid": 101,
        "ctime": ctime,
        "size": 1024,
        "format": "pbs-ct",
        "content": "backup",
    }
    if notes is not None:
        item["notes"] = notes
    return item


OWN_OLD = _backup(f"{STORAGE}:backup/ct/101/2026-09-10T00:00:00Z", f"skylab-backup:{TOKEN}", 100)
OWN_NEW = _backup(
    f"{STORAGE}:backup/ct/101/2026-09-20T00:00:00Z",
    f"skylab-backup:{TOKEN} | 升級前",
    200,
)
# 機構自己的排程備份：備註是機器名稱，沒有 SkyLab 標記
INSTITUTIONAL = _backup(f"{STORAGE}:backup/ct/101/2026-09-15T04:00:00Z", "code-server", 150)
# 同一個 VMID 的前一台機器留下的 SkyLab 備份（建立時間不同）
PREVIOUS_OWNER = _backup(
    f"{STORAGE}:backup/ct/101/2026-01-01T00:00:00Z", "skylab-backup:1700000000 | 舊主人", 50
)
NO_NOTES = _backup(f"{STORAGE}:backup/ct/101/2026-09-16T04:00:00Z", None, 160)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "storage": STORAGE,
        "usable": True,
        "backups": [OWN_OLD, INSTITUTIONAL, OWN_NEW, PREVIOUS_OWNER, NO_NOTES],
        "snapshot_feature": False,
        "status": "running",
        "admin": False,
        "limit": 2,
        "created": [],
        "restored": [],
        "deleted": [],
        "control": [],
        "enqueued": [],
        "audits": [],
        "key_sync": [],
        "rootfs": "lvm-data:vm-101-disk-0,size=8G",
        "lxc_config": {"unprivileged": 1},
    }
    pve = backup_service.proxmox_service

    def _usable(node: str, storage: str) -> bool:
        if isinstance(state["usable"], Exception):
            raise state["usable"]
        return bool(state["usable"])

    def _create(node: str, vmid: int, storage: str, **kwargs: Any) -> str:
        if isinstance(state.get("create_error"), Exception):
            raise state["create_error"]
        state["created"].append({"node": node, "vmid": vmid, "storage": storage, **kwargs})
        return "UPID:backup"

    def _restore(node: str, vmid: int, rtype: str, volid: str, **kwargs: Any) -> str:
        if isinstance(state.get("restore_error"), Exception):
            raise state["restore_error"]
        state["restored"].append({"vmid": vmid, "rtype": rtype, "volid": volid, **kwargs})
        return "UPID:restore"

    def _delete(node: str, storage: str, volid: str, **kwargs: Any) -> None:
        if volid in state.get("delete_fails", ()):
            raise RuntimeError("storage busy")
        state["deleted"].append(volid)

    def _enqueue(**kwargs: Any) -> Any:
        state["enqueued"].append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        backup_service,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(backup_storage=state["storage"]),
    )
    monkeypatch.setattr(pve, "storage_accepts_backups", _usable)
    monkeypatch.setattr(pve, "list_backups", lambda node, storage, vmid: list(state["backups"]))
    monkeypatch.setattr(
        pve, "has_snapshot_feature", lambda node, vmid, rtype: state["snapshot_feature"]
    )
    monkeypatch.setattr(pve, "create_backup", _create)
    monkeypatch.setattr(pve, "restore_backup", _restore)
    monkeypatch.setattr(pve, "delete_backup", _delete)
    monkeypatch.setattr(pve, "get_status", lambda node, vmid, rtype: {"status": state["status"]})
    monkeypatch.setattr(
        pve,
        "control",
        lambda node, vmid, rtype, action, **kw: state["control"].append(action),
    )
    monkeypatch.setattr(
        pve,
        "get_config",
        lambda node, vmid, rtype: {"rootfs": state["rootfs"], **state["lxc_config"]},
    )
    monkeypatch.setattr(
        backup_service.resource_repo,
        "get_resource_by_vmid",
        lambda **kw: None
        if state.get("untracked")
        else SimpleNamespace(vmid=kw["vmid"], created_at=CREATED_AT),
    )
    monkeypatch.setattr(
        backup_service.governance_repo,
        "get_governance_config",
        lambda **kw: SimpleNamespace(student_backup_max_count=state["limit"]),
    )
    monkeypatch.setattr(backup_service, "is_admin", lambda user: state["admin"])
    monkeypatch.setattr(backup_service, "enqueue_task_sync", _enqueue)
    monkeypatch.setattr(backup_service.audit_service, "log_action", lambda **kw: None)
    monkeypatch.setattr(
        backup_service,
        "_audit_task",
        lambda vmid, user_id, **kw: state["audits"].append(kw),
    )
    monkeypatch.setattr(
        reset_service,
        "_sync_lxc_platform_key_after_start",
        lambda node, vmid, rtype: state["key_sync"].append((vmid, rtype)),
    )
    return state


# ─── 備註標記 ─────────────────────────────────────────────────────────────────


def test_notes_round_trip_with_and_without_description() -> None:
    assert backup_service.parse_notes(backup_service.build_notes(TOKEN, None)) == (TOKEN, None)
    assert backup_service.parse_notes(backup_service.build_notes(TOKEN, " 升級前 ")) == (
        TOKEN,
        "升級前",
    )


@pytest.mark.parametrize("notes", ["code-server", "", None, 123, "skylab-backup", "x skylab-backup:1"])
def test_parse_notes_rejects_backups_not_made_by_skylab(notes: object) -> None:
    assert backup_service.parse_notes(notes) is None


def test_description_cannot_inject_template_or_extra_lines() -> None:
    """notes-template 會展開 {{變數}} 與反斜線跳脫，換行會把標記擠到第二行以後。"""
    notes = backup_service.build_notes(TOKEN, "a\nskylab-backup:1 {{guestname}} \\n b")
    assert "\n" not in notes and "\\" not in notes and "{" not in notes
    assert backup_service.parse_notes(notes) == (TOKEN, "a skylab-backup:1 guestname n b")


def test_description_is_truncated() -> None:
    cleaned = backup_service.clean_description("x" * 500)
    assert cleaned is not None and len(cleaned) == backup_service.DESCRIPTION_MAX_LENGTH


# ─── 可用性 ───────────────────────────────────────────────────────────────────


def test_capability_not_configured(env: dict[str, Any]) -> None:
    env["storage"] = None
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=QEMU, user=USER
    )
    assert result == {
        "available": False,
        "reason": "not_configured",
        "requires_shutdown": False,
        "max_count": None,
    }


def test_capability_storage_unavailable(env: dict[str, Any]) -> None:
    env["usable"] = False
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=QEMU, user=USER
    )
    assert (result["available"], result["reason"]) == (False, "storage_unavailable")


def test_capability_unknown_when_check_fails(env: dict[str, Any]) -> None:
    env["usable"] = RuntimeError("PVE down")
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=QEMU, user=USER
    )
    assert (result["available"], result["reason"]) == (False, "unknown")


def test_capability_vm_backs_up_online_and_reports_student_limit(env: dict[str, Any]) -> None:
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=QEMU, user=USER
    )
    assert result == {
        "available": True,
        "reason": None,
        "requires_shutdown": False,
        "max_count": 2,
    }


def test_capability_lxc_without_snapshot_support_requires_shutdown(env: dict[str, Any]) -> None:
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=LXC, user=USER
    )
    assert result["requires_shutdown"] is True


def test_capability_lxc_with_snapshot_support_backs_up_online(env: dict[str, Any]) -> None:
    env["snapshot_feature"] = True
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=LXC, user=USER
    )
    assert result["requires_shutdown"] is False


def test_capability_admin_has_no_limit(env: dict[str, Any]) -> None:
    env["admin"] = True
    result = backup_service.get_capability(
        session=_Session(), vmid=101, resource_info=QEMU, user=USER
    )
    assert result["max_count"] is None


# ─── 清單 ─────────────────────────────────────────────────────────────────────


def test_list_only_shows_this_machines_skylab_backups_newest_first(env: dict[str, Any]) -> None:
    """機構的排程備份、沒備註的、以及同 VMID 前一台機器的備份都不能出現。"""
    result = backup_service.list_backups(session=_Session(), vmid=101, resource_info=LXC)
    assert [b["volid"] for b in result] == [OWN_NEW["volid"], OWN_OLD["volid"]]
    assert result[0]["description"] == "升級前"
    assert result[1]["description"] is None
    assert result[0]["created_at"] == 200 and result[0]["size"] == 1024


def test_list_rejected_when_not_configured(env: dict[str, Any]) -> None:
    env["storage"] = None
    with pytest.raises(ConflictError):
        backup_service.list_backups(session=_Session(), vmid=101, resource_info=LXC)


def test_list_rejected_for_untracked_machine(env: dict[str, Any]) -> None:
    env["untracked"] = True
    with pytest.raises(NotFoundError):
        backup_service.list_backups(session=_Session(), vmid=101, resource_info=LXC)


# ─── 建立備份（入列）──────────────────────────────────────────────────────────


def test_start_backup_enqueues_task_with_marker_notes(env: dict[str, Any]) -> None:
    env["limit"] = 3
    task_id = backup_service.start_backup(
        _Session(), vmid=101, resource_info=LXC, user=USER, description="升級前"
    )
    assert uuid.UUID(task_id)
    (call,) = env["enqueued"]
    assert call["task_type"] == backup_service.TASK_BACKUP
    assert call["user_id"] == USER.id
    assert call["payload"] == {
        "vmid": 101,
        "node": "pve1",
        "rtype": "lxc",
        "storage": STORAGE,
        "notes": f"skylab-backup:{TOKEN} | 升級前",
        "user_id": str(USER.id),
    }


def test_start_backup_rejected_at_limit(env: dict[str, Any]) -> None:
    """上限只算這台機器自己的 SkyLab 備份（這裡有 2 份、上限 2）。"""
    with pytest.raises(ConflictError):
        backup_service.start_backup(
            _Session(), vmid=101, resource_info=LXC, user=USER, description=None
        )
    assert env["enqueued"] == []


def test_start_backup_admin_ignores_limit(env: dict[str, Any]) -> None:
    env["admin"] = True
    backup_service.start_backup(
        _Session(), vmid=101, resource_info=LXC, user=USER, description=None
    )
    assert len(env["enqueued"]) == 1


@pytest.mark.parametrize(
    "task_type",
    [reset_service.TASK_RESET, backup_service.TASK_BACKUP, backup_service.TASK_RESTORE],
)
def test_start_backup_rejected_while_another_machine_task_is_active(
    env: dict[str, Any], task_type: str
) -> None:
    env["limit"] = 5
    active = TaskRecord(
        task_type=task_type,
        user_id=uuid.uuid4(),
        payload={"vmid": 101},
        status=TaskRecordStatus.running,
    )
    session = _Session([active])
    with pytest.raises(ConflictError):
        backup_service.start_backup(
            session, vmid=101, resource_info=LXC, user=USER, description=None
        )
    assert env["enqueued"] == []
    assert session.rolled_back is True


def test_active_task_of_another_machine_does_not_block(env: dict[str, Any]) -> None:
    env["limit"] = 5
    other = TaskRecord(
        task_type=backup_service.TASK_BACKUP,
        user_id=uuid.uuid4(),
        payload={"vmid": 999},
        status=TaskRecordStatus.queued,
    )
    backup_service.start_backup(
        _Session([other]), vmid=101, resource_info=LXC, user=USER, description=None
    )
    assert len(env["enqueued"]) == 1


def test_start_backup_rejected_when_not_configured(env: dict[str, Any]) -> None:
    env["storage"] = "  "
    with pytest.raises(ConflictError):
        backup_service.start_backup(
            _Session(), vmid=101, resource_info=LXC, user=USER, description=None
        )


def test_start_backup_reports_502_when_storage_check_fails(env: dict[str, Any]) -> None:
    env["usable"] = RuntimeError("PVE down")
    with pytest.raises(ProxmoxError):
        backup_service.start_backup(
            _Session(), vmid=101, resource_info=LXC, user=USER, description=None
        )
    assert env["enqueued"] == []


# ─── 還原（入列）──────────────────────────────────────────────────────────────


def test_start_restore_enqueues_owned_backup(env: dict[str, Any]) -> None:
    backup_service.start_restore(
        _Session(), vmid=101, resource_info=LXC, user=USER, volid=OWN_OLD["volid"]
    )
    (call,) = env["enqueued"]
    assert call["task_type"] == backup_service.TASK_RESTORE
    assert call["payload"] == {
        "vmid": 101,
        "node": "pve1",
        "rtype": "lxc",
        "volid": OWN_OLD["volid"],
        "user_id": str(USER.id),
    }


@pytest.mark.parametrize(
    "volid",
    [
        INSTITUTIONAL["volid"],
        PREVIOUS_OWNER["volid"],
        NO_NOTES["volid"],
        "pbs-main:backup/ct/999/2026-09-10T00:00:00Z",
        "../../qemu/101",
    ],
)
def test_start_restore_refuses_backups_that_are_not_this_machines(
    env: dict[str, Any], volid: str
) -> None:
    with pytest.raises(NotFoundError):
        backup_service.start_restore(
            _Session(), vmid=101, resource_info=LXC, user=USER, volid=volid
        )
    assert env["enqueued"] == []


# ─── 刪除 ─────────────────────────────────────────────────────────────────────


def test_delete_owned_backup(env: dict[str, Any]) -> None:
    backup_service.delete_backup(
        session=_Session(), vmid=101, resource_info=LXC, user=USER, volid=OWN_NEW["volid"]
    )
    assert env["deleted"] == [OWN_NEW["volid"]]


@pytest.mark.parametrize(
    "volid", [INSTITUTIONAL["volid"], PREVIOUS_OWNER["volid"], "../../qemu/101"]
)
def test_delete_refuses_backups_that_are_not_this_machines(
    env: dict[str, Any], volid: str
) -> None:
    with pytest.raises(NotFoundError):
        backup_service.delete_backup(
            session=_Session(), vmid=101, resource_info=LXC, user=USER, volid=volid
        )
    assert env["deleted"] == []


def test_delete_rejected_while_task_active(env: dict[str, Any]) -> None:
    active = TaskRecord(
        task_type=backup_service.TASK_RESTORE,
        user_id=uuid.uuid4(),
        payload={"vmid": 101},
        status=TaskRecordStatus.queued,
    )
    with pytest.raises(ConflictError):
        backup_service.delete_backup(
            session=_Session([active]),
            vmid=101,
            resource_info=LXC,
            user=USER,
            volid=OWN_NEW["volid"],
        )
    assert env["deleted"] == []


# ─── worker：備份 ─────────────────────────────────────────────────────────────


def _backup_payload(rtype: str) -> dict[str, Any]:
    return {
        "vmid": 101,
        "node": "pve1",
        "rtype": rtype,
        "storage": STORAGE,
        "notes": f"skylab-backup:{TOKEN}",
        "user_id": str(USER.id),
    }


def test_run_backup_vm_uses_online_snapshot_mode(env: dict[str, Any]) -> None:
    result = backup_service.run_backup_task(uuid.uuid4(), _backup_payload("qemu"))
    (call,) = env["created"]
    assert call["mode"] == "snapshot"
    assert call["storage"] == STORAGE
    assert call["notes"] == f"skylab-backup:{TOKEN}"
    assert result == {"vmid": 101, "mode": "snapshot"}
    assert env["audits"][-1]["ok"] is True


def test_run_backup_lxc_without_snapshot_support_uses_stop_mode(env: dict[str, Any]) -> None:
    backup_service.run_backup_task(uuid.uuid4(), _backup_payload("lxc"))
    assert env["created"][0]["mode"] == "stop"


def test_run_backup_lxc_with_snapshot_support_stays_online(env: dict[str, Any]) -> None:
    env["snapshot_feature"] = True
    backup_service.run_backup_task(uuid.uuid4(), _backup_payload("lxc"))
    assert env["created"][0]["mode"] == "snapshot"


def test_run_backup_failure_is_audited_and_reraised(env: dict[str, Any]) -> None:
    env["create_error"] = RuntimeError("vzdump failed")
    with pytest.raises(RuntimeError):
        backup_service.run_backup_task(uuid.uuid4(), _backup_payload("qemu"))
    assert env["audits"][-1]["ok"] is False


# ─── worker：還原 ─────────────────────────────────────────────────────────────


def _restore_payload(rtype: str) -> dict[str, Any]:
    return {
        "vmid": 101,
        "node": "pve1",
        "rtype": rtype,
        "volid": OWN_OLD["volid"],
        "user_id": str(USER.id),
    }


def test_run_restore_running_lxc_stops_restores_to_rootfs_storage_and_restarts(
    env: dict[str, Any],
) -> None:
    """LXC 還原不指定 storage 時 PVE 會放到 local、不指定 unprivileged 可能變特權容器。"""
    backup_service.run_restore_task(uuid.uuid4(), _restore_payload("lxc"))
    assert env["control"] == ["stop", "start"]
    assert env["restored"] == [
        {
            "vmid": 101,
            "rtype": "lxc",
            "volid": OWN_OLD["volid"],
            "storage": "lvm-data",
            "unprivileged": True,
            "wait_timeout_seconds": backup_service.RESTORE_WAIT_SECONDS,
        }
    ]
    assert env["key_sync"] == [(101, "lxc")]
    assert env["audits"][-1]["ok"] is True


def test_run_restore_stopped_vm_stays_stopped_and_keeps_original_storage(
    env: dict[str, Any],
) -> None:
    env["status"] = "stopped"
    backup_service.run_restore_task(uuid.uuid4(), _restore_payload("qemu"))
    assert env["control"] == []
    assert env["restored"][0]["storage"] is None
    assert env["restored"][0]["unprivileged"] is None
    assert env["key_sync"] == []


def test_run_restore_privileged_lxc_stays_privileged(env: dict[str, Any]) -> None:
    env["lxc_config"] = {}
    backup_service.run_restore_task(uuid.uuid4(), _restore_payload("lxc"))
    assert env["restored"][0]["unprivileged"] is False


def test_run_restore_failure_tries_to_power_the_machine_back_on(env: dict[str, Any]) -> None:
    env["restore_error"] = RuntimeError("restore failed")
    with pytest.raises(RuntimeError):
        backup_service.run_restore_task(uuid.uuid4(), _restore_payload("qemu"))
    assert env["control"] == ["stop", "start"]
    assert env["audits"][-1]["ok"] is False


def test_run_restore_failure_on_stopped_machine_does_not_start_it(env: dict[str, Any]) -> None:
    env["status"] = "stopped"
    env["restore_error"] = RuntimeError("restore failed")
    with pytest.raises(RuntimeError):
        backup_service.run_restore_task(uuid.uuid4(), _restore_payload("qemu"))
    assert env["control"] == []


# ─── 任務中心顯示 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("task_type", "kind", "title"),
    [
        (backup_service.TASK_BACKUP, "resource_backup", "備份：VMID 101"),
        (backup_service.TASK_RESTORE, "resource_restore", "還原備份：VMID 101"),
    ],
)
def test_backup_tasks_show_up_as_their_own_job_kind(
    task_type: str, kind: str, title: str
) -> None:
    """沒登記種類的 task_type 會被當成範本任務顯示。"""
    from app.services.jobs import jobs_service

    record = TaskRecord(
        task_type=task_type,
        user_id=uuid.uuid4(),
        payload={"vmid": 101},
        status=TaskRecordStatus.running,
        created_at=CREATED_AT,
    )
    job = jobs_service._task_record_to_job(record)

    assert job.kind.value == kind
    assert job.title == title
    assert set(jobs_service._FETCHERS) == set(jobs_service._DETAIL_FETCHERS)
    assert job.kind in jobs_service._FETCHERS


def test_queue_handlers_are_registered_for_both_tasks() -> None:
    from app.infrastructure.queue import registered_functions
    from app.services.resource import tasks  # noqa: F401  註冊 handler

    names = {getattr(fn, "name", None) for fn in registered_functions()}
    assert {backup_service.TASK_BACKUP, backup_service.TASK_RESTORE} <= names


# ─── 機器刪除時的清理 ─────────────────────────────────────────────────────────


def test_purge_removes_every_skylab_backup_but_never_institutional_ones(
    env: dict[str, Any],
) -> None:
    """機器已不存在：前一台留下的 SkyLab 備份也一併清，機構的備份不碰。"""
    removed = backup_service.purge_backups_for_removed_machine(node="pve1", vmid=101)
    assert removed == 3
    assert set(env["deleted"]) == {
        OWN_OLD["volid"],
        OWN_NEW["volid"],
        PREVIOUS_OWNER["volid"],
    }


def test_purge_continues_after_a_failed_delete(env: dict[str, Any]) -> None:
    env["delete_fails"] = {OWN_OLD["volid"]}
    removed = backup_service.purge_backups_for_removed_machine(node="pve1", vmid=101)
    assert removed == 2
    assert OWN_OLD["volid"] not in env["deleted"]


def test_purge_is_a_noop_without_backup_storage(env: dict[str, Any]) -> None:
    env["storage"] = None
    assert backup_service.purge_backups_for_removed_machine(node="pve1", vmid=101) == 0
    assert env["deleted"] == []


def test_purge_never_raises(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(node: str, storage: str, vmid: int) -> list:
        raise RuntimeError("PVE down")

    monkeypatch.setattr(backup_service.proxmox_service, "list_backups", _boom)
    assert backup_service.purge_backups_for_removed_machine(node="pve1", vmid=101) == 0
