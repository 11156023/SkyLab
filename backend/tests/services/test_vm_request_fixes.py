"""稽核修復的回歸測試（services/vm）。

- LXC 克隆申請在可用性檢查時也要帶範本節點約束
- 可用性月曆不重複扣已建機的申請，且要扣 GPU 槽
- select_reserved_target_node_for_request 不再有無作用的參數
- 申請時段必須有上限，逐小時檢查的成本不能跟時段長度成正比
- 自動核准重排保留失敗時回在地化 400，而不是 500
- 非法時區字串回 400
- 送單時的配額依資源類型取正確的磁碟欄位
- 時段不可行的錯誤訊息走 i18n，不外洩 advisor 的英文摘要
"""

from __future__ import annotations

import inspect
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
from app.core.i18n import t
from app.domain.placement.schemas import NodeCapacity
from app.exceptions import BadRequestError
from app.models.vm_request import VMRequest
from app.schemas.vm_request import (
    VMRequestAvailabilityRequest,
    VMRequestWindowAvailabilityRequest,
)
from app.services.vm import placement_service, placement_support, vm_request_service
from app.services.vm import vm_request_availability_service as svc
from app.utils.timeutil import normalize_datetime

GIB = 1024**3
_ENGLISH_SUMMARY = "LXC request cannot be placed on the current PVE nodes."


def _capacity(node: str = "pve-a", gpu_slots: int = 0) -> NodeCapacity:
    return NodeCapacity(
        node=node,
        status="online",
        candidate=True,
        running_resources=3,
        guest_soft_limit=16,
        guest_pressure_ratio=0.2,
        guest_overloaded=False,
        cpu_ratio=0.2,
        memory_ratio=0.3,
        disk_ratio=0.25,
        total_cpu_cores=16,
        allocatable_cpu_cores=10.0,
        total_memory_bytes=64 * GIB,
        allocatable_memory_bytes=40 * GIB,
        total_disk_bytes=1000 * GIB,
        allocatable_disk_bytes=700 * GIB,
        gpu_count=gpu_slots,
        allocatable_gpu_slots=gpu_slots,
    )


def _reservation(
    start: datetime,
    end: datetime,
    *,
    vmid: int | None = None,
    gpu_mapping_id: str | None = None,
) -> VMRequest:
    return VMRequest(
        user_id=uuid.uuid4(),
        reason="test",
        resource_type="lxc",
        hostname="box",
        password="x",
        cores=2,
        memory=1024,
        rootfs_size=8,
        start_at=start,
        end_at=end,
        assigned_node="pve-a",
        vmid=vmid,
        gpu_mapping_id=gpu_mapping_id,
    )


def _slot_starts(count: int) -> list[datetime]:
    base = datetime(2026, 9, 2, 0, tzinfo=UTC)
    return [base + timedelta(hours=i) for i in range(count)]


def _infeasible_selection():
    return SimpleNamespace(
        node=None,
        strategy="balanced",
        plan=SimpleNamespace(feasible=False, summary=_ENGLISH_SUMMARY, warnings=[]),
    )




def test_template_constraints_pin_lxc_clone_to_template() -> None:
    assert svc._template_constraints(
        resource_type="lxc", ostemplate="local:vztmpl/x.tar.zst", template_id=123
    ) == (None, 123)
    assert svc._template_constraints(
        resource_type="lxc", ostemplate="local:vztmpl/x.tar.zst", template_id=None
    ) == ("local:vztmpl/x.tar.zst", None)
    assert svc._template_constraints(
        resource_type="vm", ostemplate=None, template_id=9000
    ) == (None, 9000)
    assert svc._template_constraints(
        resource_type="vm", ostemplate="ignored", template_id=None
    ) == (None, None)


def test_calendar_placement_request_matches_approval_rule_for_lxc_clone() -> None:
    request = svc._to_placement_request(
        VMRequestAvailabilityRequest(resource_type="lxc", template_id=321)
    )
    assert request.template_vmid == 321
    assert request.ostemplate is None

    # 與核准路徑（placement_support.to_placement_request）同一個答案
    db_request = SimpleNamespace(
        resource_type="lxc",
        cores=2,
        memory=2048,
        disk_size=None,
        rootfs_size=None,
        template_id=321,
        ostemplate="local:vztmpl/x.tar.zst",
        gpu_mapping_id=None,
        placement_group_id=None,
    )
    approval = placement_support.to_placement_request(db_request)  # type: ignore[arg-type]
    assert (approval.ostemplate, approval.template_vmid) == (None, 321)


