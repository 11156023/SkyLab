"""排程器自動開機的 GPU 失敗攔截測試。

qmstart 由 fire-and-forget 改為阻塞等待任務結果後：
- vGPU/vfio 類啟動失敗 → 寫 resource_warning（等待 GPU 釋出）、不標 failed、
  申請單留在 active 清單繼續重試
- 其他啟動失敗 → 照舊往外拋，由 runtime error 流程標 failed
- 開機成功 → 清除先前的 GPU 等待警示
"""

from __future__ import annotations

import uuid

import pytest

from app.exceptions import ProxmoxError
from app.models import VMRequest, VMRequestStatus
from app.services.scheduling import coordinator

GPU_TASK_ERROR = (
    "Task UPID:pve205:0018C3AD:qmstart:480:root@pam: failed with exitstatus: "
    "start failed: QEMU exited with code 1. Task log tail: kvm: -device "
    "vfio-pci,host=0000:15:00.6,id=hostpci0: vfio 0000:15:00.6: "
    "error getting device from group 146: Input/output error"
)

PLAIN_TASK_ERROR = (
    "Task UPID:pve205:0018C3AD:qmstart:480:root@pam: failed with exitstatus: "
    "start failed: org.freedesktop.systemd1 timed out"
)


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


def _request(**overrides) -> VMRequest:
    defaults: dict = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "hostname": "vm-test",
        "resource_type": "vm",
        "status": VMRequestStatus.approved,
        "vmid": 480,
        "cores": 2,
        "memory": 2048,
    }
    defaults.update(overrides)
    return VMRequest(**defaults)


def _patch_provisioned_vm(monkeypatch, req: VMRequest, *, status: str) -> None:
    """把 _ensure_request_running 的已開通分支外部依賴全部釘住。"""
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
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        coordinator.audit_service, "log_action", lambda **kwargs: None
    )
    monkeypatch.setattr(
        coordinator.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {"status": status},
    )


class TestIsGpuStartFailure:
    def test_vfio_group_error_detected(self) -> None:
        assert coordinator._is_gpu_start_failure(GPU_TASK_ERROR)

    def test_nvidia_vgpu_marker_detected(self) -> None:
        assert coordinator._is_gpu_start_failure(
            "TASK ERROR: [nvidia-vgpu-vfio] 0000:15:00.6: start failed. status: 0x1"
        )

    def test_plain_error_not_detected(self) -> None:
        assert not coordinator._is_gpu_start_failure(PLAIN_TASK_ERROR)


class TestEnsureRequestRunningStart:
    def test_gpu_failure_writes_warning_without_failing_request(
        self, monkeypatch
    ) -> None:
        req = _request()
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="stopped")

        def _fail_start(node, vmid, rtype, action, **kwargs):
            raise ProxmoxError(GPU_TASK_ERROR)

        monkeypatch.setattr(coordinator.proxmox_service, "control", _fail_start)

        started = coordinator._ensure_request_running(
            session=session, request=req, now=coordinator._utc_now()
        )

        assert started is False
        assert req.resource_warning == coordinator.GPU_WAIT_WARNING
        # 沒有丟例外 → 不會走 runtime error 流程標 failed，下一 tick 續retry
        assert session.commits >= 1

    def test_non_gpu_failure_propagates(self, monkeypatch) -> None:
        req = _request()
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="stopped")

        def _fail_start(node, vmid, rtype, action, **kwargs):
            raise ProxmoxError(PLAIN_TASK_ERROR)

        monkeypatch.setattr(coordinator.proxmox_service, "control", _fail_start)

        with pytest.raises(ProxmoxError):
            coordinator._ensure_request_running(
                session=session, request=req, now=coordinator._utc_now()
            )
        assert req.resource_warning is None

    def test_successful_start_clears_gpu_warning(self, monkeypatch) -> None:
        req = _request(resource_warning=coordinator.GPU_WAIT_WARNING)
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="stopped")
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda node, vmid, rtype, action, **kwargs: None,
        )

        started = coordinator._ensure_request_running(
            session=session, request=req, now=coordinator._utc_now()
        )

        assert started is True
        assert req.resource_warning is None

    def test_lxc_start_syncs_platform_key(self, monkeypatch) -> None:
        req = _request(resource_type="lxc")
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="stopped")
        sync_calls: list[tuple[str, int, str]] = []
        monkeypatch.setattr(
            coordinator,
            "_sync_lxc_platform_key",
            lambda *, session, node, vmid, resource_type: sync_calls.append(
                (node, vmid, resource_type)
            ),
        )
        monkeypatch.setattr(
            coordinator.proxmox_service,
            "control",
            lambda node, vmid, rtype, action, **kwargs: None,
        )

        assert coordinator._ensure_request_running(
            session=session, request=req, now=coordinator._utc_now()
        )
        assert sync_calls == [("pve205", 480, "lxc")]

    def test_already_running_clears_gpu_warning(self, monkeypatch) -> None:
        req = _request(resource_warning=coordinator.GPU_WAIT_WARNING)
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="running")

        started = coordinator._ensure_request_running(
            session=session, request=req, now=coordinator._utc_now()
        )

        assert started is False
        assert req.resource_warning is None

    def test_start_task_timeout_treated_as_started(self, monkeypatch) -> None:
        req = _request()
        session = _FakeSession()
        _patch_provisioned_vm(monkeypatch, req, status="stopped")

        def _slow_start(node, vmid, rtype, action, **kwargs):
            raise TimeoutError("PVE task did not finish within 60s")

        monkeypatch.setattr(coordinator.proxmox_service, "control", _slow_start)

        started = coordinator._ensure_request_running(
            session=session, request=req, now=coordinator._utc_now()
        )

        assert started is True
