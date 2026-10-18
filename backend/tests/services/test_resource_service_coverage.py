"""補齊 app/services/resource/resource_service.py 的分支覆蓋（PVE／DB 一律以假物件替換）。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.domain.resource_markers import RESOURCE_DELETED_BY_USER_MARKER
from app.exceptions import (
    BadRequestError,
    ConflictError,
    PermissionDeniedError,
    ProxmoxError,
)
from app.models import TeachingClassStatus
from app.models.vm_request import VMProvisioningStatus
from app.schemas import ResourcePublic
from app.services.network import (
    ip_management_service,
    nat_service,
    reverse_proxy_service,
)
from app.services.resource import deletion_service, resource_service
from app.services.template import clone_service

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """依序回傳 exec 結果的假 session（沒有 begin_nested，_savepoint 退回 nullcontext）。"""

    def __init__(self, exec_results=(), get_map=None):
        self._exec = list(exec_results)
        self._get = get_map or {}
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def exec(self, _stmt):
        return _Result(self._exec.pop(0) if self._exec else [])

    def get(self, _model, key):
        return self._get.get(key)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _db_resource(vmid: int = 100, **overrides) -> SimpleNamespace:
    base = {
        "vmid": vmid,
        "request_id": None,
        "environment_type": None,
        "os_info": None,
        "guest_os": None,
        "expiry_date": None,
        "template_id": None,
        "batch_job_id": None,
        "ssh_public_key": None,
        "login_password_encrypted": None,
        "login_password_hash": None,
        "login_password_pending_encrypted": None,
        "login_password_pending_hash": None,
        "idle_since": None,
        "auto_stop_at": None,
        "auto_stop_reason": None,
        "scheduled_deletion_at": None,
        "mining_exempt": False,
        "teaching_class_id": None,
        "allocation_scope": "personal",
        "control_policy": "owner",
        "user_id": uuid.uuid4(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _public(vmid: int | None = 100) -> ResourcePublic:
    return ResourcePublic(
        vmid=vmid, name="vm", status="running", node="pve1", type="qemu"
    )


def _patch_get_resource(monkeypatch, value):
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: value,
    )


def _policy(**overrides):
    base = {
        "practice_session_hours": 3,
        "practice_warning_minutes": 30,
        "expiry_warning_hours": 24,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session_policy(*, session: Any) -> SimpleNamespace:
    return _policy()


# ---------------------------------------------------------------------------
# 開機時段
# ---------------------------------------------------------------------------


def test_enforce_start_window_not_started(monkeypatch) -> None:
    _patch_get_resource(monkeypatch, None)
    monkeypatch.setattr(
        resource_service,
        "start_window_state",
        lambda **kw: ("window_not_started", None, None),
    )
    with pytest.raises(BadRequestError):
        resource_service._enforce_start_window(session=FakeSession(), vmid=1)


# ---------------------------------------------------------------------------
# LXC 平台金鑰／登入密碼
# ---------------------------------------------------------------------------


def test_ensure_lxc_platform_key_sync_incomplete(monkeypatch) -> None:
    _patch_get_resource(monkeypatch, _db_resource(ssh_public_key="ssh-ed25519 AAA"))
    monkeypatch.setattr(clone_service, "inject_lxc_platform_key", lambda n, v, k: False)
    assert (
        resource_service.ensure_lxc_platform_key(
            session=FakeSession(), node="n", vmid=1
        )
        is False
    )


def test_ensure_lxc_platform_key_exception(monkeypatch) -> None:
    def boom(*, session, vmid):
        raise RuntimeError("db gone")

    monkeypatch.setattr(resource_service.resource_repo, "get_resource_by_vmid", boom)
    assert (
        resource_service.ensure_lxc_platform_key(
            session=FakeSession(), node="n", vmid=1
        )
        is False
    )


def test_ensure_lxc_login_password_missing_resource(monkeypatch) -> None:
    _patch_get_resource(monkeypatch, None)
    assert (
        resource_service.ensure_lxc_login_password(
            session=FakeSession(), node="n", vmid=1
        )
        is False
    )


def test_promote_pending_password_savepoint_failure_rolls_back() -> None:
    class _BrokenSession(FakeSession):
        is_active = False

        def begin_nested(self):
            raise RuntimeError("savepoint failed")

    session = _BrokenSession()
    resource = _db_resource(login_password_pending_hash="$6$x")
    assert (
        resource_service._promote_pending_login_password(
            session=session, resource=resource, vmid=1
        )
        is False
    )
    assert session.rollbacks == 1


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def test_ensure_utc_converts_aware_datetime() -> None:
    tz8 = timezone(timedelta(hours=8))
    value = datetime(2026, 1, 1, 8, 0, tzinfo=tz8)
    assert resource_service._ensure_utc(value) == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 1, 1, 8, 0)
    assert resource_service._ensure_utc(naive) == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)


def test_placeholder_resource_status(monkeypatch) -> None:
    monkeypatch.setattr(resource_service, "_utc_now", lambda: NOW)
    failed = SimpleNamespace(
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error=None,
        start_at=None,
    )
    assert resource_service._placeholder_resource_status(failed) == "failed"
    scheduled = SimpleNamespace(
        provisioning_status=None,
        provisioning_error=None,
        start_at=NOW + timedelta(hours=1),
    )
    assert resource_service._placeholder_resource_status(scheduled) == "scheduled"
    now_req = SimpleNamespace(
        provisioning_status=None, provisioning_error=None, start_at=None
    )
    assert resource_service._placeholder_resource_status(now_req) == "provisioning"


def test_is_practice_resource_variants() -> None:
    rid = uuid.uuid4()
    db = _db_resource(request_id=rid)
    assert resource_service._is_practice_resource(FakeSession(), db, {rid}) is True
    assert (
        resource_service._is_practice_resource(FakeSession([[rid]]), db, None) is True
    )
    assert resource_service._is_practice_resource(FakeSession([[]]), db, None) is False


def test_public_urls_by_vmid_empty_and_rollback_failure(monkeypatch) -> None:
    assert resource_service.public_urls_by_vmid(FakeSession(), [None]) == {}

    def boom(session, vmids):
        raise RuntimeError("db broke")

    class _NoRollback(FakeSession):
        def rollback(self):
            raise RuntimeError("connection dead")

    monkeypatch.setattr(resource_service.rp_repo, "list_rules_by_vmids", boom)
    assert resource_service.public_urls_by_vmid(_NoRollback(), [1]) == {}


def test_teaching_display_names_unmatched_job_without_prefix() -> None:
    db = _db_resource(teaching_class_id=uuid.uuid4(), batch_job_id=uuid.uuid4())
    # nodes 查詢、jobs 查詢都回空
    session = FakeSession([[], []])
    assert (
        resource_service._teaching_display_names(session=session, db_resources=[db])
        == {}
    )


def test_prefetch_list_uses_matching_source_request(monkeypatch) -> None:
    rid = uuid.uuid4()
    db = _db_resource(vmid=100, request_id=rid)
    source = SimpleNamespace(id=rid, vmid=100)
    monkeypatch.setattr(
        resource_service.live_ip, "probe_live_ips", lambda entries, fn: {}
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "sync_ip_cache_many",
        lambda *, session, live_ips: {},
    )
    seen: dict[str, Any] = {}

    def latest(*, session, vmids, skip_closed):
        seen["vmids"] = vmids
        return {}

    monkeypatch.setattr(
        resource_service.vm_request_repo,
        "get_latest_approved_vm_requests_by_vmids",
        latest,
    )
    monkeypatch.setattr(
        resource_service, "_course_environment_names", lambda s, rows, tcs: {}
    )
    prefetch = resource_service._prefetch_list(
        FakeSession([[source]]), [({"vmid": 100}, db)]
    )
    assert prefetch.window_requests == {100: source}
    assert seen["vmids"] == []


def test_course_environment_names_skips_missing_vmid() -> None:
    rows = [_db_resource(vmid=None)]
    assert resource_service._course_environment_names(FakeSession(), rows, {}) == {}


def test_build_resource_public_quick_practice_source(monkeypatch) -> None:
    rid = uuid.uuid4()
    db = _db_resource(vmid=100, request_id=rid)
    prefetch = resource_service._ListPrefetch(
        source_requests={rid: SimpleNamespace(request_kind="quick_template")}
    )
    public = resource_service._build_resource_public(
        {"vmid": 100, "name": "vm", "status": "running"},
        db,
        "pve1",
        "qemu",
        FakeSession(),
        known_practice_ids=set(),
        public_urls={},
        display_names={},
        prefetch=prefetch,
    )
    assert public.can_extend is False
    assert public.machine_kind == "quick_practice"


# ---------------------------------------------------------------------------
# annotate_access_for_user / _mark_shared
# ---------------------------------------------------------------------------


@pytest.fixture
def access_env(monkeypatch):
    state: dict[str, Any] = {
        "db": None,
        "relation": None,
        "bypass": False,
        "share": None,
        "manage_error": False,
    }
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: state["db"],
    )
    monkeypatch.setattr(
        resource_service, "can_bypass_resource_ownership", lambda user: state["bypass"]
    )
    monkeypatch.setattr(
        resource_service, "list_owned_teaching_class_ids", lambda *, session, user: []
    )
    monkeypatch.setattr(
        resource_service.resource_kind,
        "class_relation_for",
        lambda db, *, viewer_id, owned_class_ids: state["relation"],
    )
    monkeypatch.setattr(
        resource_service.resource_kind,
        "user_display_names",
        lambda session, ids: dict.fromkeys(ids, "Owner Name"),
    )
    monkeypatch.setattr(
        resource_service.share_repo,
        "get_share",
        lambda *, session, vmid, user_id: state["share"],
    )

    def manage(*, session, user, vmid):
        if state["manage_error"]:
            raise PermissionDeniedError("no")

    monkeypatch.setattr(resource_service, "require_resource_management", manage)
    return state


def test_annotate_access_without_vmid(access_env) -> None:
    public = _public(vmid=None)
    assert (
        resource_service.annotate_access_for_user(
            session=FakeSession(), public=public, user=SimpleNamespace(id=uuid.uuid4())
        )
        is public
    )


@pytest.mark.parametrize(("bypass", "role"), [(True, "admin"), (False, "owner")])
def test_annotate_access_untracked(access_env, bypass, role) -> None:
    access_env["bypass"] = bypass
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=SimpleNamespace(id=uuid.uuid4())
    )
    assert public.access_role == role


def test_annotate_access_owner_roles(access_env) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    access_env["db"] = _db_resource(user_id=user.id)
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.access_role == "owner"
    assert public.can_manage is True
    assert public.owner_name is None

    access_env["db"] = _db_resource(user_id=user.id, allocation_scope="teaching_class")
    access_env["manage_error"] = True
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.access_role == "class_member"
    assert public.can_manage is False


def test_annotate_access_teacher_admin_and_fallback(access_env) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    access_env["db"] = _db_resource()

    access_env["relation"] = "teacher"
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.access_role == "class_teacher"
    assert public.owner_name == "Owner Name"

    access_env["relation"] = None
    access_env["bypass"] = True
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.access_role == "admin"

    access_env["bypass"] = False
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.access_role == "admin"


def test_annotate_access_shared(access_env) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    owner_id = uuid.uuid4()
    access_env["db"] = _db_resource(user_id=owner_id)
    access_env["share"] = object()
    owner = SimpleNamespace(email="o@example.com", full_name=None)
    public = resource_service.annotate_access_for_user(
        session=FakeSession(get_map={owner_id: owner}), public=_public(), user=user
    )
    assert public.access_role == "shared"
    assert public.machine_kind == "shared"
    assert public.can_manage is False
    assert public.owner_email == "o@example.com"
    assert public.owner_name == "o@example.com"

    # 擁有者帳號已不存在
    public = resource_service.annotate_access_for_user(
        session=FakeSession(), public=_public(), user=user
    )
    assert public.owner_email is None
    assert public.owner_name is None


# ---------------------------------------------------------------------------
# list_all / list_by_user
# ---------------------------------------------------------------------------


def test_list_all_wraps_errors(monkeypatch) -> None:
    def boom():
        raise RuntimeError("pve down")

    monkeypatch.setattr(resource_service.proxmox_service, "list_all_resources", boom)
    with pytest.raises(ProxmoxError):
        resource_service.list_all(session=FakeSession())


def test_list_by_user_wraps_errors(monkeypatch) -> None:
    def boom(*, session, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(resource_service.resource_repo, "get_resources_by_user", boom)
    with pytest.raises(ProxmoxError):
        resource_service.list_by_user(session=FakeSession(), user_id=uuid.uuid4())


def test_list_by_user_shared_taught_and_pending(monkeypatch) -> None:
    user_id = uuid.uuid4()
    student_id = uuid.uuid4()
    class_id = uuid.uuid4()
    owned = _db_resource(vmid=100, user_id=user_id)
    shared = _db_resource(vmid=200, user_id=uuid.uuid4())
    taught = _db_resource(
        vmid=300,
        user_id=student_id,
        teaching_class_id=class_id,
        allocation_scope="teaching_class",
    )

    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_user",
        lambda *, session, user_id: [owned],
    )
    monkeypatch.setattr(
        resource_service.share_repo,
        "list_shares_for_user",
        lambda *, session, user_id: [
            SimpleNamespace(resource_vmid=200),
            SimpleNamespace(resource_vmid=100),
        ],
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_vmids",
        lambda *, session, vmids: {200: shared} if 200 in vmids else {},
    )
    monkeypatch.setattr(
        resource_service,
        "list_teaching_class_ids_owned_by",
        lambda *, session, user_id: [class_id],
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_teaching_classes",
        lambda *, session, teaching_class_ids: [taught, owned, shared],
    )
    monkeypatch.setattr(
        resource_service.resource_kind,
        "user_display_names",
        lambda session, ids: dict.fromkeys(ids, "Student"),
    )
    monkeypatch.setattr(
        resource_service.resource_kind, "practice_request_ids", lambda session: set()
    )
    monkeypatch.setattr(resource_service, "public_urls_by_vmid", lambda s, v: {})
    monkeypatch.setattr(
        resource_service, "_teaching_display_names", lambda *, session, db_resources: {}
    )
    monkeypatch.setattr(resource_service, "end_read_transaction", lambda s: None)
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "list_all_resources",
        lambda: [
            {"vmid": 900, "template": 1},
            {"vmid": 100, "node": "pve1", "type": "qemu"},
            {"vmid": 200, "node": "pve1", "type": "qemu"},
            {"vmid": 300, "node": "pve1", "type": "lxc"},
            {"vmid": 400, "node": "pve1", "type": "qemu"},
        ],
    )
    monkeypatch.setattr(
        resource_service,
        "_prefetch_list",
        lambda s, pairs: resource_service._ListPrefetch(),
    )
    monkeypatch.setattr(
        resource_service,
        "_build_resource_public",
        lambda r, db, node, vm_type, session, *a, **kw: ResourcePublic(
            vmid=r["vmid"], name="vm", status="running", node=node, type=vm_type
        ),
    )
    monkeypatch.setattr(
        resource_service.proxmox_service, "list_booting_vmids", lambda nodes: set()
    )
    monkeypatch.setattr(
        deletion_service, "list_active_for_vmids", lambda *, session, vmids: {}
    )

    pending = [
        SimpleNamespace(vmid=100, resource_warning=None),
        SimpleNamespace(vmid=None, resource_warning=RESOURCE_DELETED_BY_USER_MARKER),
    ]
    session = FakeSession([pending, []])
    result = {
        p.vmid: p
        for p in resource_service.list_by_user(session=session, user_id=user_id)
    }

    assert set(result) == {100, 200, 300}
    assert result[200].access_role == "shared"
    assert result[300].access_role == "class_teacher"
    assert result[300].class_relation == "teacher"
    assert result[300].can_manage is True
    assert result[300].owner_name == "Student"
    assert result[100].access_role == "owner"


# ---------------------------------------------------------------------------
# 刪除流程小步驟
# ---------------------------------------------------------------------------


def test_cancel_open_spec_change_requests_logs_count(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        resource_service.spec_request_repo,
        "cancel_open_spec_change_requests_for_vmid",
        lambda **kw: calls.append(kw) or 2,
    )
    resource_service._cancel_open_spec_change_requests(
        session=FakeSession(), vmid=1, marker="m"
    )
    assert calls and calls[0]["vmid"] == 1


def test_restore_linked_request_noop_and_restore_failure() -> None:
    session = FakeSession()
    resource_service.restore_linked_request(session=session, snapshot=None)
    assert session.commits == 0

    class _Broken(FakeSession):
        def rollback(self):
            raise RuntimeError("rollback failed")

    # 失敗只記 warning，不往外拋
    resource_service._restore_after_failed_delete(
        session=_Broken(), snapshot={"request": object()}, vmid=1
    )


def test_cleanup_after_resource_removed_all_steps_fail(monkeypatch) -> None:
    def boom(*a, **kw):
        raise RuntimeError("step failed")

    monkeypatch.setattr(
        reverse_proxy_service, "remove_reverse_proxy_rules_for_vmid", boom
    )
    monkeypatch.setattr(nat_service, "remove_nat_rules_for_vmid", boom)
    monkeypatch.setattr(ip_management_service, "release_ip", boom)
    monkeypatch.setattr(
        resource_service.batch_provision_repo, "clear_task_vmid_references", boom
    )
    monkeypatch.setattr(
        resource_service.spec_request_repo,
        "cancel_open_spec_change_requests_for_vmid",
        lambda **kw: 0,
    )
    deleted = []
    monkeypatch.setattr(
        resource_service.resource_repo,
        "delete_resource",
        lambda *, session, vmid: deleted.append(vmid),
    )
    monkeypatch.setattr(
        resource_service, "_mark_class_reclaimed_if_empty", lambda **kw: None
    )
    resource_service._cleanup_after_resource_removed(
        session=FakeSession(), vmid=5, teaching_class_id=None, marker="m"
    )
    assert deleted == [5]


def test_cleanup_after_resource_removed_clears_batch_refs(monkeypatch) -> None:
    monkeypatch.setattr(
        reverse_proxy_service, "remove_reverse_proxy_rules_for_vmid", lambda s, v: None
    )
    monkeypatch.setattr(nat_service, "remove_nat_rules_for_vmid", lambda s, v: None)
    monkeypatch.setattr(ip_management_service, "release_ip", lambda s, v: None)
    monkeypatch.setattr(
        resource_service.batch_provision_repo,
        "clear_task_vmid_references",
        lambda *, session, vmid, commit: 3,
    )
    monkeypatch.setattr(
        resource_service.spec_request_repo,
        "cancel_open_spec_change_requests_for_vmid",
        lambda **kw: 0,
    )
    monkeypatch.setattr(
        resource_service.resource_repo, "delete_resource", lambda *, session, vmid: None
    )
    reclaimed = []
    monkeypatch.setattr(
        resource_service,
        "_mark_class_reclaimed_if_empty",
        lambda *, session, teaching_class_id: reclaimed.append(teaching_class_id),
    )
    resource_service._cleanup_after_resource_removed(
        session=FakeSession(), vmid=5, teaching_class_id=None, marker="m"
    )
    assert reclaimed == [None]


def test_mark_class_reclaimed_if_empty(monkeypatch) -> None:
    monkeypatch.setattr(resource_service, "_utc_now", lambda: NOW)
    # 沒有班級
    resource_service._mark_class_reclaimed_if_empty(
        session=FakeSession(), teaching_class_id=None
    )
    # 班級不存在
    missing = FakeSession()
    resource_service._mark_class_reclaimed_if_empty(
        session=missing, teaching_class_id=uuid.uuid4()
    )
    assert missing.commits == 0

    remaining: dict[str, list] = {"rows": [object()]}
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resources_by_teaching_class",
        lambda *, session, teaching_class_id: remaining["rows"],
    )
    cid = uuid.uuid4()
    active = SimpleNamespace(
        status=TeachingClassStatus.active, updated_at=None, resources_reclaimed_at=None
    )
    session = FakeSession(get_map={cid: active})
    resource_service._mark_class_reclaimed_if_empty(
        session=session, teaching_class_id=cid
    )
    assert active.status == TeachingClassStatus.partial_failed
    assert active.resources_reclaimed_at is None
    assert session.commits == 1

    remaining["rows"] = []
    archived = SimpleNamespace(
        status=TeachingClassStatus.archived,
        updated_at=None,
        resources_reclaimed_at=None,
    )
    session = FakeSession(get_map={cid: archived})
    resource_service._mark_class_reclaimed_if_empty(
        session=session, teaching_class_id=cid
    )
    assert archived.status == TeachingClassStatus.archived
    assert archived.resources_reclaimed_at == NOW
    assert session.commits == 1


def test_ensure_stopped_before_delete_graceful_failure_falls_back(monkeypatch) -> None:
    actions = []

    def control(node, vmid, rtype, action):
        actions.append(action)
        if action == "shutdown":
            raise RuntimeError("agent missing")

    monkeypatch.setattr(resource_service.proxmox_service, "control", control)
    monkeypatch.setattr(resource_service, "_wait_until_stopped", lambda *a, **kw: True)
    resource_service._ensure_stopped_before_delete("n", 1, "qemu", force=False)
    assert actions == ["shutdown", "stop"]


def test_delete_unexpected_error_wrapped(monkeypatch) -> None:
    _patch_get_resource(monkeypatch, None)
    with pytest.raises(ProxmoxError):
        resource_service.delete(
            session=FakeSession(), vmid=1, resource_info={}, user_id=uuid.uuid4()
        )


# ---------------------------------------------------------------------------
# config / control / stats / spec
# ---------------------------------------------------------------------------


def test_get_config_filters_and_wraps(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_config",
        lambda node, vmid, rtype: {
            "cores": 2,
            "scsi0": "local:vm-1-disk-0",
            "net0": "virtio=AA",
            "netx": "nope",
            "cipassword": "secret",
            "sshkeys": "k",
        },
    )
    cfg = resource_service.get_config(
        vmid=1, resource_info={"node": "n", "type": "qemu"}
    )
    assert cfg == {"cores": 2, "scsi0": "local:vm-1-disk-0", "net0": "virtio=AA"}

    with pytest.raises(ProxmoxError):
        resource_service.get_config(vmid=1, resource_info={})


def test_control_invalid_action() -> None:
    with pytest.raises(BadRequestError):
        resource_service.control(
            session=FakeSession(),
            vmid=1,
            action="explode",
            resource_info={"node": "n", "type": "qemu"},
            user_id=uuid.uuid4(),
        )


@pytest.fixture
def control_env(monkeypatch):
    calls: dict[str, list] = {"ssh": [], "auto": [], "audit": [], "fw": []}
    monkeypatch.setattr(resource_service, "_enforce_start_window", lambda **kw: None)
    monkeypatch.setattr(resource_service.proxmox_service, "control", lambda *a: None)
    monkeypatch.setattr(
        resource_service.guest_ssh_login,
        "schedule_after_start",
        lambda node, vmid, rtype: calls["ssh"].append(vmid),
    )
    monkeypatch.setattr(
        resource_service.firewall_service,
        "ensure_firewall_enabled",
        lambda node, vmid, rtype: calls["fw"].append(vmid),
    )
    monkeypatch.setattr(
        resource_service.firewall_service,
        "ensure_platform_ssh_rule",
        lambda session, node, vmid, rtype: calls["fw"].append(("ssh", vmid)),
    )
    monkeypatch.setattr(
        resource_service, "_set_auto_stop_for_user_start", lambda **kw: None
    )
    monkeypatch.setattr(
        resource_service.resource_repo,
        "set_auto_stop",
        lambda **kw: calls["auto"].append(kw),
    )
    monkeypatch.setattr(
        resource_service.audit_service,
        "log_action",
        lambda **kw: calls["audit"].append(kw["action"]),
    )
    return calls


def test_control_start_qemu_schedules_guest_ssh(control_env) -> None:
    out = resource_service.control(
        session=FakeSession(),
        vmid=7,
        action="start",
        resource_info={"node": "n", "type": "qemu"},
        user_id=uuid.uuid4(),
    )
    assert out["message"] == "Resource 7 start"
    assert control_env["ssh"] == [7]
    assert control_env["fw"] == [7, ("ssh", 7)]
    assert control_env["audit"] == ["resource_start"]


@pytest.mark.parametrize("action", ["start", "reboot", "reset"])
def test_control_refuses_to_boot_template_source(
    monkeypatch, control_env, action: str
) -> None:
    # 平台範本的母機在 PVE 端是一般機器，開機會改到範本內容
    powered: list[tuple] = []
    monkeypatch.setattr(
        resource_service.proxmox_service, "control", lambda *a: powered.append(a)
    )
    monkeypatch.setattr(
        resource_service.proxmox_service, "platform_template_vmids", lambda: {213}
    )
    with pytest.raises(ConflictError):
        resource_service.control(
            session=FakeSession(),
            vmid=213,
            action=action,
            resource_info={"node": "n", "type": "lxc"},
            user_id=uuid.uuid4(),
        )
    assert powered == []


def test_control_still_stops_template_source(monkeypatch, control_env) -> None:
    # 被誤開的母機要能從平台關掉
    _patch_get_resource(monkeypatch, None)
    powered: list[tuple] = []
    monkeypatch.setattr(
        resource_service.proxmox_service, "control", lambda *a: powered.append(a)
    )
    monkeypatch.setattr(
        resource_service.proxmox_service, "platform_template_vmids", lambda: {213}
    )
    resource_service.control(
        session=FakeSession(),
        vmid=213,
        action="shutdown",
        resource_info={"node": "n", "type": "lxc"},
        user_id=uuid.uuid4(),
    )
    assert powered == [("n", 213, "lxc", "shutdown")]


def test_control_unexpected_error_wrapped(monkeypatch, control_env) -> None:
    def boom(*a):
        raise RuntimeError("pve error")

    monkeypatch.setattr(resource_service.proxmox_service, "control", boom)
    with pytest.raises(ProxmoxError, match="pve error"):
        resource_service.control(
            session=FakeSession(),
            vmid=7,
            action="stop",
            resource_info={"node": "n", "type": "qemu"},
            user_id=uuid.uuid4(),
        )


def test_get_current_stats(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_status",
        lambda node, vmid, rtype: {
            "cpu": 0.5,
            "maxcpu": 4,
            "mem": 1,
            "status": "running",
        },
    )
    stats = resource_service.get_current_stats(
        vmid=1, resource_info={"node": "n", "type": "qemu"}
    )
    assert stats["maxcpu"] == 4
    assert stats["status"] == "running"
    with pytest.raises(ProxmoxError):
        resource_service.get_current_stats(vmid=1, resource_info={})


def test_get_rrd_stats(monkeypatch) -> None:
    with pytest.raises(BadRequestError):
        resource_service.get_rrd_stats(vmid=1, resource_info={}, timeframe="decade")
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "get_rrd_data",
        lambda node, vmid, rtype, tf: [{"time": 1, "tf": tf}],
    )
    assert resource_service.get_rrd_stats(
        vmid=1, resource_info={"node": "n", "type": "qemu"}, timeframe="hour"
    ) == [{"time": 1, "tf": "hour"}]
    with pytest.raises(ProxmoxError):
        resource_service.get_rrd_stats(vmid=1, resource_info={}, timeframe="day")

    def bad_request(node, vmid, rtype, tf):
        raise BadRequestError("bad timeframe upstream")

    monkeypatch.setattr(resource_service.proxmox_service, "get_rrd_data", bad_request)
    with pytest.raises(BadRequestError):
        resource_service.get_rrd_stats(
            vmid=1, resource_info={"node": "n", "type": "qemu"}, timeframe="week"
        )


@pytest.fixture
def spec_env(monkeypatch):
    calls: dict[str, list] = {"config": [], "resize": [], "audit": []}
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "update_config",
        lambda node, vmid, rtype, **kw: calls["config"].append(kw),
    )
    monkeypatch.setattr(
        resource_service.proxmox_service,
        "resize_disk",
        lambda node, vmid, rtype, disk, size: calls["resize"].append((disk, size)),
    )
    monkeypatch.setattr(
        resource_service.audit_service,
        "log_action",
        lambda **kw: calls["audit"].append(kw["details"]),
    )
    return calls


def test_direct_update_spec_all_fields_qemu(spec_env) -> None:
    out = resource_service.direct_update_spec(
        session=FakeSession(),
        vmid=1,
        resource_info={"node": "n", "type": "qemu"},
        user_id=uuid.uuid4(),
        cores=4,
        memory=2048,
        disk_size="+10G",
    )
    assert spec_env["config"] == [{"cores": 4, "memory": 2048}]
    assert spec_env["resize"] == [("scsi0", "+10G")]
    assert "Disk: +10G" in out["message"]


def test_direct_update_spec_errors(spec_env) -> None:
    with pytest.raises(BadRequestError):
        resource_service.direct_update_spec(
            session=FakeSession(),
            vmid=1,
            resource_info={"node": "n", "type": "qemu"},
            user_id=uuid.uuid4(),
        )
    with pytest.raises(ProxmoxError):
        resource_service.direct_update_spec(
            session=FakeSession(),
            vmid=1,
            resource_info={},
            user_id=uuid.uuid4(),
            cores=1,
        )


# ---------------------------------------------------------------------------
# auto-stop / session
# ---------------------------------------------------------------------------


@pytest.fixture
def autostop_env(monkeypatch):
    state: dict[str, Any] = {"resource": None, "request": None, "in_window": False}
    calls: list[dict] = []
    monkeypatch.setattr(resource_service, "_utc_now", lambda: NOW)
    monkeypatch.setattr(resource_service, "get_schedule_policy", _session_policy)
    monkeypatch.setattr(
        resource_service.resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: state["resource"],
    )
    monkeypatch.setattr(
        resource_service.resource_repo, "set_auto_stop", lambda **kw: calls.append(kw)
    )
    monkeypatch.setattr(
        resource_service.vm_request_repo,
        "get_window_request_for_vmid",
        lambda *, session, vmid, source_request_id: state["request"],
    )
    monkeypatch.setattr(
        resource_service, "is_in_window", lambda start, end, now: state["in_window"]
    )
    return state, calls


def test_set_auto_stop_in_class_window(autostop_env) -> None:
    state, calls = autostop_env
    job_id = uuid.uuid4()
    state["resource"] = _db_resource(
        teaching_class_id=uuid.uuid4(), batch_job_id=job_id
    )
    state["in_window"] = True
    window_end = NOW + timedelta(hours=1)
    job = SimpleNamespace(next_window_start=NOW, next_window_end=window_end)
    resource_service._set_auto_stop_for_user_start(
        session=FakeSession(get_map={job_id: job}), vmid=1
    )
    assert calls == [
        {
            "session": calls[0]["session"],
            "vmid": 1,
            "auto_stop_at": window_end,
            "auto_stop_reason": "window_grace",
        }
    ]


def test_set_auto_stop_in_request_window_keeps_grace(autostop_env) -> None:
    state, calls = autostop_env
    state["request"] = SimpleNamespace(next_window_start=NOW, next_window_end=NOW)
    state["in_window"] = True
    resource_service._set_auto_stop_for_user_start(session=FakeSession(), vmid=1)
    assert calls == []


def test_set_auto_stop_outside_window_uses_practice_quota(autostop_env) -> None:
    state, calls = autostop_env
    state["resource"] = _db_resource(
        teaching_class_id=uuid.uuid4(), batch_job_id=uuid.uuid4()
    )
    resource_service._set_auto_stop_for_user_start(session=FakeSession(), vmid=1)
    assert calls[0]["auto_stop_reason"] == "practice_quota"
    assert calls[0]["auto_stop_at"] == NOW + timedelta(hours=3)


def test_extend_session_rejections(monkeypatch) -> None:
    user_id = uuid.uuid4()
    _patch_get_resource(monkeypatch, None)
    with pytest.raises(BadRequestError, match="not found"):
        resource_service.extend_session(session=FakeSession(), vmid=1, user_id=user_id)

    _patch_get_resource(monkeypatch, _db_resource(user_id=uuid.uuid4()))
    with pytest.raises(BadRequestError, match="owner"):
        resource_service.extend_session(session=FakeSession(), vmid=1, user_id=user_id)

    _patch_get_resource(
        monkeypatch, _db_resource(user_id=user_id, auto_stop_reason=None)
    )
    with pytest.raises(BadRequestError, match="automatic stop"):
        resource_service.extend_session(session=FakeSession(), vmid=1, user_id=user_id)


def test_get_session_status_expiry_warning(monkeypatch) -> None:
    monkeypatch.setattr(resource_service, "_utc_now", lambda: NOW)
    monkeypatch.setattr(resource_service, "get_schedule_policy", _session_policy)
    _patch_get_resource(monkeypatch, _db_resource(expiry_date=date(2026, 10, 15)))
    status = resource_service.get_session_status(
        session=FakeSession(), vmid=1, resource_info={"status": "running"}
    )
    assert status.warn_reason == "expiry"
    assert status.hours_until_expiry == 12
    assert status.should_warn is True


def test_session_status_no_warning(monkeypatch) -> None:
    monkeypatch.setattr(resource_service, "_utc_now", lambda: NOW)
    status = resource_service._session_status(
        session=FakeSession(),
        vmid=1,
        resource=_db_resource(expiry_date=date(2026, 12, 31)),
        running=True,
        policy=_policy(),
    )
    assert status.warn_reason is None
    assert status.should_warn is False


# ---------------------------------------------------------------------------
# batch_action
# ---------------------------------------------------------------------------


def test_batch_action_delete_control_and_errors(monkeypatch) -> None:
    def find(vmid):
        if vmid == 3:
            raise RuntimeError("not found")
        return {"node": "n", "type": "qemu"}

    monkeypatch.setattr(resource_service.proxmox_service, "find_resource", find)
    monkeypatch.setattr(
        resource_service, "require_resource_management", lambda **kw: None
    )
    monkeypatch.setattr(resource_service, "require_resource_use", lambda **kw: None)
    queued = []
    monkeypatch.setattr(
        deletion_service,
        "create_deletion_request",
        lambda **kw: SimpleNamespace(vmid=kw["vmid"]),
    )
    monkeypatch.setattr(
        deletion_service,
        "enqueue_processing",
        lambda *, session, req: queued.append(req.vmid),
    )
    controlled = []
    monkeypatch.setattr(
        resource_service,
        "control",
        lambda **kw: controlled.append((kw["vmid"], kw["action"])),
    )
    user = SimpleNamespace(id=uuid.uuid4())

    out = resource_service.batch_action(
        session=FakeSession(), vmids=[1, 3], action="delete", user=user
    )
    assert queued == [1]
    assert out.succeeded == 1
    assert out.failed == 1
    assert out.results[1].message == "not found"

    out = resource_service.batch_action(
        session=FakeSession(), vmids=[2], action="start", user=user
    )
    assert controlled == [(2, "start")]
    assert out.results[0].message == "Resource 2 start succeeded"
