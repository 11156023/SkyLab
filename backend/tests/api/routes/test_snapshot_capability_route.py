"""快照可用性路由測試：機器當下不能用快照時，查詢回不可用、所有快照寫入端點都拒絕。

以 dependency override + monkeypatch 隔離 DB 與 PVE：只驗證路由接線、回應格式
與「不可用時請求不會送到 PVE」。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps.auth import get_current_user
from app.api.deps.database import get_db
from app.api.deps.proxmox import get_resource_info_teaching
from app.api.routes import resource_details
from app.core.config import settings
from app.core.i18n import SUPPORTED_LANGUAGES, _catalog
from app.infrastructure.proxmox import operations
from app.main import app
from app.models import User, UserRole

BASE = f"{settings.API_V1_STR}/resources/105"


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    """不進入 lifespan 的 client（避免連 Redis / 啟動排程器）。"""
    yield TestClient(app)


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """PVE 替身：feature 回應可調；任何快照寫入一旦真的送出就記到 writes。"""
    state: dict[str, Any] = {"feature": True, "type": "qemu", "writes": []}

    def _has_feature(node: str, vmid: int, rtype: str) -> bool:
        if isinstance(state["feature"], Exception):
            raise state["feature"]
        return bool(state["feature"])

    def _record(name: str):
        def _call(*args: Any, **kwargs: Any) -> str:
            state["writes"].append(name)
            return "UPID:x"

        return _call

    monkeypatch.setattr(operations, "has_snapshot_feature", _has_feature)
    monkeypatch.setattr(operations, "list_snapshots", lambda *a, **k: [])
    for name in ("create_snapshot", "delete_snapshot", "rollback_snapshot"):
        monkeypatch.setattr(operations, name, _record(name))
    monkeypatch.setattr(
        resource_details, "require_resource_management", lambda **kwargs: None
    )

    admin = User(
        id=uuid.uuid4(),
        email="admin-snapshot-test@example.com",
        hashed_password="x",
        role=UserRole.admin,
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_resource_info_teaching] = lambda: {
        "vmid": 105,
        "node": "pve1",
        "type": state["type"],
    }
    yield state
    for dep in (get_current_user, get_db, get_resource_info_teaching):
        app.dependency_overrides.pop(dep, None)


@pytest.mark.parametrize("rtype", ["qemu", "lxc"])
def test_capability_available(
    api_client: TestClient, pve: dict[str, Any], rtype: str
) -> None:
    pve["type"] = rtype
    response = api_client.get(f"{BASE}/snapshot-capability")
    assert response.status_code == 200
    assert response.json() == {"available": True, "reason": None}


@pytest.mark.parametrize("rtype", ["qemu", "lxc"])
def test_capability_unsupported(
    api_client: TestClient, pve: dict[str, Any], rtype: str
) -> None:
    pve["type"] = rtype
    pve["feature"] = False
    response = api_client.get(f"{BASE}/snapshot-capability")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "unsupported"}


def test_capability_unknown_when_pve_check_fails(
    api_client: TestClient, pve: dict[str, Any]
) -> None:
    """查不到也回 200：前端據此隱藏功能，不是顯示錯誤。"""
    pve["feature"] = RuntimeError("storage 'data-nvme' does not exist")
    response = api_client.get(f"{BASE}/snapshot-capability")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "unknown"}


WRITE_CALLS = [
    ("post", "/snapshots", {"snapname": "before-upgrade"}),
    ("delete", "/snapshots/before-upgrade", None),
    ("post", "/snapshots/before-upgrade/rollback", None),
    ("post", "/reset-to-init", None),
    ("post", "/init-snapshot", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITE_CALLS)
def test_snapshot_writes_rejected_when_unsupported(
    api_client: TestClient,
    pve: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    pve["feature"] = False
    kwargs = {"json": body} if body is not None else {}
    response = api_client.request(method.upper(), f"{BASE}{path}", **kwargs)

    assert response.status_code == 409
    assert response.json()["detail"] == _catalog("zh-TW")["snapshot.unavailable"]
    assert pve["writes"] == []


@pytest.mark.parametrize(("method", "path", "body"), WRITE_CALLS)
def test_snapshot_writes_rejected_when_check_fails(
    api_client: TestClient,
    pve: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    pve["feature"] = RuntimeError("PVE down")
    kwargs = {"json": body} if body is not None else {}
    response = api_client.request(method.upper(), f"{BASE}{path}", **kwargs)

    assert response.status_code == 502
    assert response.json()["detail"] == _catalog("zh-TW")["snapshot.checkFailed"]
    assert pve["writes"] == []


@pytest.mark.parametrize("key", ["snapshot.unavailable", "snapshot.checkFailed"])
@pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGUAGES))
def test_snapshot_capability_messages_translated(lang: str, key: str) -> None:
    assert _catalog(lang).get(key), f"{lang} 缺少 {key}"
