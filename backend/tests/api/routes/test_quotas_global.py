"""全域預設配額 API 測試。

以 dependency override + monkeypatch 隔離 DB：這些案例只驗證路由、schema
約束與權限，不需要真實資料庫。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps.auth import get_current_active_superuser
from app.api.deps.database import get_db
from app.core.config import settings
from app.main import app
from app.models import QuotaConfig, User, UserRole
from app.services.resource import quota_service
from app.services.user import audit_service

BASE = f"{settings.API_V1_STR}/quotas/global"


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    """不進入 lifespan 的 client（避免連 Redis / 啟動排程器）。"""
    yield TestClient(app)


@pytest.fixture()
def as_admin() -> Iterator[None]:
    admin = User(
        id=uuid.uuid4(),
        email="admin-quota-test@example.com",
        hashed_password="x",
        role=UserRole.admin,
    )
    app.dependency_overrides[get_current_active_superuser] = lambda: admin
    app.dependency_overrides[get_db] = lambda: None
    yield
    app.dependency_overrides.pop(get_current_active_superuser, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_service, "log_action", lambda **kwargs: None)


def _config(**overrides: Any) -> QuotaConfig:
    values: dict[str, Any] = {
        "id": 1,
        "max_cpu_cores": 8,
        "max_memory_mb": 16384,
        "max_disk_gb": 100,
        "max_instances": 5,
    }
    values.update(overrides)
    return QuotaConfig(**values)


def test_get_returns_current_global_quota(
    api_client: TestClient, as_admin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        quota_service, "get_global_quota", lambda session: _config(max_cpu_cores=16)
    )

    response = api_client.get(BASE)

    assert response.status_code == 200
    body = response.json()
    assert body["max_cpu_cores"] == 16
    assert body["max_instances"] == 5
    assert "updated_at" in body


def test_put_applies_partial_update(
    api_client: TestClient, as_admin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PUT /quotas/global 必須走到全域端點，而不是被 /{quota_id} 當成 UUID。"""
    seen: dict[str, Any] = {}

    def _fake_update(session: Any, data: dict[str, Any]) -> QuotaConfig:
        seen.update(data)
        return _config(**data)

    monkeypatch.setattr(quota_service, "update_global_quota", _fake_update)

    response = api_client.put(BASE, json={"max_cpu_cores": 32})

    assert response.status_code == 200
    assert seen == {"max_cpu_cores": 32}
    assert response.json()["max_cpu_cores"] == 32


def test_put_accepts_zero_as_unlimited(
    api_client: TestClient, as_admin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 = 無限制，是合法值。"""
    monkeypatch.setattr(
        quota_service,
        "update_global_quota",
        lambda session, data: _config(**data),
    )

    response = api_client.put(BASE, json={"max_cpu_cores": 0})

    assert response.status_code == 200
    assert response.json()["max_cpu_cores"] == 0


def test_put_rejects_out_of_range_value(
    api_client: TestClient, as_admin: None
) -> None:
    response = api_client.put(BASE, json={"max_cpu_cores": -1})

    assert response.status_code == 422
    # 必須是 body 欄位的範圍錯誤；若路由被 /{quota_id} 吃掉，422 會來自
    # path 參數的 UUID 解析失敗，那是完全不同的原因。
    locations = [
        loc for item in response.json()["detail"] for loc in item.get("loc", [])
    ]
    assert "max_cpu_cores" in locations
    assert "quota_id" not in locations


def test_get_requires_authentication(api_client: TestClient) -> None:
    assert api_client.get(BASE).status_code == 401
