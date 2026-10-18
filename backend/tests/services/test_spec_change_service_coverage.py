"""app.services.vm.spec_change_service 的分支補測（記憶體 SQLite + 假 Proxmox）。"""

from __future__ import annotations

import random
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    ProxmoxError,
)
from app.models import (
    Resource,
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
    User,
    UserRole,
)
from app.repositories import user as user_repo
from app.schemas import (
    SpecChangeRequestCreate,
    SpecChangeRequestReview,
    UserCreate,
)
from app.services.vm import spec_change_service as svc

REASON = "Need more CPU for workload spikes"


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(svc, "_open_session", lambda: Session(engine))
    with Session(engine) as session:
        yield session


def _user(session: Session, *, admin: bool = False) -> User:
    user = user_repo.create_user(
        session=session,
        user_create=UserCreate(
            email=f"{'admin' if admin else 'user'}-{uuid.uuid4().hex[:8]}@example.com",
            password="strongpass123",
            role=UserRole.admin if admin else UserRole.student,
        ),
    )
    session.commit()
    session.refresh(user)
    return user


def _vmid() -> int:
    return random.randint(700_000, 999_999)


def _resource(session: Session, *, user: User, vmid: int, **extra: Any) -> Resource:
    resource = Resource(
        vmid=vmid,
        user_id=user.id,
        environment_type="Owned VM",
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(resource)
    session.commit()
    return resource


def _request(
    session: Session,
    *,
    user: User,
    vmid: int,
    status: SpecChangeRequestStatus = SpecChangeRequestStatus.pending,
    **overrides: Any,
) -> SpecChangeRequest:
    fields: dict[str, Any] = {
        "vmid": vmid,
        "resource_vmid": vmid if session.get(Resource, vmid) is not None else None,
        "user_id": user.id,
        "change_type": SpecChangeType.cpu,
        "reason": REASON,
        "current_cpu": 2,
        "requested_cpu": 4,
        "status": status,
        "created_at": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    request = SpecChangeRequest(**fields)
    session.add(request)
    session.commit()
    session.refresh(request)
    return request


def _reload(session: Session, request_id: uuid.UUID) -> SpecChangeRequest:
    session.expire_all()
    row = session.exec(
        select(SpecChangeRequest).where(SpecChangeRequest.id == request_id)
    ).first()
    assert row is not None
    return row


def _fake_pve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str = "stopped",
    specs: dict[str, Any] | None = None,
    resize_error: Exception | None = None,
    update_error: Exception | None = None,
    control_error: Exception | None = None,
    listing: Any = None,
) -> SimpleNamespace:
    calls: list[str] = []

    def update_config(*_a: Any, **_k: Any) -> None:
        calls.append("update_config")
        if update_error is not None:
            raise update_error

    def resize_disk(*_a: Any, **_k: Any) -> None:
        calls.append("resize_disk")
        if resize_error is not None:
            raise resize_error

    def control(_node: str, _vmid: int, _rtype: str, action: str) -> None:
        calls.append(action)
        if control_error is not None and action == "start":
            raise control_error

    def list_all_resources() -> list[dict[str, Any]]:
        if isinstance(listing, Exception):
            raise listing
        return listing or []

    fake = SimpleNamespace(
        calls=calls,
        find_resource=lambda vmid: {
            "node": "node-a",
            "type": "qemu",
            "vmid": vmid,
            "name": "vm-a",
        },
        get_current_specs=lambda *_a, **_k: (
            specs or {"cpu": 2, "memory": 2048, "disk": 20}
        ),
        get_status=lambda *_a, **_k: {"status": status},
        control=control,
        update_config=update_config,
        resize_disk=resize_disk,
        list_all_resources=list_all_resources,
    )
    monkeypatch.setattr(svc, "proxmox_service", fake)
    return fake


# ---------------------------------------------------------------------------
# 純函式
# ---------------------------------------------------------------------------


def _status_request(**kw: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "status": SpecChangeRequestStatus.approved,
        "applied_at": None,
        "apply_error": None,
        "apply_started_at": None,
    }
    values.update(kw)
    return SimpleNamespace(**values)


def test_describe_status_all_states(monkeypatch: pytest.MonkeyPatch) -> None:
    pending = svc._describe_status(
        _status_request(status=SpecChangeRequestStatus.pending)
    )
    rejected = svc._describe_status(
        _status_request(status=SpecChangeRequestStatus.rejected)
    )
    cancelled = svc._describe_status(
        _status_request(status=SpecChangeRequestStatus.cancelled)
    )
    applied = svc._describe_status(
        _status_request(applied_at=datetime.now(timezone.utc))
    )
    monkeypatch.setattr(svc.background_tasks, "is_active", lambda _tid: True)
    applying = svc._describe_status(
        _status_request(apply_started_at=datetime.now(timezone.utc))
    )
    awaiting = svc._describe_status(_status_request())
    assert len({pending, rejected, cancelled, applied, applying, awaiting}) == 6


def test_describe_changes_covers_every_field() -> None:
    request = SimpleNamespace(
        requested_cpu=None,
        current_cpu=1,
        requested_memory=4096,
        current_memory=2048,
        requested_disk=40,
        current_disk=20,
        requested_expiry_date=date(2027, 1, 1),
        current_expiry_date=None,
    )
    assert svc._describe_changes(request) == [
        "Memory: 2048 -> 4096MB",
        "Disk: 20 -> 40GB",
        "Expiry: unlimited -> 2027-01-01",
    ]
    request.current_expiry_date = date(2026, 12, 1)
    assert svc._describe_changes(request)[-1] == "Expiry: 2026-12-01 -> 2027-01-01"


def test_resource_names_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_pve(monkeypatch, listing=RuntimeError("pve down"))
    assert svc._resource_names() == {}

    _fake_pve(
        monkeypatch,
        listing=[
            {"vmid": None, "name": "x"},
            {"vmid": "101", "name": None},
            {"vmid": 102, "name": "web"},
        ],
    )
    assert svc._resource_names() == {101: "", 102: "web"}


# ---------------------------------------------------------------------------
# 查詢
# ---------------------------------------------------------------------------


def test_list_by_user_and_list_all_include_names(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    vmid = _vmid()
    _request(db, user=user, vmid=vmid)
    _fake_pve(monkeypatch, listing=[{"vmid": vmid, "name": "my-vm"}])

    mine = svc.list_by_user(session=db, user_id=user.id)
    assert mine.count == 1
    assert mine.data[0].resource_name == "my-vm"

    everyone = svc.list_all(session=db, vmid=vmid)
    assert everyone.count == 1
    assert everyone.data[0].user_email == user.email

    empty = svc.list_all(session=db, vmid=-1)
    assert empty.data == []


# ---------------------------------------------------------------------------
# 建立
# ---------------------------------------------------------------------------


def test_non_admin_ownership_is_checked(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    checked: list[int] = []
    monkeypatch.setattr(
        svc,
        "require_resource_management",
        lambda *, session, user, vmid: checked.append(vmid),
    )
    _fake_pve(monkeypatch)
    info = svc._check_ownership_and_get_info(session=db, user=user, vmid=123)
    assert checked == [123]
    assert info["node"] == "node-a"


def test_fixed_spec_resources_are_rejected(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    class_vmid = _vmid()
    _resource(db, user=user, vmid=class_vmid, allocation_scope="teaching_class")
    with pytest.raises(BadRequestError):
        svc._reject_fixed_spec_resource(session=db, vmid=class_vmid)

    practice_vmid = _vmid()
    _resource(db, user=user, vmid=practice_vmid)
    monkeypatch.setattr(svc.resource_service, "_is_practice_resource", lambda *_a: True)
    with pytest.raises(BadRequestError):
        svc._reject_fixed_spec_resource(session=db, vmid=practice_vmid)

    monkeypatch.setattr(
        svc.resource_service, "_is_practice_resource", lambda *_a: False
    )
    svc._reject_fixed_spec_resource(session=db, vmid=practice_vmid)


@pytest.mark.parametrize(
    "fields",
    [
        {"change_type": SpecChangeType.cpu},
        {"change_type": SpecChangeType.memory},
        {"change_type": SpecChangeType.disk},
        {"change_type": SpecChangeType.combined},
        {"change_type": SpecChangeType.combined, "requested_disk": 20},
    ],
)
def test_create_validates_requested_values(
    db: Session, monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any]
) -> None:
    admin = _user(db, admin=True)
    _fake_pve(monkeypatch)
    payload = SpecChangeRequestCreate(vmid=_vmid(), reason=REASON, **fields)
    with pytest.raises(BadRequestError):
        svc.create(session=db, request_in=payload, user=admin)


def test_create_expiry_request_skips_quota(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _user(db, admin=True)
    _fake_pve(monkeypatch)
    monkeypatch.setattr(svc, "_validate_expiry_request", lambda **_k: date(2026, 12, 1))
    monkeypatch.setattr(
        svc.quota_service,
        "check_quota",
        lambda *_a, **_k: pytest.fail("expiry must not check quota"),
    )
    payload = SpecChangeRequestCreate(
        vmid=_vmid(),
        change_type=SpecChangeType.expiry,
        reason=REASON,
        requested_expiry_date=date(2027, 1, 1),
    )
    result = svc.create(session=db, request_in=payload, user=admin)
    assert result.current_expiry_date == date(2026, 12, 1)
    assert result.requested_expiry_date == date(2027, 1, 1)


# ---------------------------------------------------------------------------
# 審核
# ---------------------------------------------------------------------------


def _approve() -> SpecChangeRequestReview:
    return SpecChangeRequestReview(status=SpecChangeRequestStatus.approved)


def test_review_not_found(db: Session) -> None:
    reviewer = _user(db, admin=True)
    with pytest.raises(NotFoundError):
        svc.review(
            session=db,
            request_id=uuid.uuid4(),
            review_data=_approve(),
            reviewer=reviewer,
        )


def test_review_already_reviewed(db: Session) -> None:
    user = _user(db)
    reviewer = _user(db, admin=True)
    request = _request(
        db, user=user, vmid=_vmid(), status=SpecChangeRequestStatus.rejected
    )
    with pytest.raises(BadRequestError):
        svc.review(
            session=db, request_id=request.id, review_data=_approve(), reviewer=reviewer
        )


def test_review_refuses_non_decision(db: Session) -> None:
    user = _user(db)
    reviewer = _user(db, admin=True)
    request = _request(db, user=user, vmid=_vmid())
    with pytest.raises(BadRequestError):
        svc.review(
            session=db,
            request_id=request.id,
            review_data=SimpleNamespace(
                status=SpecChangeRequestStatus.pending, review_comment=None
            ),
            reviewer=reviewer,
        )


def test_review_rejects(db: Session) -> None:
    user = _user(db)
    reviewer = _user(db, admin=True)
    request = _request(db, user=user, vmid=_vmid())
    result = svc.review(
        session=db,
        request_id=request.id,
        review_data=SpecChangeRequestReview(
            status=SpecChangeRequestStatus.rejected, review_comment="no budget"
        ),
        reviewer=reviewer,
    )
    assert result.status == SpecChangeRequestStatus.rejected
    assert result.review_comment == "no budget"


def test_review_approves_expiry_immediately(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    reviewer = _user(db, admin=True)
    vmid = _vmid()
    _resource(db, user=user, vmid=vmid, expiry_date=date(2026, 11, 1))
    request = _request(
        db,
        user=user,
        vmid=vmid,
        change_type=SpecChangeType.expiry,
        requested_cpu=None,
        current_expiry_date=date(2026, 11, 1),
        requested_expiry_date=date(2027, 1, 1),
    )
    monkeypatch.setattr(svc, "_window_request", lambda _s, _v: None)
    _fake_pve(monkeypatch)

    result = svc.review(
        session=db, request_id=request.id, review_data=_approve(), reviewer=reviewer
    )

    assert result.applied_at is not None
    assert result.apply_status == "applied"
    db.expire_all()
    assert db.get(Resource, vmid).expiry_date == date(2027, 1, 1)


# ---------------------------------------------------------------------------
# 撤銷
# ---------------------------------------------------------------------------


def test_cancel_not_found(db: Session) -> None:
    user = _user(db)
    with pytest.raises(NotFoundError):
        svc.cancel(session=db, request_id=uuid.uuid4(), user=user)


def test_cancel_refused_while_applying(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    request = _request(
        db, user=user, vmid=_vmid(), status=SpecChangeRequestStatus.approved
    )
    monkeypatch.setattr(svc.background_tasks, "is_active", lambda _tid: True)
    with pytest.raises(ConflictError):
        svc.cancel(session=db, request_id=request.id, user=user)


# ---------------------------------------------------------------------------
# 套用
# ---------------------------------------------------------------------------


def test_apply_not_found(db: Session) -> None:
    user = _user(db)
    with pytest.raises(NotFoundError):
        svc.apply(session=db, request_id=uuid.uuid4(), user=user)


def test_apply_requires_approved(db: Session) -> None:
    user = _user(db)
    request = _request(db, user=user, vmid=_vmid())
    with pytest.raises(BadRequestError):
        svc.apply(session=db, request_id=request.id, user=user)


def test_apply_refuses_already_applied(db: Session) -> None:
    user = _user(db)
    request = _request(
        db,
        user=user,
        vmid=_vmid(),
        status=SpecChangeRequestStatus.approved,
        applied_at=datetime.now(timezone.utc),
    )
    with pytest.raises(BadRequestError):
        svc.apply(session=db, request_id=request.id, user=user)


def test_apply_refuses_when_resource_gone(db: Session) -> None:
    user = _user(db)
    request = _request(
        db, user=user, vmid=_vmid(), status=SpecChangeRequestStatus.approved
    )
    assert request.resource_vmid is None
    with pytest.raises(BadRequestError):
        svc.apply(session=db, request_id=request.id, user=user)


def test_apply_refuses_within_lock_window(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    vmid = _vmid()
    _resource(db, user=user, vmid=vmid)
    request = _request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        apply_started_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(svc.background_tasks, "is_active", lambda _tid: False)
    with pytest.raises(ConflictError):
        svc.apply(session=db, request_id=request.id, user=user)


def test_apply_runner_unavailable_marks_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    vmid = _vmid()
    _resource(db, user=user, vmid=vmid)
    request = _request(
        db, user=user, vmid=vmid, status=SpecChangeRequestStatus.approved
    )
    _fake_pve(monkeypatch)
    monkeypatch.setattr(svc.background_tasks, "is_active", lambda _tid: False)
    monkeypatch.setattr(svc.background_tasks, "submit_sync", lambda *_a, **_k: None)

    with pytest.raises(AppError) as exc_info:
        svc.apply(session=db, request_id=request.id, user=user)

    assert exc_info.value.status_code == 503
    assert _reload(db, request.id).apply_error


# ---------------------------------------------------------------------------
# _refresh_current_specs
# ---------------------------------------------------------------------------


def test_refresh_current_specs_not_found_and_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(id=uuid.uuid4(), vmid=1)

    def missing(_vmid: int) -> dict[str, Any]:
        raise NotFoundError("gone")

    monkeypatch.setattr(svc, "proxmox_service", SimpleNamespace(find_resource=missing))
    with pytest.raises(BadRequestError):
        svc._refresh_current_specs(None, request, strict=False)

    def down(_vmid: int) -> dict[str, Any]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc, "proxmox_service", SimpleNamespace(find_resource=down))
    assert svc._refresh_current_specs(None, request, strict=False) is request
    with pytest.raises(RuntimeError):
        svc._refresh_current_specs(None, request, strict=True)


# ---------------------------------------------------------------------------
# 背景套用
# ---------------------------------------------------------------------------


def test_wait_until_stopped_tolerates_status_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "_POLL_INTERVAL_SECONDS", 0)
    replies: list[Any] = [RuntimeError("busy"), {"status": "stopped"}]

    def get_status(*_a: Any) -> dict[str, Any]:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(svc, "proxmox_service", SimpleNamespace(get_status=get_status))
    assert svc._wait_until_stopped("n", 1, "qemu", 60) is True


def test_run_apply_partial_failure_restart_fails(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    vmid = _vmid()
    _resource(db, user=user, vmid=vmid)
    request = _request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        change_type=SpecChangeType.combined,
        current_disk=20,
        requested_disk=40,
        apply_started_at=datetime.now(timezone.utc),
    )
    fake = _fake_pve(
        monkeypatch,
        status="running",
        resize_error=ProxmoxError("resize failed"),
        control_error=RuntimeError("start failed"),
    )
    monkeypatch.setattr(svc, "_ensure_stopped", lambda *_a: None)

    with pytest.raises(svc._PartialApplyError):
        svc._run_apply(
            request.id,
            svc._snapshot(request),
            {"node": "node-a", "type": "qemu", "vmid": vmid},
            user.id,
        )

    assert fake.calls == ["update_config", "resize_disk", "start"]
    refreshed = _reload(db, request.id)
    assert refreshed.applied_at is None
    assert "start failed" in refreshed.apply_error
    assert refreshed.current_cpu == 4
    assert refreshed.current_disk == 20


def test_run_apply_success_with_start_warning(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    vmid = _vmid()
    _resource(db, user=user, vmid=vmid)
    request = _request(
        db,
        user=user,
        vmid=vmid,
        status=SpecChangeRequestStatus.approved,
        apply_started_at=datetime.now(timezone.utc),
    )
    fake = _fake_pve(
        monkeypatch, status="running", control_error=RuntimeError("start failed")
    )
    monkeypatch.setattr(svc, "_ensure_stopped", lambda *_a: None)

    svc._run_apply(
        request.id,
        svc._snapshot(request),
        {"node": "node-a", "type": "qemu", "vmid": vmid},
        user.id,
    )

    assert fake.calls == ["update_config", "start"]
    refreshed = _reload(db, request.id)
    assert refreshed.applied_at is not None


def test_finish_apply_logs_when_db_write_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken() -> Session:
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_open_session", broken)
    svc._finish_apply(uuid.uuid4(), uuid.uuid4(), 1, changes=["x"])
    assert "Failed to record apply result" in caplog.text


def test_open_session_uses_application_engine() -> None:
    from app.core.db import engine

    session = svc._open_session()
    try:
        assert session.get_bind() is engine
    finally:
        session.close()