def test_window_validation_uses_template_node_for_lxc_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_select(**kwargs):
        captured["request"] = kwargs["request"]
        return SimpleNamespace(
            node="pve-a",
            strategy="balanced",
            plan=SimpleNamespace(feasible=True, summary="ok", warnings=[]),
        )

    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        fake_select,
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    svc.validate_request_window(
        session=None,  # type: ignore[arg-type]
        current_user=None,
        request_in=SimpleNamespace(
            resource_type="lxc",
            cores=2,
            memory=2048,
            disk_size=None,
            rootfs_size=16,
            gpu_mapping_id=None,
            ostemplate=None,
            template_id=555,
            start_at=start,
            end_at=start + timedelta(hours=3),
        ),
    )
    assert captured["request"].template_vmid == 555




def test_timeline_skips_already_provisioned_requests() -> None:
    starts = _slot_starts(4)
    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=[_capacity()],
        reserved_requests=[_reservation(starts[0], starts[3], vmid=101)],
        slot_starts=starts,
    )
    for slot in starts:
        assert timeline[slot][0].allocatable_cpu_cores == 10.0
        assert timeline[slot][0].allocatable_memory_bytes == 40 * GIB


def test_timeline_subtracts_reserved_gpu_slots() -> None:
    starts = _slot_starts(4)
    timeline = svc._build_reserved_capacity_timeline(
        baseline_capacities=[_capacity(gpu_slots=2)],
        reserved_requests=[
            _reservation(starts[1], starts[3], gpu_mapping_id="h200"),
        ],
        slot_starts=starts,
    )
    assert timeline[starts[0]][0].allocatable_gpu_slots == 2
    assert timeline[starts[1]][0].allocatable_gpu_slots == 1
    assert timeline[starts[2]][0].allocatable_gpu_slots == 1
    assert timeline[starts[3]][0].allocatable_gpu_slots == 2




def test_reserved_selection_cohort_parameter_is_optional_and_deprecated() -> None:
    # allow_cohort_optimization 已不影響結果，只為相容舊呼叫端保留且必須可省略。
    params = inspect.signature(
        placement_service.select_reserved_target_node_for_request
    ).parameters
    param = params.get("allow_cohort_optimization")
    assert param is not None
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is True




def test_window_bounds_reject_overlong_and_ancient_windows() -> None:
    now = datetime.now(UTC)
    with pytest.raises(BadRequestError):
        svc.ensure_window_bounds(
            start_at=now,
            end_at=now + timedelta(days=svc.MAX_REQUEST_WINDOW_DAYS + 1),
        )
    with pytest.raises(BadRequestError):
        svc.ensure_window_bounds(
            start_at=datetime(1, 1, 1, tzinfo=UTC),
            end_at=datetime(1, 1, 2, tzinfo=UTC),
        )
    # 表單停留幾分鐘、開始時間略早於現在仍可接受
    svc.ensure_window_bounds(
        start_at=now - timedelta(minutes=5), end_at=now + timedelta(days=90)
    )


def test_window_availability_rejects_huge_window_before_placement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(**kwargs):
        raise AssertionError("placement must not run for an unbounded window")

    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        boom,
    )
    with pytest.raises(BadRequestError):
        svc.assess_request_window(
            session=None,  # type: ignore[arg-type]
            current_user=None,
            request_in=VMRequestWindowAvailabilityRequest(
                start_at=datetime(1, 1, 1, tzinfo=UTC),
                end_at=datetime(9999, 12, 31, tzinfo=UTC),
            ),
        )
    start = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(BadRequestError):
        svc.assess_request_window(
            session=None,  # type: ignore[arg-type]
            current_user=None,
            request_in=VMRequestWindowAvailabilityRequest(
                start_at=start, end_at=start + timedelta(days=365 * 20)
            ),
        )


def _brute_force_active_sets(start_at, end_at, reserved):
    """舊實作：start_at 加上之後每一個整點。"""
    cursor = start_at.replace(minute=0, second=0, microsecond=0)
    if cursor < start_at:
        cursor += timedelta(hours=1)
    grid = []
    while cursor < end_at:
        grid.append(cursor)
        cursor += timedelta(hours=1)
    checkpoints = [start_at] + [c for c in (grid or [start_at]) if c != start_at]
    return checkpoints, _active_sets(checkpoints, reserved)


