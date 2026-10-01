"""稽核修正的回歸測試（services/resource、services/jobs）。

- 已開通／克隆中的排程申請不得被標成「排程超時、仍未開始建立」
- 尚未佈建的申請單也要佔 max_instances
- 轉移擁有權要檢查新擁有者的配額
- 轉出機器後，前擁有者不應出現「建立中」佔位卡
- 同一台機器已有進行中的重置時拒絕再入列
- LXC authorized_keys 讀取失敗時不得覆寫檔案
- Web Push 用的 own_only 讓管理員也只撈本人任務
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import ConflictError, ProxmoxError
from app.models import Resource, VMProvisioningStatus, VMRequest, VMRequestStatus
from app.models.task_record import TaskRecord, TaskRecordStatus
from app.schemas.jobs import JobKind, JobStatus
from app.services.jobs import jobs_service
from app.services.resource import (
    credentials_service,
    quota_service,
    reset_service,
    resource_service,
    sharing_service,
)
from app.services.resource.quota_policy import QuotaUsage


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any:
        return self._rows[0] if self._rows else None




def _scheduled_request(**overrides: object) -> VMRequest:
    values: dict[str, object] = dict(
        user_id=uuid.uuid4(),
        reason="course lab",
        resource_type="vm",
        hostname="lab-vm",
        password="pw",
        status=VMRequestStatus.approved,
        start_at=datetime.now(timezone.utc) - timedelta(hours=41),
    )
    values.update(overrides)
    return VMRequest(**values)


def test_provisioned_request_is_not_overdue() -> None:
    req = _scheduled_request(
        vmid=101, provisioning_status=VMProvisioningStatus.completed
    )

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.completed
    assert item.meta["overdue"] is False
    assert "超時" not in (item.message or "")


def test_cloning_request_is_not_overdue() -> None:
    req = _scheduled_request(vmid=None, provisioning_status=VMProvisioningStatus.running)

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.running
    assert item.meta["overdue"] is False
    assert "超時" not in (item.message or "")


def test_unstarted_request_past_start_at_is_still_overdue() -> None:
    req = _scheduled_request(vmid=None, provisioning_status=None)

    item = jobs_service._vm_request_to_job(req)

    assert item.status == JobStatus.pending
    assert item.meta["overdue"] is True
    assert "仍未開始建立" in (item.message or "")




class _CaptureSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    def exec(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        return _Result([])


def _where_sql(stmt: Any) -> str:
    return str(stmt.whereclause)


def test_admin_sees_all_jobs_by_default() -> None:
    admin = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")
    session = _CaptureSession()

    jobs_service._fetch_vm_requests(
        session,  # type: ignore[arg-type]
        user=admin,  # type: ignore[arg-type]
        since=datetime.now(timezone.utc),
    )

    assert "user_id" not in _where_sql(session.statements[0])


def test_own_only_filters_admin_to_own_jobs_in_sql() -> None:
    admin = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")
    since = datetime.now(timezone.utc)
    for fetch in (
        jobs_service._fetch_vm_requests,
        jobs_service._fetch_spec_changes,
        jobs_service._fetch_deletions,
        jobs_service._FETCHERS[JobKind.template],
    ):
        session = _CaptureSession()
        fetch(session, user=admin, since=since, own_only=True)  # type: ignore[operator]
        assert "user_id" in _where_sql(session.statements[0]), fetch


def test_list_recent_passes_own_only_to_every_fetcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[bool] = []

    def fake_fetch(session: Any, *, user: Any, since: Any, own_only: bool = False):
        seen.append(own_only)
        return []

    monkeypatch.setattr(jobs_service, "_FETCHERS", dict.fromkeys(JobKind, fake_fetch))
    admin = SimpleNamespace(id=uuid.uuid4(), is_superuser=True, role="admin")

    jobs_service.list_recent_for_user(
        session=None,  # type: ignore[arg-type]
        user=admin,  # type: ignore[arg-type]
        limit=20,
        own_only=True,
    )

    assert seen and all(seen)




class _RequestsSession:
    def __init__(self, requests: list[Any]) -> None:
        self.requests = requests

    def exec(self, statement: Any) -> _Result:
        del statement
        return _Result(self.requests)


def _unprovisioned(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "resource_type": "lxc",
        "cores": 1,
        "memory": 512,
        "rootfs_size": 8,
        "disk_size": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _stub_default_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota_service, "_quota_for_user", lambda s, u: None)
    monkeypatch.setattr(quota_service, "_global_quota_row", lambda s: None)


def test_check_quota_counts_pending_requests_as_instances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """預設 max_instances=5：1 台機器＋4 張未佈建的單，第 6 張要被擋。"""
    _stub_default_quota(monkeypatch)
    monkeypatch.setattr(quota_service, "_owned_vmids", lambda s, u: [101])
    monkeypatch.setattr(
        quota_service.proxmox_service,
        "list_all_resources",
        lambda: [{"vmid": 101, "maxcpu": 1, "maxmem": 0, "maxdisk": 0}],
    )
    session = _RequestsSession([_unprovisioned() for _ in range(4)])

    with pytest.raises(ConflictError):
        quota_service.check_quota(
            session,  # type: ignore[arg-type]
            uuid.uuid4(),
            delta_cores=1,
            delta_instances=1,
        )


def test_check_quota_for_provision_counts_sibling_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """並行克隆中的其他申請單（vmid 仍為 NULL）也要互相計入台數。"""
    _stub_default_quota(monkeypatch)
    monkeypatch.setattr(quota_service, "_owned_vmids", lambda s, u: [101])
    monkeypatch.setattr(
        quota_service.proxmox_service,
        "list_all_resources",
        lambda: [{"vmid": 101, "maxcpu": 1, "maxmem": 0, "maxdisk": 0}],
    )
    # 這張單自己由 exclude_request_id 排除，其餘 4 張（假 session 不過濾）
    siblings = [_unprovisioned() for _ in range(4)]
    request = _unprovisioned(user_id=uuid.uuid4())

    with pytest.raises(ConflictError):
        quota_service.check_quota_for_provision(
            _RequestsSession(siblings),  # type: ignore[arg-type]
            request,
        )




def _patch_transfer(
    monkeypatch: pytest.MonkeyPatch, *, owner_id: uuid.UUID, target_id: uuid.UUID
) -> SimpleNamespace:
    resource = SimpleNamespace(
        vmid=300, user_id=owner_id, allocation_scope="personal", teaching_class_id=None
    )
    target = SimpleNamespace(id=target_id, email="target@campus.edu", is_active=True)
    monkeypatch.setattr(
        sharing_service, "_get_personal_resource", lambda session, vmid: resource
    )
    monkeypatch.setattr(
        sharing_service, "_find_target_user", lambda session, email: target
    )
    monkeypatch.setattr(
        sharing_service.share_repo, "get_share", lambda **kwargs: None
    )
    monkeypatch.setattr(
        sharing_service.share_repo, "create_share", lambda **kwargs: None
    )
    monkeypatch.setattr(
        sharing_service.spec_request_repo,
        "cancel_open_spec_change_requests_for_vmid",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr(sharing_service.audit_service, "log_action", lambda **kw: None)
    return resource


class _TransferSession:
    def __init__(self) -> None:
        self.commits = 0

    def get(self, model: Any, key: Any) -> Any:
        return None

    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        self.commits += 1


_LIVE_INFO = {"vmid": 300, "maxcpu": 2, "maxmem": 2 * 1024**3, "maxdisk": 20 * 1024**3}


def test_transfer_blocked_when_target_over_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, target_id = uuid.uuid4(), uuid.uuid4()
    resource = _patch_transfer(monkeypatch, owner_id=owner_id, target_id=target_id)
    _stub_default_quota(monkeypatch)
    monkeypatch.setattr(
        quota_service,
        "get_usage",
        lambda session, user_id, **kw: QuotaUsage(
            cpu_cores=0, memory_mb=0, disk_gb=0, instances=5
        ),
    )
    session = _TransferSession()
    actor = SimpleNamespace(id=owner_id, email="a@campus.edu", role="student",
                            is_superuser=False)

    with pytest.raises(ConflictError):
        sharing_service.transfer_ownership(
            session=session,  # type: ignore[arg-type]
            vmid=300,
            actor=actor,
            email="target@campus.edu",
            keep_access=True,
            resource_info=_LIVE_INFO,
        )

    assert resource.user_id == owner_id
    assert session.commits == 0


def test_transfer_checks_target_quota_with_live_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id, target_id = uuid.uuid4(), uuid.uuid4()
    resource = _patch_transfer(monkeypatch, owner_id=owner_id, target_id=target_id)
    calls: list[tuple[Any, dict[str, int]]] = []
    monkeypatch.setattr(
        quota_service,
        "check_quota",
        lambda session, user_id, **kw: calls.append((user_id, kw)),
    )
    actor = SimpleNamespace(id=owner_id, email="a@campus.edu", role="student",
                            is_superuser=False)

    sharing_service.transfer_ownership(
        session=_TransferSession(),  # type: ignore[arg-type]
        vmid=300,
        actor=actor,
        email="target@campus.edu",
        keep_access=False,
        resource_info=_LIVE_INFO,
    )

    assert calls == [
        (
            target_id,
            {
                "delta_cores": 2,
                "delta_memory_mb": 2048,
                "delta_disk_gb": 20,
                "delta_instances": 1,
            },
        )
    ]
    assert resource.user_id == target_id


def test_admin_transfer_skips_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id, target_id = uuid.uuid4(), uuid.uuid4()
    resource = _patch_transfer(monkeypatch, owner_id=owner_id, target_id=target_id)

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("admin transfer must not check quota")

    monkeypatch.setattr(quota_service, "check_quota", _fail)
    admin = SimpleNamespace(id=uuid.uuid4(), email="root@campus.edu", role="admin",
                            is_superuser=True)

    sharing_service.transfer_ownership(
        session=_TransferSession(),  # type: ignore[arg-type]
        vmid=300,
        actor=admin,
        email="target@campus.edu",
        keep_access=False,
        resource_info=_LIVE_INFO,
    )

    assert resource.user_id == target_id




class _EntitySession:
    """依查詢的主體 model 回傳對應的列；其餘查詢一律空。"""

    def __init__(self, rows: dict[type, list[Any]]) -> None:
        self.rows = rows

    def exec(self, stmt: Any) -> _Result:
        descriptions = getattr(stmt, "column_descriptions", None) or []
        entity = descriptions[0].get("entity") if descriptions else None
        return _Result(list(self.rows.get(entity, [])))

    def get(self, model: Any, key: Any) -> Any:
        return None

    def rollback(self) -> None:
        pass


def _patch_empty_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: [],
    )
    monkeypatch.setattr(
        resource_service.proxmox_service, "list_all_resources", lambda: []
    )


def test_transferred_machine_has_no_placeholder_for_previous_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_owner, new_owner = uuid.uuid4(), uuid.uuid4()
    _patch_empty_live(monkeypatch)
    req = _scheduled_request(
        user_id=old_owner,
        vmid=500,
        provisioning_status=VMProvisioningStatus.completed,
    )
    session = _EntitySession(
        {
            VMRequest: [req],
            Resource: [SimpleNamespace(vmid=500, user_id=new_owner)],
        }
    )

    result = resource_service.list_by_user(
        session=session,  # type: ignore[arg-type]
        user_id=old_owner,
    )

    assert [item for item in result if item.vmid == 500] == []


def test_cloning_request_without_resource_row_keeps_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = uuid.uuid4()
    _patch_empty_live(monkeypatch)
    req = _scheduled_request(
        user_id=owner, vmid=501, provisioning_status=VMProvisioningStatus.running
    )
    session = _EntitySession({VMRequest: [req], Resource: []})

    result = resource_service.list_by_user(
        session=session,  # type: ignore[arg-type]
        user_id=owner,
    )

    placeholders = [item for item in result if item.vmid == 501]
    assert len(placeholders) == 1
    assert placeholders[0].is_placeholder is True




def _raw_reset_record(
    payload: Any, status: TaskRecordStatus = TaskRecordStatus.queued
) -> TaskRecord:
    return TaskRecord(
        task_type=reset_service.TASK_RESET,
        user_id=uuid.uuid4(),
        payload=payload,
        status=status,
    )


def _reset_record(vmid: int, status: TaskRecordStatus) -> TaskRecord:
    # payload 是 JSON 欄位，ORM 從資料庫讀回來就是 dict
    return _raw_reset_record({"vmid": vmid, "node": "pve1", "rtype": "qemu"}, status)


class _ResetSession:
    def __init__(self, records: list[TaskRecord]) -> None:
        self.records = records
        self.rolled_back = False

    def exec(self, stmt: Any) -> _Result:
        return _Result(self.records)

    def add(self, obj: Any) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self.rolled_back = True


def test_start_reset_rejects_when_same_vmid_already_resetting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reset_service, "require_snapshot_available", lambda *_: None)
    monkeypatch.setattr(reset_service, "_has_init_snapshot", lambda *_: True)
    monkeypatch.setattr(reset_service.audit_service, "log_action", lambda **_: None)
    enqueued: list[Any] = []
    monkeypatch.setattr(
        reset_service, "enqueue_task_sync", lambda **kw: enqueued.append(kw)
    )
    session = _ResetSession([_reset_record(101, TaskRecordStatus.queued)])

    with pytest.raises(ConflictError):
        reset_service.start_reset(
            session,  # type: ignore[arg-type]
            vmid=101,
            resource_info={"node": "pve1", "type": "qemu"},
            user=SimpleNamespace(id=uuid.uuid4()),
        )

    assert enqueued == []
    assert session.rolled_back is True


def test_start_reset_allowed_for_other_vmid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reset_service, "require_snapshot_available", lambda *_: None)
    monkeypatch.setattr(reset_service, "_has_init_snapshot", lambda *_: True)
    audit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        reset_service.audit_service, "log_action", lambda **kw: audit_calls.append(kw)
    )
    monkeypatch.setattr(
        reset_service,
        "enqueue_task_sync",
        lambda **kw: SimpleNamespace(id=uuid.uuid4()),
    )
    session = _ResetSession([_reset_record(202, TaskRecordStatus.running)])

    task_id = reset_service.start_reset(
        session,  # type: ignore[arg-type]
        vmid=101,
        resource_info={"node": "pve1", "type": "qemu"},
        user=SimpleNamespace(id=uuid.uuid4()),
    )

    assert uuid.UUID(task_id)
    # 稽核不能自己 commit，否則會提前釋放 advisory xact lock
    assert audit_calls and audit_calls[0]["commit"] is False


def test_has_active_reset_ignores_malformed_payload() -> None:
    malformed = [
        _raw_reset_record("not json"),
        _raw_reset_record(json.dumps([8])),
        _raw_reset_record({"vmid": "x"}),
        _raw_reset_record({}),
    ]
    session = _ResetSession([*malformed, _reset_record(7, TaskRecordStatus.queued)])

    assert reset_service._has_active_reset(session, 7) is True  # type: ignore[arg-type]
    assert reset_service._has_active_reset(session, 8) is False  # type: ignore[arg-type]


def test_has_active_reset_reads_legacy_string_payload() -> None:
    legacy = _raw_reset_record(json.dumps({"vmid": 7, "node": "pve1"}))
    session = _ResetSession([legacy])

    assert reset_service._has_active_reset(session, 7) is True  # type: ignore[arg-type]
    assert reset_service._has_active_reset(session, 8) is False  # type: ignore[arg-type]




def test_add_key_does_not_overwrite_when_lxc_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    def fake_exec(node: str, vmid: int, command: str, **kw: Any):
        commands.append(command)
        if command.startswith("cat "):
            raise RuntimeError("ssh timeout")
        return 0, "", ""

    monkeypatch.setattr(credentials_service.guest, "exec_lxc", fake_exec)
    monkeypatch.setattr(
        credentials_service, "_get_db_resource", lambda session, vmid: SimpleNamespace()
    )
    monkeypatch.setattr(credentials_service.audit_service, "log_action", lambda **kw: None)

    with pytest.raises(ProxmoxError):
        credentials_service.add_authorized_key(
            session=None,  # type: ignore[arg-type]
            vmid=120,
            resource_info={"node": "pve1", "type": "lxc", "status": "running"},
            user_id=uuid.uuid4(),
            public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyForTest user@x",
        )

    assert len(commands) == 1
    assert commands[0].startswith("cat ")


def test_get_credentials_reader_stays_lenient(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exec(node: str, vmid: int, command: str, **kw: Any):
        raise RuntimeError("ssh timeout")

    monkeypatch.setattr(credentials_service.guest, "exec_lxc", fake_exec)

    keys = credentials_service._lxc_authorized_keys(
        {"node": "pve1", "type": "lxc", "status": "running"}, 120
    )

    assert keys == []
