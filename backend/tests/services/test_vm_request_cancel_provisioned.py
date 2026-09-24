"""Regression tests for cancelling VM requests after provisioning.

``cancel()`` used to let an ``approved`` request with a ``vmid`` (machine
already provisioned) be cancelled: the request was marked ``cancelled`` and
its node fields cleared while the live VM and its Resource record stayed
around. The scheduler only manages machines through the active approved
request list, so the orphaned VM lost its start window, auto-shutdown and
provisioning handling.

New behaviour under test:
- ``pending`` requests are cancellable (standard flow);
- ``approved`` requests without a ``vmid`` are cancellable (provisioning is
  aborted via the background worker);
- ``approved`` requests with a ``vmid`` are rejected with 400, pointing the
  user at the resource deletion flow (which cancels the request itself with
  ``review_comment="Resource deleted by user"``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError
from app.models import VMRequestStatus
from app.services.vm import vm_request_service


class FakeSession:
    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        pass


class FakeRepo:
    """Stand-in for vm_request_repo backed by a single request object."""

    def __init__(self, request: SimpleNamespace) -> None:
        self.request = request
        self.status_updates: list[dict[str, Any]] = []

    def get_vm_request_by_id(self, **kwargs: Any) -> SimpleNamespace:
        return self.request

    def update_vm_request_status(self, **kwargs: Any) -> SimpleNamespace:
        self.status_updates.append(kwargs)
        self.request.status = kwargs["status"]
        return self.request


def _make_request(
    *, status: VMRequestStatus, vmid: int | None, user_id: uuid.UUID
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        status=status,
        vmid=vmid,
    )


@pytest.fixture()
def fake_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every collaborator of vm_request_service.cancel."""
    user_id = uuid.uuid4()
    current_user = SimpleNamespace(id=user_id, is_superuser=False)

    monkeypatch.setattr(
        vm_request_service,
        "require_vm_request_cancel",
        lambda current_user, owner_id: None,
    )
    monkeypatch.setattr(
        vm_request_service,
        "audit_service",
        SimpleNamespace(log_action=lambda **kw: None),
    )
    monkeypatch.setattr(
        vm_request_service, "_to_public", lambda req, user_override=None: req
    )

    return {
        "session": FakeSession(),
        "current_user": current_user,
        "user_id": user_id,
    }


def _cancel(fake_env: dict[str, Any], request: SimpleNamespace) -> Any:
    return vm_request_service.cancel(
        session=fake_env["session"],
        request_id=request.id,
        current_user=fake_env["current_user"],
    )


def test_pending_request_is_cancellable(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    request = _make_request(
        status=VMRequestStatus.pending, vmid=None, user_id=fake_env["user_id"]
    )
    repo = FakeRepo(request)
    monkeypatch.setattr(vm_request_service, "vm_request_repo", repo)

    _cancel(fake_env, request)

    assert request.status == VMRequestStatus.cancelled
    assert fake_env["session"].committed


def test_approved_request_without_vmid_is_cancellable(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    request = _make_request(
        status=VMRequestStatus.approved, vmid=None, user_id=fake_env["user_id"]
    )
    repo = FakeRepo(request)
    monkeypatch.setattr(vm_request_service, "vm_request_repo", repo)

    _cancel(fake_env, request)

    assert request.status == VMRequestStatus.cancelled
    assert fake_env["session"].committed


def test_approved_request_with_clone_in_flight_is_rejected(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    """worker 正在 clone（DB running 且未 stale）：取消會留孤兒，必須拒絕。"""
    from datetime import UTC, datetime

    from app.models import VMProvisioningStatus

    request = _make_request(
        status=VMRequestStatus.approved, vmid=None, user_id=fake_env["user_id"]
    )
    request.provisioning_status = VMProvisioningStatus.running
    request.provisioning_started_at = datetime.now(UTC)
    repo = FakeRepo(request)
    monkeypatch.setattr(vm_request_service, "vm_request_repo", repo)

    with pytest.raises(BadRequestError) as exc:
        _cancel(fake_env, request)

    assert exc.value.status_code == 400
    assert request.status == VMRequestStatus.approved
    assert repo.status_updates == []


def test_approved_request_with_stale_clone_is_cancellable(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    """running 已超過 stale 門檻：視為 worker 已死，允許取消。"""
    from datetime import UTC, datetime, timedelta

    from app.models import VMProvisioningStatus

    request = _make_request(
        status=VMRequestStatus.approved, vmid=None, user_id=fake_env["user_id"]
    )
    request.provisioning_status = VMProvisioningStatus.running
    request.provisioning_started_at = datetime.now(UTC) - timedelta(hours=2)
    repo = FakeRepo(request)
    monkeypatch.setattr(vm_request_service, "vm_request_repo", repo)

    _cancel(fake_env, request)

    assert request.status == VMRequestStatus.cancelled


def test_approved_request_with_vmid_is_rejected(
    monkeypatch: pytest.MonkeyPatch, fake_env: dict[str, Any]
) -> None:
    request = _make_request(
        status=VMRequestStatus.approved, vmid=150, user_id=fake_env["user_id"]
    )
    repo = FakeRepo(request)
    monkeypatch.setattr(vm_request_service, "vm_request_repo", repo)

    with pytest.raises(BadRequestError, match="請改為刪除該資源") as exc:
        _cancel(fake_env, request)

    assert exc.value.status_code == 400
    # The request must stay approved so the scheduler keeps managing the VM.
    assert request.status == VMRequestStatus.approved
    assert repo.status_updates == []
    assert not fake_env["session"].committed