def _active_sets(checkpoints, reserved):
    return {
        frozenset(
            index
            for index, item in enumerate(reserved)
            if normalize_datetime(item.start_at)
            <= checkpoint
            < normalize_datetime(item.end_at)
        )
        for checkpoint in checkpoints
    }


def test_window_checkpoints_cover_every_reservation_state() -> None:
    rng = random.Random(20260927)
    base = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    for _ in range(200):
        start_at = base + timedelta(minutes=rng.randint(0, 600))
        end_at = start_at + timedelta(minutes=rng.randint(1, 3000))
        reserved = []
        for _ in range(rng.randint(0, 6)):
            r_start = base + timedelta(minutes=rng.randint(-600, 3600))
            r_end = r_start + timedelta(minutes=rng.randint(1, 1500))
            reserved.append(SimpleNamespace(start_at=r_start, end_at=r_end))
        brute_points, brute_sets = _brute_force_active_sets(start_at, end_at, reserved)
        points = placement_support.window_checkpoints(
            start_at=start_at,
            end_at=end_at,
            reserved_requests=reserved,  # type: ignore[arg-type]
            normalize_datetime_fn=normalize_datetime,
        )
        assert points[0] == start_at
        assert set(points) <= set(brute_points)
        assert _active_sets(points, reserved) == brute_sets


def test_window_checkpoints_do_not_scale_with_window_length() -> None:
    start_at = datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    points = placement_support.window_checkpoints(
        start_at=start_at,
        end_at=start_at + timedelta(days=3650),
        reserved_requests=[
            SimpleNamespace(
                start_at=start_at + timedelta(days=10),
                end_at=start_at + timedelta(days=12),
            )
        ],
        normalize_datetime_fn=normalize_datetime,
    )
    assert len(points) <= 4


