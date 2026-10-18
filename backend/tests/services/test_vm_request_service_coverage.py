"""app.services.vm.vm_request_service 的分支補測（記憶體 SQLite + 假放置／PVE）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    UserRole,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from app.schemas import VMRequestCreate, VMRequestReview
from app.services.vm import vm_request_service as svc

ADMIN = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin, email="admin@example.com")
TEACHER = SimpleNamespace(
    id=uuid.uuid4(), role=UserRole.teacher, email="teacher@example.com"
)
STUDENT = SimpleNamespace(
    id=uuid.uuid4(), role=UserRole.student, email="student@example.com"
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def no_audit(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **kw: calls.append(kw))
    return calls


def _now() -> datetime:
    return datetime.now(UTC)


def _vm_request(db: Session | None = None, **overrides: Any) -> VMRequest:
    fields: dict[str, Any] = {
        "user_id": STUDENT.id,
        "reason": "Need a machine for coursework",
        "resource_type": "vm",
        "hostname": f"host-{uuid.uuid4().hex[:6]}",
        "template_id": 9000,
        "status": VMRequestStatus.pending,
        "start_at": _now() + timedelta(hours=1),
        "end_at": _now() + timedelta(hours=3),
        "created_at": _now(),
    }
    fields.update(overrides)
    request = VMRequest(**fields)
    if db is not None:
        db.add(request)
        db.commit()
        db.refresh(request)
    return request


def _selection(node: str | None = "pve-a", **plan: Any) -> SimpleNamespace:
    plan_values: dict[str, Any] = {
        "feasible": True,
        "summary": "ok",
        "rationale": [],
        "warnings": [],
    }
    plan_values.update(plan)
    return SimpleNamespace(
        node=node, strategy="dominant_share", plan=SimpleNamespace(**plan_values)
    )


# ---------------------------------------------------------------------------
# 密碼
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _approve_and_place
# ---------------------------------------------------------------------------


def test_approve_without_window_and_quick_template_without_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _vm_request(db, start_at=None, end_at=None, request_kind="quick_template")
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node",
        lambda **_k: None,
    )
    with pytest.raises(BadRequestError):
        svc._approve_and_place(session=db, db_request=request, reviewer_id=ADMIN.id)


def test_approve_rebuild_updates_other_requests(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = _now() + timedelta(hours=1)
    end = start + timedelta(hours=2)
    provisioned = _vm_request(
        db,
        status=VMRequestStatus.approved,
        start_at=start,
        end_at=end,
        vmid=801,
        actual_node="pve-a",
    )
    pinned = _vm_request(
        db, status=VMRequestStatus.approved, start_at=start, end_at=end
    )
    running = _vm_request(
        db,
        status=VMRequestStatus.approved,
        start_at=start,
        end_at=end,
        provisioning_status=VMProvisioningStatus.running,
    )
    current = _vm_request(db, start_at=start, end_at=end)
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "provisioning_pinned_node",
        lambda request: "pve-z" if request.id == pinned.id else None,
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "rebuild_reserved_assignments",
        lambda **_k: {
            running.id: _selection("pve-b"),
            current.id: _selection("pve-c"),
        },
    )

    selection = svc._approve_and_place(
        session=db, db_request=current, reviewer_id=ADMIN.id
    )

    assert selection.node == "pve-c"
    assert provisioned.provisioning_status == VMProvisioningStatus.completed
    assert provisioned.assigned_node == "pve-a"
    assert pinned.assigned_node is None
    assert running.provisioning_status == VMProvisioningStatus.running
    assert running.assigned_node == "pve-b"
    assert current.status == VMRequestStatus.approved


def test_approve_rebuild_rejects_provisioned_request_without_node(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = _now() + timedelta(hours=1)
    end = start + timedelta(hours=2)
    _vm_request(
        db, status=VMRequestStatus.approved, start_at=start, end_at=end, vmid=802
    )
    current = _vm_request(db, start_at=start, end_at=end)
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "rebuild_reserved_assignments",
        lambda **_k: {},
    )
    with pytest.raises(BadRequestError):
        svc._approve_and_place(session=db, db_request=current, reviewer_id=ADMIN.id)


def test_approve_rebuild_requires_selection(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = _vm_request(db, end_at=None)
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "rebuild_reserved_assignments",
        lambda **_k: {},
    )
    with pytest.raises(BadRequestError):
        svc._approve_and_place(session=db, db_request=current, reviewer_id=ADMIN.id)


# ---------------------------------------------------------------------------
# 範本來源
# ---------------------------------------------------------------------------


def _template(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "resource_type": "qemu",
        "status": VMTemplateStatus.ready,
        "visibility": VMTemplateVisibility.global_,
        "default_disk": None,
        "requires_gpu": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _validate(
    monkeypatch: pytest.MonkeyPatch,
    template: Any,
    *,
    user: Any = STUDENT,
    resource_type: str = "vm",
    visible: bool = True,
) -> Any:
    monkeypatch.setattr(
        svc.vm_template_repo, "get_template_by_pve_vmid", lambda **_k: template
    )
    monkeypatch.setattr(
        svc.vm_template_repo, "is_template_visible_to_user", lambda **_k: visible
    )
    return svc._validate_template_source(
        session=None, template_vmid=100, user=user, resource_type=resource_type
    )


def test_validate_template_source_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(BadRequestError):
        _validate(monkeypatch, None, resource_type="lxc")
    assert _validate(monkeypatch, None) is None
    with pytest.raises(BadRequestError):
        _validate(monkeypatch, _template(resource_type="LXC"))
    with pytest.raises(BadRequestError):
        _validate(monkeypatch, _template(status=VMTemplateStatus.updating))
    admin_template = _template(visibility="private")
    assert _validate(monkeypatch, admin_template, user=ADMIN) is admin_template
    assert _validate(monkeypatch, admin_template, user=TEACHER) is admin_template
    with pytest.raises(BadRequestError):
        _validate(monkeypatch, admin_template, user=TEACHER, visible=False)
    with pytest.raises(BadRequestError):
        _validate(monkeypatch, admin_template)
    open_template = _template()
    assert _validate(monkeypatch, open_template) is open_template


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


@pytest.fixture()
def create_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.quota_service, "check_quota", lambda *_a, **_k: None)
    monkeypatch.setattr(
        svc.vm_template_repo, "get_template_by_pve_vmid", lambda **_k: None
    )


def _create_in(**overrides: Any) -> VMRequestCreate:
    values: dict[str, Any] = {
        "reason": "Need a machine for coursework",
        "resource_type": "vm",
        "hostname": "lab-1",
        "cores": 2,
        "memory": 2048,
        "template_id": 9000,
        "username": "student",
        "start_at": _now() + timedelta(hours=1),
        "end_at": _now() + timedelta(hours=3),
    }
    values.update(overrides)
    return VMRequestCreate.model_construct(**values)


def _create(request_in: VMRequestCreate, user: Any = STUDENT) -> Any:
    return svc.create(session=None, request_in=request_in, user=user)


def test_create_rejects_unknown_resource_type() -> None:
    with pytest.raises(BadRequestError):
        _create(_create_in(resource_type="docker"))


def test_create_auto_mode_disabled(
    monkeypatch: pytest.MonkeyPatch, create_env: None
) -> None:
    monkeypatch.setattr(
        svc.governance_repo,
        "get_governance_config",
        lambda **_k: SimpleNamespace(workload_advisor_enabled=False),
    )
    with pytest.raises(BadRequestError):
        _create(_create_in(requested_mode="auto"))


def test_create_auto_mode_records_advice_then_validates(
    monkeypatch: pytest.MonkeyPatch, create_env: None
) -> None:
    monkeypatch.setattr(
        svc.governance_repo,
        "get_governance_config",
        lambda **_k: SimpleNamespace(workload_advisor_enabled=True),
    )
    advised: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.workload_advisor,
        "advise",
        lambda **kw: (
            advised.append(kw)
            or SimpleNamespace(reasons=["light"], resource_type="lxc")
        ),
    )
    with pytest.raises(BadRequestError):
        _create(_create_in(requested_mode="auto", template_id=None))
    assert advised[0]["cores"] == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"resource_type": "lxc", "template_id": None, "ostemplate": None},
        {"template_id": None},
        {"gpu_mdev_profile": "nvidia-1", "gpu_mapping_id": None},
    ],
)
def test_create_rejects_missing_sources(
    create_env: None, overrides: dict[str, Any]
) -> None:
    with pytest.raises(BadRequestError):
        _create(_create_in(**overrides))


def test_create_vm_requires_username_unless_windows(
    monkeypatch: pytest.MonkeyPatch, create_env: None
) -> None:
    from app.services.proxmox import provisioning_service

    monkeypatch.setattr(provisioning_service, "is_windows_template", lambda _tid: False)
    with pytest.raises(BadRequestError):
        _create(_create_in(username=None))

    # Windows 範本不需要帳號：往下走到停用的 quick_template 模式才被擋
    monkeypatch.setattr(provisioning_service, "is_windows_template", lambda _tid: True)
    with pytest.raises(BadRequestError):
        _create(_create_in(username=None, mode="quick_template"))


def test_create_vgpu_profile_checks(
    monkeypatch: pytest.MonkeyPatch, create_env: None
) -> None:
    from app.services.proxmox import gpu_service

    def unavailable(_mapping: str) -> Any:
        raise RuntimeError("pve down")

    monkeypatch.setattr(gpu_service, "get_gpu_mapping", unavailable)
    # 查不到 GPU 時不擋，繼續到模式驗證（quick_template 已停用）
    with pytest.raises(BadRequestError):
        _create(
            _create_in(
                gpu_mapping_id="gpu-a", gpu_mdev_profile="nv-1", mode="quick_template"
            )
        )

    monkeypatch.setattr(
        gpu_service,
        "get_gpu_mapping",
        lambda _m: SimpleNamespace(profiles=[SimpleNamespace(mdev_type="nv-2")]),
    )
    with pytest.raises(BadRequestError) as exc_info:
        _create(_create_in(gpu_mapping_id="gpu-a", gpu_mdev_profile="nv-1"))
    assert "nv-1" in str(exc_info.value.message)


def test_create_immediate_window_checks(
    monkeypatch: pytest.MonkeyPatch, create_env: None
) -> None:
    with pytest.raises(BadRequestError):
        _create(
            _create_in(mode="immediate", end_at=_now() - timedelta(hours=1)),
            user=ADMIN,
        )

    bounds: list[dict[str, Any]] = []
    monkeypatch.setattr(
        svc.vm_request_availability_service,
        "ensure_window_bounds",
        lambda **kw: bounds.append(kw),
    )

    class Stop(Exception):
        pass

    def stop(**_k: Any) -> None:
        raise Stop

    monkeypatch.setattr(
        svc.vm_request_availability_service, "validate_request_window", stop
    )
    with pytest.raises(Stop):
        _create(_create_in(mode="immediate"), user=ADMIN)
    assert len(bounds) == 1


def test_create_scheduled_window_checks(create_env: None) -> None:
    with pytest.raises(BadRequestError):
        _create(_create_in(start_at=None))
    start = _now() + timedelta(hours=2)
    with pytest.raises(BadRequestError):
        _create(_create_in(start_at=start, end_at=start - timedelta(hours=1)))


# ---------------------------------------------------------------------------
# quick practice / 查詢
# ---------------------------------------------------------------------------


def test_create_quick_practice_request(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_audit: list[dict[str, Any]]
) -> None:
    placed: list[Any] = []
    monkeypatch.setattr(
        svc, "_approve_and_place", lambda **kw: placed.append(kw["db_request"])
    )
    request_in = VMRequestCreate(
        reason="Quick practice machine",
        resource_type="lxc",
        hostname="qp-1",
        ostemplate="local:vztmpl/debian.tar.zst",
        password="Secret123",
    )
    group = uuid.uuid4()
    db_request = svc.create_quick_practice_request(
        session=db, request_in=request_in, user=STUDENT, placement_group_id=group
    )
    assert placed == [db_request]
    assert db_request.request_kind == "quick_template"
    assert db_request.password not in (None, "Secret123")
    assert no_audit[0]["action"] == "quick_practice_machine_create"


def test_list_get_and_not_found(db: Session) -> None:
    request = _vm_request(db)
    other = _vm_request(db, user_id=uuid.uuid4())

    mine = svc.list_by_user(session=db, user_id=STUDENT.id)
    assert [item.id for item in mine.data] == [request.id]
    everyone = svc.list_all(session=db)
    assert everyone.count == 2

    assert svc.get(session=db, request_id=other.id, current_user=ADMIN).id == other.id
    with pytest.raises(NotFoundError):
        svc.get(session=db, request_id=uuid.uuid4(), current_user=ADMIN)


# ---------------------------------------------------------------------------
# get_review_context
# ---------------------------------------------------------------------------


@pytest.fixture()
def pve_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.proxmox_service, "list_nodes", lambda: [{"node": "pve-a"}])
    monkeypatch.setattr(
        svc.proxmox_service,
        "list_all_resources",
        lambda: [
            {"vmid": 900, "status": "stopped", "node": "pve-a"},
            {"vmid": 901, "status": "running", "node": "pve-a", "type": "qemu"},
        ],
    )


def test_review_context_errors(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NotFoundError):
        svc.get_review_context(session=db, request_id=uuid.uuid4(), current_user=ADMIN)
    no_window = _vm_request(db, start_at=None)
    with pytest.raises(BadRequestError):
        svc.get_review_context(session=db, request_id=no_window.id, current_user=ADMIN)
    unplaceable = _vm_request(db)
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "rebuild_reserved_assignments",
        lambda **_k: {},
    )
    with pytest.raises(BadRequestError):
        svc.get_review_context(
            session=db, request_id=unplaceable.id, current_user=ADMIN
        )


def test_review_context_open_ended_infeasible_with_warnings(
    db: Session, monkeypatch: pytest.MonkeyPatch, pve_listing: None
) -> None:
    request = _vm_request(db, end_at=None, resource_warning="storage almost full")
    selections: dict[uuid.UUID, Any] = {}

    def rebuild(**kwargs: Any) -> dict[uuid.UUID, Any]:
        projected = kwargs["requests"][-1]
        selections[projected.id] = _selection(
            "pve-a", feasible=False, warnings=["cpu tight"]
        )
        return selections

    def broken_scores(**_k: Any) -> list[Any]:
        raise RuntimeError("no metrics")

    monkeypatch.setattr(
        svc.vm_request_placement_service, "rebuild_reserved_assignments", rebuild
    )
    monkeypatch.setattr(
        svc.vm_request_placement_service, "get_preview_node_scores", broken_scores
    )

    context = svc.get_review_context(
        session=db, request_id=request.id, current_user=ADMIN
    )

    assert context.feasible is False
    assert context.node_scores == []
    assert context.resource_warnings[1:] == ["cpu tight", "storage almost full"]
    assert [item.vmid for item in context.current_running_resources] == [901]
    assert context.window_end > _now() + timedelta(days=3000)


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def _review(
    db: Session, request_id: uuid.UUID, status: str, comment: str | None = None
) -> Any:
    return svc.review(
        session=db,
        request_id=request_id,
        review_data=VMRequestReview(status=status, review_comment=comment),
        reviewer=ADMIN,
    )


def test_review_errors(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NotFoundError):
        _review(db, uuid.uuid4(), "approved")
    done = _vm_request(db, status=VMRequestStatus.rejected)
    with pytest.raises(BadRequestError):
        _review(db, done.id, "approved")
    no_window = _vm_request(db, start_at=None)
    with pytest.raises(BadRequestError):
        _review(db, no_window.id, "approved")
    ended = _vm_request(
        db,
        start_at=_now() - timedelta(hours=3),
        end_at=_now() - timedelta(hours=1),
    )
    with pytest.raises(BadRequestError):
        _review(db, ended.id, "approved")

    def value_error(**_k: Any) -> None:
        raise ValueError("cannot place")

    monkeypatch.setattr(svc, "_approve_and_place", value_error)
    pending = _vm_request(db)
    with pytest.raises(BadRequestError, match="cannot place"):
        _review(db, pending.id, "approved")


def test_review_approve_with_comment_submits_open_window(
    db: Session, monkeypatch: pytest.MonkeyPatch, no_audit: list[dict[str, Any]]
) -> None:
    request = _vm_request(db, start_at=_now() - timedelta(minutes=5))

    def place(*, session: Session, db_request: VMRequest, reviewer_id: uuid.UUID):
        db_request.status = VMRequestStatus.approved
        session.add(db_request)
        return _selection("pve-b")

    submitted: list[uuid.UUID] = []
    monkeypatch.setattr(svc, "_approve_and_place", place)
    monkeypatch.setattr(
        svc, "_submit_provision", lambda _s, req: submitted.append(req.id)
    )

    result = _review(db, request.id, "approved", comment="enjoy")

    assert result.review_comment == "enjoy"
    assert submitted == [request.id]
    assert "reserved node pve-b" in no_audit[0]["details"]
    assert "Comment: enjoy" in no_audit[0]["details"]


# ---------------------------------------------------------------------------
# cancel / retry
# ---------------------------------------------------------------------------


def test_cancel_errors(db: Session) -> None:
    with pytest.raises(NotFoundError):
        svc.cancel(session=db, request_id=uuid.uuid4(), current_user=ADMIN)
    rejected = _vm_request(db, status=VMRequestStatus.rejected)
    with pytest.raises(BadRequestError):
        svc.cancel(session=db, request_id=rejected.id, current_user=ADMIN)


def test_retry_errors(db: Session) -> None:
    with pytest.raises(NotFoundError):
        svc.retry(session=db, request_id=uuid.uuid4(), current_user=ADMIN)
    pending = _vm_request(db)
    with pytest.raises(BadRequestError):
        svc.retry(session=db, request_id=pending.id, current_user=ADMIN)


def test_review_reject(db: Session, no_audit: list[dict[str, Any]]) -> None:
    request = _vm_request(db)
    result = _review(db, request.id, "rejected", comment="no capacity")
    assert result.status == VMRequestStatus.rejected
    assert result.review_comment == "no capacity"
    assert no_audit[0]["details"].endswith("rejected. Comment: no capacity")
