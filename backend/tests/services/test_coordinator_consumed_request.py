"""排程器對「已消耗」申請單（使用者刪機 / 轉範本 → provisioning_status=failed）的防護。

1. 已開通分支：鎖定重讀後若申請單已標 failed，不得再寫回 completed，
   否則下個 tick 會把它當成活單、發現機器不見而重新 clone（機器復活）。
2. process_due_request_stops：
   - 查詢必須排除 failed，刪機後不會再把 vmid 清掉
   - 機器真的不在 Proxmox 時標 failed 並保留 vmid，不清 vmid
     （approved 且 vmid 為空在前端會變成「建立中」placeholder）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.exceptions import NotFoundError
from app.models import VMProvisioningStatus, VMRequest, VMRequestStatus
from app.services.scheduling import coordinator

DELETED_MARKER = "Resource deleted by user"


class _FakeSession:
    def __init__(self) -> None:
        self.added: list = []
        self.commits = 0

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass


class _FakeExecResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeScopedSession(_FakeSession):
    """`with Session(engine) as session` 用的假 session，回傳固定的撈單結果。"""

    def __init__(self, rows: list) -> None:
        super().__init__()
        self.rows = rows
        self.statements: list = []

    def __enter__(self) -> _FakeScopedSession:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def exec(self, statement):
        self.statements.append(statement)
        return _FakeExecResult(self.rows)


def _request(**overrides) -> VMRequest:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "hostname": "test-123",
        "resource_type": "vm",
        "status": VMRequestStatus.approved,
        "vmid": 480,
        "cores": 4,
        "memory": 4096,
    }
    defaults.update(overrides)
    return VMRequest(**defaults)


class TestEnsureRequestRunningSkipsConsumed:
    def test_consumed_request_is_not_started_nor_rewritten(self, monkeypatch) -> None:
        # 模擬本 tick 撈單後，刪機流程已把申請單標成已消耗並 commit：
        # 鎖定重讀拿到的是 failed + marker
        consumed = _request(
            provisioning_status=VMProvisioningStatus.failed,
            provisioning_error=DELETED_MARKER,
            review_comment=DELETED_MARKER,
            resource_warning=DELETED_MARKER,
        )
        session = _FakeSession()
        calls: list[str] = []

        monkeypatch.setattr(
            coordinator,
            "_refresh_actual_node",
            lambda *, session, request: ("pve205", {}),
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "get_vm_request_by_id",
            lambda **kwargs: consumed,
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "update_vm_request_provisioning",
            lambda **kwargs: calls.append("update"),
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "get_status",
            lambda node, vmid, rtype: calls.append("get_status") or {"status": "stopped"},
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda *a, **k: calls.append("start"),
        )
        monkeypatch.setattr(
            coordinator.audit_service, "log_action", lambda **kwargs: None
        )

        started = coordinator._ensure_request_running(
            session=session, request=consumed
        )

        assert started is False
        assert calls == []
        assert consumed.provisioning_status == VMProvisioningStatus.failed
        assert consumed.provisioning_error == DELETED_MARKER

    def test_active_request_still_gets_started(self, monkeypatch) -> None:
        # 使用者按了重試：provisioning_status 被重設為 pending，代表還欠一次開機
        req = _request(provisioning_status=VMProvisioningStatus.pending)
        session = _FakeSession()
        calls: list[str] = []

        monkeypatch.setattr(
            coordinator,
            "_refresh_actual_node",
            lambda *, session, request: ("pve205", {}),
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "get_vm_request_by_id",
            lambda **kwargs: req,
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "update_vm_request_provisioning",
            lambda **kwargs: calls.append("update"),
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "get_status",
            lambda node, vmid, rtype: {"status": "stopped"},
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda *a, **k: calls.append("start"),
        )
        monkeypatch.setattr(
            coordinator.audit_service, "log_action", lambda **kwargs: None
        )

        started = coordinator._ensure_request_running(
            session=session, request=req
        )

        assert started is True
        assert calls == ["start", "update"]

    def test_provisioned_request_is_not_restarted_after_shutdown(
        self, monkeypatch
    ) -> None:
        """已開過機的單（completed）機器被關掉後，排程不能再把它開回來。

        回歸背景：沒有結束時間的立即模式與週期性時段的申請單會一直留在使用中
        集合，排程每分鐘都把沒在跑的機器開回來，蓋掉使用者手動關機、時段外
        自動關機與閒置／到期關機。
        """
        req = _request(provisioning_status=VMProvisioningStatus.completed)

        monkeypatch.setattr(
            coordinator,
            "_refresh_actual_node",
            lambda *, session, request: ("pve205", {"status": "stopped"}),
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "get_vm_request_by_id",
            lambda **kwargs: req,
        )

        def _no_pve_call(*_a, **_k):
            raise AssertionError("已開過機的單不該再查狀態或送 start")

        monkeypatch.setattr(coordinator.proxmox_service, "get_status", _no_pve_call)
        monkeypatch.setattr(coordinator.proxmox_service, "control", _no_pve_call)

        started = coordinator._ensure_request_running(
            session=_FakeSession(), request=req
        )

        assert started is False
        assert req.provisioning_status == VMProvisioningStatus.completed

    def test_gpu_waiting_request_keeps_retrying_the_start(self, monkeypatch) -> None:
        req = _request(
            provisioning_status=VMProvisioningStatus.completed,
            resource_warning=coordinator.GPU_WAIT_WARNING,
        )
        calls: list[str] = []
        monkeypatch.setattr(
            coordinator,
            "_refresh_actual_node",
            lambda *, session, request: ("pve205", {"status": "stopped"}),
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kwargs: req
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "update_vm_request_provisioning",
            lambda **kwargs: None,
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "get_status",
            lambda node, vmid, rtype: {"status": "stopped"},
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda *a, **k: calls.append("start"),
        )
        monkeypatch.setattr(
            coordinator.audit_service, "log_action", lambda **kwargs: None
        )

        assert coordinator._ensure_request_running(
            session=_FakeSession(), request=req
        ) is True
        assert calls == ["start"]
        assert req.resource_warning is None


class TestRefreshActualNodeKeepsOwedStart:
    def test_pending_status_survives_node_refresh(self, monkeypatch) -> None:
        """重試留下的 pending 不能在節點同步時被改成 completed（否則就不會開機）。"""
        req = _request(provisioning_status=VMProvisioningStatus.pending)
        req.hostname = "vm-test"
        writes: list[dict] = []

        monkeypatch.setattr(
            coordinator.vm_request_repo, "get_vm_request_by_id", lambda **kwargs: req
        )
        monkeypatch.setattr(
            coordinator.scheduling_support,
            "find_resource_strict",
            lambda vmid: {"node": "pve9", "name": "vm-test", "status": "stopped"},
        )
        monkeypatch.setattr(
            coordinator.vm_request_repo,
            "update_vm_request_provisioning",
            lambda **kwargs: writes.append(kwargs),
        )

        coordinator._refresh_actual_node(session=_FakeSession(), request=req)

        assert writes and writes[0]["provisioning_status"] is None
        assert req.provisioning_status == VMProvisioningStatus.pending


class TestProcessDueRequestStops:
    def test_query_excludes_failed_requests(self, monkeypatch) -> None:
        fake = _FakeScopedSession(rows=[])
        monkeypatch.setattr(coordinator, "Session", lambda engine: fake)

        assert coordinator.process_due_request_stops() == 0

        assert len(fake.statements) == 1
        sql = str(fake.statements[0])
        assert "vm_requests.provisioning_status !=" in sql
        assert "vm_requests.vmid IS NOT NULL" in sql

    def test_vanished_vm_marks_request_failed_but_keeps_vmid(self, monkeypatch) -> None:
        req = _request(end_at=datetime.now(UTC) - timedelta(hours=1))
        fake = _FakeScopedSession(rows=[req])
        monkeypatch.setattr(coordinator, "Session", lambda engine: fake)

        def _missing(vmid: int) -> dict:
            raise NotFoundError(f"Resource {vmid} not found")

        monkeypatch.setattr(
            coordinator.scheduling_support, "find_resource_strict", _missing
        )

        stopped = coordinator.process_due_request_stops()

        assert stopped == 0
        assert req.vmid == 480
        assert req.provisioning_status == VMProvisioningStatus.failed
        assert req.provisioning_error is not None
        assert "no longer exists" in req.provisioning_error
        assert fake.commits == 1

    def test_running_vm_past_end_at_is_shut_down(self, monkeypatch) -> None:
        req = _request(end_at=datetime.now(UTC) - timedelta(hours=1))
        fake = _FakeScopedSession(rows=[req])
        actions: list[str] = []
        monkeypatch.setattr(coordinator, "Session", lambda engine: fake)
        monkeypatch.setattr(
            coordinator.scheduling_support,
            "find_resource_strict",
            lambda vmid: {"node": "pve205", "vmid": vmid},
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "get_status",
            lambda node, vmid, rtype: {"status": "running"},
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda node, vmid, rtype, action: actions.append(action),
        )
        monkeypatch.setattr(
            coordinator.audit_service, "log_action", lambda **kwargs: None
        )

        stopped = coordinator.process_due_request_stops()

        assert stopped == 1
        assert actions == ["shutdown"]
        assert req.vmid == 480
        assert req.provisioning_status != VMProvisioningStatus.failed