def _scheduled_request(**overrides):
    values = dict(
        resource_type="lxc",
        requested_mode="manual",
        ostemplate="local:vztmpl/x.tar.zst",
        template_id=None,
        cores=2,
        memory=2048,
        rootfs_size=8,
        disk_size=None,
        gpu_mapping_id=None,
        gpu_mdev_profile=None,
        username=None,
        password="strongpass123",
        mode="scheduled",
        start_at=None,
        end_at=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_create_rejects_overlong_window_before_placement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vm_request_service.quota_service, "check_quota", lambda *a, **k: None
    )

    def boom(**kwargs):
        raise AssertionError("window validation must not be reached")

    monkeypatch.setattr(
        vm_request_service.vm_request_availability_service,
        "validate_request_window",
        boom,
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(BadRequestError):
        vm_request_service.create(
            session=None,  # type: ignore[arg-type]
            request_in=_scheduled_request(
                start_at=start,
                end_at=start + timedelta(days=svc.MAX_REQUEST_WINDOW_DAYS + 30),
            ),
            user=SimpleNamespace(id=uuid.uuid4(), email="stu@campus.edu"),
        )




class _FakeSession:
    def __init__(self) -> None:
        self.rolled_back = False
        self.committed = False

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True


def test_auto_approved_immediate_request_returns_400_when_rebuild_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    def fake_create_vm_request(**kwargs):
        request = SimpleNamespace(
            id=uuid.uuid4(),
            start_at=kwargs["vm_request_in"].start_at,
            end_at=kwargs["vm_request_in"].end_at,
            request_kind=None,
            vmid=None,
        )
        created["request"] = request
        return request

    def fail_rebuild(**kwargs):
        raise ValueError("No feasible reservation exists for request 1234")

    monkeypatch.setattr(
        vm_request_service.quota_service, "check_quota", lambda *a, **k: None
    )
    monkeypatch.setattr(
        vm_request_service, "require_immediate_vm_request_access", lambda user: None
    )
    monkeypatch.setattr(
        vm_request_service, "can_auto_approve_vm_request", lambda user, mode: True
    )
    monkeypatch.setattr(
        vm_request_service.password_policy, "find_template", lambda *a, **k: None
    )
    monkeypatch.setattr(
        vm_request_service.password_policy,
        "resolve_login_password",
        lambda **kwargs: "pw",
    )
    monkeypatch.setattr(vm_request_service, "_encrypt_login_password", lambda pw: pw)
    monkeypatch.setattr(
        vm_request_service.vm_request_repo, "create_vm_request", fake_create_vm_request
    )
    monkeypatch.setattr(
        vm_request_service.vm_request_repo,
        "lock_overlapping_vm_requests_for_window",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        vm_request_service.vm_request_repo,
        "update_vm_request_status",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        vm_request_service.vm_request_placement_service,
        "rebuild_reserved_assignments",
        fail_rebuild,
    )

    session = _FakeSession()
    with pytest.raises(BadRequestError) as exc_info:
        vm_request_service.create(
            session=session,  # type: ignore[arg-type]
            request_in=_scheduled_request(mode="immediate"),
            user=SimpleNamespace(id=uuid.uuid4(), email="teacher@campus.edu"),
        )
    assert exc_info.value.message == t("vm_request.no_node_after_reservations")
    assert "1234" not in exc_info.value.message
    assert created["request"] is not None
    assert session.rolled_back is True
    assert session.committed is False




@pytest.mark.parametrize("value", ["../UTC", "/etc/localtime", "zone1970.tab", "America"])
def test_malformed_timezone_is_bad_request(value: str) -> None:
    with pytest.raises(BadRequestError):
        svc._resolve_timezone(value)




class _QuotaCaptured(Exception):
    pass


def _capture_quota_disk(monkeypatch: pytest.MonkeyPatch, request_in) -> int:
    captured: dict[str, int] = {}

    def capture(session, user_id, **kwargs):
        captured.update(kwargs)
        raise _QuotaCaptured

    monkeypatch.setattr(vm_request_service.quota_service, "check_quota", capture)
    with pytest.raises(_QuotaCaptured):
        vm_request_service.create(
            session=None,  # type: ignore[arg-type]
            request_in=request_in,
            user=SimpleNamespace(id=uuid.uuid4(), email="stu@campus.edu"),
        )
    return captured["delta_disk_gb"]


def test_create_quota_counts_rootfs_for_lxc(monkeypatch: pytest.MonkeyPatch) -> None:
    request_in = _scheduled_request(disk_size=1, rootfs_size=2000)
    assert _capture_quota_disk(monkeypatch, request_in) == 2000


def test_create_quota_counts_disk_default_for_vm(monkeypatch: pytest.MonkeyPatch) -> None:
    request_in = _scheduled_request(
        resource_type="vm", ostemplate=None, disk_size=None, rootfs_size=500
    )
    assert _capture_quota_disk(monkeypatch, request_in) == 20




def test_window_validation_error_is_localised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        lambda **kwargs: _infeasible_selection(),
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    with pytest.raises(BadRequestError) as exc_info:
        svc.validate_request_window(
            session=None,  # type: ignore[arg-type]
            current_user=None,
            request_in=_scheduled_request(start_at=start, end_at=start + timedelta(hours=2)),
        )
    assert exc_info.value.message == t("availability.no_node_for_window")


def test_window_assessment_does_not_forward_advisor_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        svc.vm_request_placement_service,
        "select_reserved_target_node_for_request",
        lambda **kwargs: _infeasible_selection(),
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    response = svc.assess_request_window(
        session=None,  # type: ignore[arg-type]
        current_user=None,
        request_in=VMRequestWindowAvailabilityRequest(
            start_at=start, end_at=start + timedelta(hours=2)
        ),
    )
    assert response.feasible is False
    assert response.reason == t("availability.window_scheduled_unavailable")
    assert response.summary == response.reason
    assert _ENGLISH_SUMMARY not in response.reason




_VM_AVAILABILITY_MESSAGE_KEYS = (
    "availability.window_too_long",
    "availability.start_too_far_in_past",
    "availability.window_quick_available",
    "availability.window_quick_unavailable",
    "availability.window_scheduled_available",
    "availability.window_scheduled_unavailable",
    "availability.window_long_warning",
)


@pytest.mark.parametrize("lang", ["zh-TW", "en", "ja"])
@pytest.mark.parametrize("key", _VM_AVAILABILITY_MESSAGE_KEYS)
def test_availability_keys_are_translated_in_every_language(
    key: str, lang: str
) -> None:
    # 直接讀該語言的 vm.json：translate() 缺 key 時會退回 zh-TW，無法抓到漏譯。
    vm_json = Path(app.__file__).resolve().parent / "locales" / lang / "vm.json"
    catalog = json.loads(vm_json.read_text(encoding="utf-8"))
    assert catalog.get(key), f"{key} missing from {lang}/vm.json"
    assert catalog[key] != key
    if key == "availability.window_too_long":
        assert "{days}" in catalog[key]
