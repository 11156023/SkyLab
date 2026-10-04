"""延長到期日申請：日期驗證與核准即生效。

「目前到期」是 ``resources.expiry_date``（TTL）與核准使用時段 ``vm_requests.end_at``
取較早者；自己申請的機器通常只有後者，延期要同時把時段迄往後推。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.models import SpecChangeType
from app.schemas.spec_change_request import SpecChangeRequestCreate
from app.services.vm import spec_change_service as scs


class _Session:
    def __init__(self, resource):
        self._resource = resource
        self.added = []

    def get(self, model, key):
        return self._resource

    def add(self, obj):
        self.added.append(obj)


def _request_in(requested: date | None) -> SpecChangeRequestCreate:
    return SpecChangeRequestCreate(
        vmid=150,
        change_type=SpecChangeType.expiry,
        reason="期末專題需要多跑兩週",
        requested_expiry_date=requested,
    )


def _window(end_date: date, *, gpu: str | None = None) -> SimpleNamespace:
    """核准使用時段的申請：end_at 落在 end_date 當天（模擬表單的 end-of-day）。"""
    return SimpleNamespace(
        id=uuid.uuid4(),
        vmid=150,
        gpu_mapping_id=gpu,
        start_at=datetime.now(timezone.utc) - timedelta(days=30),
        end_at=datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(hours=15, minutes=59, seconds=59),
    )


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch) -> dict[str, SimpleNamespace | None]:
    """預設沒有核准時段；測試把 ``holder["request"]`` 換掉就能模擬有時段的機器。"""
    holder: dict[str, SimpleNamespace | None] = {"request": None}
    monkeypatch.setattr(
        scs.vm_request_repo,
        "get_latest_approved_vm_request_by_vmid",
        lambda *, session, vmid: holder["request"],
    )
    monkeypatch.setattr(
        scs.vm_request_repo,
        "get_unprovisioned_gpu_requests_overlapping_window",
        lambda **kwargs: [],
    )
    return holder


@pytest.fixture()
def resource(monkeypatch: pytest.MonkeyPatch, window) -> SimpleNamespace:
    res = SimpleNamespace(
        vmid=150,
        expiry_date=date.today() + timedelta(days=10),
        expiry_notified_at="notified",
        scheduled_deletion_at="scheduled",
        auto_stop_at="stop-at",
        auto_stop_reason="ttl_expired",
    )
    monkeypatch.setattr(
        scs.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: res
    )
    return res


def test_expiry_requires_date(resource: SimpleNamespace) -> None:
    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(None)
        )


def test_expiry_must_be_after_current(resource: SimpleNamespace) -> None:
    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(resource.expiry_date)
        )


def test_expiry_must_be_future_and_within_limit(resource: SimpleNamespace) -> None:
    resource.expiry_date = None
    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(date.today())
        )
    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None,
            vmid=150,
            request_in=_request_in(
                date.today() + timedelta(days=scs.EXPIRY_MAX_EXTENSION_DAYS + 1)
            ),
        )


def test_valid_expiry_returns_current(resource: SimpleNamespace) -> None:
    current = scs._validate_expiry_request(
        session=None,
        vmid=150,
        request_in=_request_in(resource.expiry_date + timedelta(days=30)),
    )
    assert current == resource.expiry_date


def test_apply_extension_resets_ttl_state(resource: SimpleNamespace) -> None:
    session = _Session(resource)
    new_date = date.today() + timedelta(days=60)
    db_request = SimpleNamespace(
        resource_vmid=150,
        requested_expiry_date=new_date,
        applied_at=None,
        apply_error="old",
    )

    scs._apply_expiry_extension(session, db_request)

    assert resource.expiry_date == new_date
    assert resource.expiry_notified_at is None
    assert resource.scheduled_deletion_at is None
    assert resource.auto_stop_at is None
    assert resource.auto_stop_reason is None
    assert db_request.applied_at is not None
    assert db_request.apply_error is None


def test_apply_extension_without_resource_fails() -> None:
    session = _Session(None)
    db_request = SimpleNamespace(
        resource_vmid=150, requested_expiry_date=date.today(), applied_at=None
    )
    with pytest.raises(BadRequestError):
        scs._apply_expiry_extension(session, db_request)


# ---------------------------------------------------------------------------
# 核准使用時段（自己申請的機器沒有 expiry_date，過期＝時段結束）
# ---------------------------------------------------------------------------


def test_window_only_machine_uses_window_end_as_current(
    resource: SimpleNamespace, window
) -> None:
    resource.expiry_date = None
    ended = date.today() - timedelta(days=3)
    window["request"] = _window(ended)

    # 已結束的時段仍可延，但要比時段迄日晚；比今天早的一律擋
    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(ended)
        )
    current = scs._validate_expiry_request(
        session=None,
        vmid=150,
        request_in=_request_in(date.today() + timedelta(days=14)),
    )
    assert current == ended


def test_effective_expiry_is_the_earlier_of_the_two(
    resource: SimpleNamespace, window
) -> None:
    window_end = date.today() + timedelta(days=5)
    window["request"] = _window(window_end)
    assert window_end < resource.expiry_date

    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(window_end)
        )
    current = scs._validate_expiry_request(
        session=None,
        vmid=150,
        request_in=_request_in(window_end + timedelta(days=1)),
    )
    assert current == window_end


def test_apply_extends_window_without_adding_expiry_date(
    resource: SimpleNamespace, window
) -> None:
    resource.expiry_date = None
    resource.auto_stop_reason = None
    resource.auto_stop_at = None
    request = _window(date.today() - timedelta(days=3))
    window["request"] = request
    session = _Session(resource)
    new_date = date.today() + timedelta(days=30)
    db_request = SimpleNamespace(
        resource_vmid=150, requested_expiry_date=new_date, applied_at=None, apply_error=None
    )

    scs._apply_expiry_extension(session, db_request)

    # 時段迄推到申請日期的最後一刻；不補 expiry_date（免得被拉進 TTL 刪除流程）
    assert request.end_at.date() == new_date
    assert request.end_at.hour == 23 and request.end_at.minute == 59
    assert resource.expiry_date is None
    assert request in session.added
    assert db_request.applied_at is not None


def test_apply_moves_both_clocks_but_never_backwards(
    resource: SimpleNamespace, window
) -> None:
    window_end = date.today() + timedelta(days=5)
    request = _window(window_end)
    window["request"] = request
    session = _Session(resource)

    # 申請日期介於兩者之間：只有較早的時段迄往後推，到期日不能被縮短
    between = resource.expiry_date - timedelta(days=2)
    original_expiry = resource.expiry_date
    scs._apply_expiry_extension(
        session,
        SimpleNamespace(resource_vmid=150, requested_expiry_date=between, applied_at=None, apply_error=None),
    )
    assert request.end_at.date() == between
    assert resource.expiry_date == original_expiry

    # 申請日期晚於兩者：兩個都推
    later = original_expiry + timedelta(days=20)
    scs._apply_expiry_extension(
        session,
        SimpleNamespace(resource_vmid=150, requested_expiry_date=later, applied_at=None, apply_error=None),
    )
    assert request.end_at.date() == later
    assert resource.expiry_date == later


def test_gpu_window_conflict_blocks_validation_and_apply(
    resource: SimpleNamespace, window, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource.expiry_date = None
    request = _window(date.today() - timedelta(days=1), gpu="gpu-h200-0")
    window["request"] = request
    other = _window(date.today() + timedelta(days=40))
    captured: dict[str, object] = {}

    def fake_overlap(**kwargs):
        captured.update(kwargs)
        return [other]

    monkeypatch.setattr(
        scs.vm_request_repo,
        "get_unprovisioned_gpu_requests_overlapping_window",
        fake_overlap,
    )
    new_date = date.today() + timedelta(days=30)

    with pytest.raises(BadRequestError):
        scs._validate_expiry_request(
            session=None, vmid=150, request_in=_request_in(new_date)
        )
    assert captured["gpu_mapping_id"] == "gpu-h200-0"
    assert captured["exclude_request_id"] == request.id
    assert captured["window_start"] == request.end_at

    with pytest.raises(BadRequestError):
        scs._apply_expiry_extension(
            _Session(resource),
            SimpleNamespace(resource_vmid=150, requested_expiry_date=new_date, applied_at=None, apply_error=None),
        )
    # 衝突時什麼都不該動
    assert request.end_at.date() == date.today() - timedelta(days=1)


def test_non_gpu_window_skips_gpu_check(
    resource: SimpleNamespace, window, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource.expiry_date = None
    window["request"] = _window(date.today() - timedelta(days=1))

    def boom(**kwargs):  # pragma: no cover - 被呼叫就是錯
        raise AssertionError("non-GPU machines must not query GPU reservations")

    monkeypatch.setattr(
        scs.vm_request_repo, "get_unprovisioned_gpu_requests_overlapping_window", boom
    )
    scs._validate_expiry_request(
        session=None,
        vmid=150,
        request_in=_request_in(date.today() + timedelta(days=7)),
    )
