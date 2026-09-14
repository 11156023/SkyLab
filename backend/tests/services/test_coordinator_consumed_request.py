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
            session=session, request=consumed, now=coordinator._utc_now()
        )

        assert started is False
        assert calls == []
        assert consumed.provisioning_status == VMProvisioningStatus.failed
        assert consumed.provisioning_error == DELETED_MARKER

    def test_active_request_still_gets_started(self, monkeypatch) -> None:
        req = _request(provisioning_status=VMProvisioningStatus.completed)
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
            session=session, request=req, now=coordinator._utc_now()
        )

        assert started is True
        assert calls == ["start", "update"]


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

        monkeypatch.setattr(coordinator.proxmox_service, "find_resource", _missing)

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
            coordinator.proxmox_service,
            "find_resource",
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
