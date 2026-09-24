"""desktop device-code 流程改用 ExpiringKV（REDIS_ENABLED=false 時是記憶體）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import desktop_client as routes
from app.core.config import settings as core_settings


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(
        routes, "_device_codes", routes.ExpiringKV("device_code_test", ttl_seconds=300)
    )


def _user():
    return SimpleNamespace(id=uuid.uuid4(), token_version=3)


def test_device_code_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import security

    monkeypatch.setattr(
        security, "create_access_token", lambda **kwargs: f"tok:{kwargs['subject']}"
    )
    created = routes.create_device_code()
    assert created.expires_in == routes._DEVICE_CODE_TTL

    assert routes.poll_device_code(created.device_code).status == "pending"

    user = _user()
    assert routes.approve_device_code(
        routes.DeviceApproveRequest(device_code=created.device_code), user, object()
    ) == {"status": "approved"}

    polled = routes.poll_device_code(created.device_code)
    assert polled.status == "approved"
    assert polled.access_token == f"tok:{user.id}"
    # 一次性：取走後 code 就失效
    with pytest.raises(HTTPException) as excinfo:
        routes.poll_device_code(created.device_code)
    assert excinfo.value.status_code == 404


def test_device_code_cannot_be_approved_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import security

    monkeypatch.setattr(security, "create_access_token", lambda **_: "tok")
    created = routes.create_device_code()
    body = routes.DeviceApproveRequest(device_code=created.device_code)
    routes.approve_device_code(body, _user(), object())

    with pytest.raises(HTTPException) as excinfo:
        routes.approve_device_code(body, _user(), object())
    assert excinfo.value.status_code == 409


def test_device_code_pending_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_DEVICE_CODE_MAX_PENDING", 2)
    routes.create_device_code()
    routes.create_device_code()

    with pytest.raises(HTTPException) as excinfo:
        routes.create_device_code()
    assert excinfo.value.status_code == 429
