"""重試申請：沒 vmid 的 clone 失敗照舊重送；已建好機器的開機失敗要重設狀態讓排程接手。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resource_markers import RESOURCE_DELETED_MARKERS
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import VMProvisioningStatus, VMRequestStatus
from app.services.scheduling import coordinator
from app.services.vm import vm_request_service


class _FakeSession:
    def __init__(self, *_args: Any) -> None:
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        """測試替身。"""

    def add(self, _obj: Any) -> None:
        """測試替身。"""

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, _obj: Any) -> None:
        """測試替身。"""

    def rollback(self) -> None:
        self.rollbacks += 1

    def expire_all(self) -> None:
        """測試替身。"""


def _request(**overrides: Any) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "status": VMRequestStatus.approved,
        "vmid": None,
        "provisioning_status": VMProvisioningStatus.failed,
        "provisioning_error": "boom",
        "resource_warning": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def retry_env(monkeypatch: pytest.MonkeyPatch):
    submitted: list[Any] = []
    monkeypatch.setattr(vm_request_service, "require_vm_request_cancel", lambda *_a: None)
    monkeypatch.setattr(vm_request_service, "_submit_provision", lambda _s, req: submitted.append(req))
    monkeypatch.setattr(vm_request_service, "_to_public", lambda req: req)

    def run(request: SimpleNamespace) -> tuple[Any, _FakeSession]:
        monkeypatch.setattr(
            vm_request_service.vm_request_repo, "get_vm_request_by_id", lambda **_kw: request
        )
        session = _FakeSession()
        result = vm_request_service.retry(
            session=session, request_id=request.id, current_user=SimpleNamespace(id=uuid.uuid4())
        )
        return result, session

    return run, submitted


def test_retry_without_vmid_resubmits_clone(retry_env) -> None:
    run, submitted = retry_env
    request = _request()

    run(request)

    assert submitted == [request]
    assert request.provisioning_status == VMProvisioningStatus.failed


def test_retry_existing_vm_resets_status_for_scheduler(retry_env) -> None:
    run, submitted = retry_env
    request = _request(vmid=482, provisioning_error="start failed")

    _, session = run(request)

    assert request.provisioning_status == VMProvisioningStatus.pending
    assert request.provisioning_error is None
    assert session.commits == 1
    assert submitted == [request]


def test_retry_rejects_request_consumed_by_resource_deletion(retry_env) -> None:
    run, submitted = retry_env
    request = _request(vmid=482, resource_warning=next(iter(RESOURCE_DELETED_MARKERS)))

    with pytest.raises(BadRequestError):
        run(request)

    assert submitted == []
    assert request.provisioning_status == VMProvisioningStatus.failed


def test_retry_rejects_request_that_did_not_fail(retry_env) -> None:
    run, submitted = retry_env

    with pytest.raises(BadRequestError):
        run(_request(vmid=482, provisioning_status=VMProvisioningStatus.completed))

    assert submitted == []


@pytest.mark.parametrize(
    ("vmid", "error", "should_mark"),
    [
        (482, ProxmoxError("start failed"), True),
        (482, NotFoundError("gone"), False),
        (None, ProxmoxError("clone failed"), False),
    ],
)
def test_immediate_start_failure_marks_only_existing_vm_restarts(
    monkeypatch: pytest.MonkeyPatch, vmid: int | None, error: Exception, should_mark: bool
) -> None:
    request = _request(vmid=vmid, provisioning_status=VMProvisioningStatus.pending)
    marked: list[str] = []
    monkeypatch.setattr(coordinator, "Session", _FakeSession)
    monkeypatch.setattr(coordinator.vm_request_repo, "get_vm_request_by_id", lambda **_kw: request)

    def _raise(**_kw: Any) -> bool:
        raise error

    monkeypatch.setattr(coordinator, "_ensure_request_running", _raise)
    monkeypatch.setattr(
        coordinator, "_mark_request_runtime_error", lambda **kw: marked.append(kw["message"])
    )

    assert coordinator.process_single_request_start(request.id) is False
    assert marked == ([str(error)] if should_mark else [])
