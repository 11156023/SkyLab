"""延長到期日申請：日期驗證與核准即生效。"""

from __future__ import annotations

from datetime import date, timedelta
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


@pytest.fixture()
def resource(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
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
