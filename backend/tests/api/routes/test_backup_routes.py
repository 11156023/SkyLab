"""備份路由測試：接線、回應格式、輸入驗證，以及「只認這台機器的 SkyLab 備份」。

以 dependency override + monkeypatch 隔離 DB、佇列與 PVE。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from types import SimpleNamespace
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
from app.services.resource import backup_service

BASE = f"{settings.API_V1_STR}/resources/105"
CREATED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)
TOKEN = int(CREATED_AT.timestamp())
OWN = "pbs-main:backup/ct/105/2026-09-20T00:00:00Z"
INSTITUTIONAL = "pbs-main:backup/ct/105/2026-09-15T04:00:00Z"


class _Session:
    def exec(self, stmt: Any) -> Any:
        return SimpleNamespace(all=lambda: [])

    def add(self, obj: Any) -> None:
        """測試替身。"""

    def commit(self) -> None:
        """測試替身。"""

    def rollback(self) -> None:
        """測試替身。"""


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    """不進入 lifespan 的 client（避免連 Redis / 啟動排程器）。"""
    yield TestClient(app)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"storage": "pbs-main", "enqueued": [], "deleted": []}

    def _enqueue(**kwargs: Any) -> Any:
        state["enqueued"].append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(
        backup_service,
        "get_proxmox_settings_for_node",
        lambda node: SimpleNamespace(backup_storage=state["storage"]),
    )
    monkeypatch.setattr(operations, "storage_accepts_backups", lambda node, storage: True)
    monkeypatch.setattr(operations, "has_snapshot_feature", lambda node, vmid, rtype: False)
    monkeypatch.setattr(
        operations,
        "list_backups",
        lambda node, storage, vmid: [
            {"volid": OWN, "ctime": 200, "size": 4096, "notes": f"skylab-backup:{TOKEN} | 升級前"},
            {"volid": INSTITUTIONAL, "ctime": 150, "size": 8192, "notes": "code-server"},
        ],
    )
    monkeypatch.setattr(
        operations,
        "delete_backup",
        lambda node, storage, volid, **kw: state["deleted"].append(volid),
    )
    monkeypatch.setattr(
        backup_service.resource_repo,
        "get_resource_by_vmid",
        lambda **kw: SimpleNamespace(vmid=kw["vmid"], created_at=CREATED_AT),
    )
    monkeypatch.setattr(backup_service, "enqueue_task_sync", _enqueue)
    monkeypatch.setattr(backup_service.audit_service, "log_action", lambda **kw: None)
    monkeypatch.setattr(
        resource_details, "require_resource_management", lambda **kwargs: None
    )

    admin = User(
        id=uuid.uuid4(),
        email="admin-backup-test@example.com",
        hashed_password="x",
        role=UserRole.admin,
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[get_db] = lambda: _Session()
    app.dependency_overrides[get_resource_info_teaching] = lambda: {
        "vmid": 105,
        "node": "pve1",
        "type": "lxc",
    }
    yield state
    for dep in (get_current_user, get_db, get_resource_info_teaching):
        app.dependency_overrides.pop(dep, None)


def test_capability_available(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.get(f"{BASE}/backup-capability")
    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "reason": None,
        "requires_shutdown": True,
        "max_count": None,
    }


def test_capability_not_configured_is_200_not_an_error(
    api_client: TestClient, env: dict[str, Any]
) -> None:
    env["storage"] = None
    response = api_client.get(f"{BASE}/backup-capability")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["reason"] == "not_configured"


def test_list_hides_institutional_backups(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.get(f"{BASE}/backups")
    assert response.status_code == 200
    assert response.json() == [
        {"volid": OWN, "created_at": 200, "size": 4096, "description": "升級前"}
    ]


def test_create_returns_202_with_task_id(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.post(f"{BASE}/backups", json={"description": "升級前"})
    assert response.status_code == 202
    assert uuid.UUID(response.json()["task_id"])
    assert env["enqueued"][0]["task_type"] == backup_service.TASK_BACKUP


def test_create_rejects_overlong_description(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.post(f"{BASE}/backups", json={"description": "x" * 121})
    assert response.status_code == 422
    assert env["enqueued"] == []


def test_create_rejected_when_not_configured(api_client: TestClient, env: dict[str, Any]) -> None:
    env["storage"] = None
    response = api_client.post(f"{BASE}/backups", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == _catalog("zh-TW")["backup.not_configured"]


def test_restore_returns_202_for_own_backup(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.post(f"{BASE}/backups/restore", json={"volid": OWN})
    assert response.status_code == 202
    assert env["enqueued"][0]["payload"]["volid"] == OWN


def test_restore_refuses_institutional_backup(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.post(f"{BASE}/backups/restore", json={"volid": INSTITUTIONAL})
    assert response.status_code == 404
    assert response.json()["detail"] == _catalog("zh-TW")["backup.not_found"]
    assert env["enqueued"] == []


def test_delete_takes_volid_from_query(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.delete(f"{BASE}/backups", params={"volid": OWN})
    assert response.status_code == 200
    assert env["deleted"] == [OWN]


def test_delete_refuses_institutional_backup(api_client: TestClient, env: dict[str, Any]) -> None:
    response = api_client.delete(f"{BASE}/backups", params={"volid": INSTITUTIONAL})
    assert response.status_code == 404
    assert env["deleted"] == []


def test_delete_requires_volid(api_client: TestClient, env: dict[str, Any]) -> None:
    assert api_client.delete(f"{BASE}/backups").status_code == 422


BACKUP_MESSAGE_KEYS = [
    "backup.not_configured",
    "backup.storage_unavailable",
    "backup.max_count_reached",
    "backup.not_found",
    "backup.resource_not_tracked",
    "backup.task_running",
]


@pytest.mark.parametrize("key", BACKUP_MESSAGE_KEYS)
@pytest.mark.parametrize("lang", sorted(SUPPORTED_LANGUAGES))
def test_backup_messages_translated(lang: str, key: str) -> None:
    assert _catalog(lang).get(key), f"{lang} 缺少 {key}"
