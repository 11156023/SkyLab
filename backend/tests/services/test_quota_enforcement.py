"""配額執法點測試：超限時 create/review 應在寫入前被 409 擋下。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import ConflictError
from app.services.vm import spec_change_service, vm_request_service


def test_vm_request_create_blocked_by_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    def _deny(session, user_id, **kwargs):
        raise ConflictError("配額不足")

    monkeypatch.setattr(
        vm_request_service.quota_service, "check_quota", _deny
    )
    request_in = SimpleNamespace(
        resource_type="lxc",
        requested_mode="manual",
        ostemplate="local:vztmpl/x.tar.zst",
        cores=2,
        memory=2048,
        rootfs_size=8,
        disk_size=None,
        mode="scheduled",
    )
    user = SimpleNamespace(id=uuid.uuid4(), email="stu@campus.edu")
    with pytest.raises(ConflictError):
        vm_request_service.create(session=None, request_in=request_in, user=user)


def test_spec_change_review_blocked_by_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    db_request = SimpleNamespace(
        id=uuid.uuid4(),
        vmid=101,
        user_id=uuid.uuid4(),
        status=spec_change_service.SpecChangeRequestStatus.pending,
        requested_cpu=8,
        current_cpu=2,
        requested_memory=None,
        current_memory=2048,
        requested_disk=None,
        current_disk=20,
    )
    monkeypatch.setattr(
        spec_change_service.spec_request_repo,
        "get_spec_change_request_by_id",
        lambda **kwargs: db_request,
    )

    def _deny(session, user_id, **kwargs):
        raise ConflictError("配額不足")

    monkeypatch.setattr(spec_change_service.quota_service, "check_quota", _deny)
    review_data = SimpleNamespace(
        status=spec_change_service.SpecChangeRequestStatus.approved,
        review_comment=None,
    )
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="admin@campus.edu")

    class _S:
        def rollback(self) -> None: ...

    with pytest.raises(ConflictError):
        spec_change_service.review(
            session=_S(), request_id=db_request.id, review_data=review_data,
            reviewer=reviewer,
        )
