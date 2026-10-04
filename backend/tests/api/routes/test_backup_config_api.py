"""備份相關設定的 API（真資料庫）：連線的 backup_storage、治理設定的備份上限。

驗證欄位真的一路寫進資料庫再讀回來（schema → route → repository → migration 欄位）。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.infrastructure.proxmox import invalidate_proxmox_client
from app.models.proxmox_connection import ProxmoxConnection

CONNECTIONS = f"{settings.API_V1_STR}/proxmox-config/connections"
GOVERNANCE = f"{settings.API_V1_STR}/governance/config"


def test_connection_backup_storage_round_trip(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    created = client.post(
        CONNECTIONS,
        headers=superuser_token_headers,
        json={
            "name": f"pytest-backup-{uuid.uuid4().hex[:8]}",
            "host": "proxmox.invalid",
            "user": "root@pam",
            "password": "not-a-real-password",
            "enabled": False,
            "backup_storage": " pbs-main ",
        },
    )
    assert created.status_code == 200, created.text
    conn_id = created.json()["id"]
    try:
        assert created.json()["backup_storage"] == "pbs-main"

        listed = client.get(CONNECTIONS, headers=superuser_token_headers).json()
        assert next(c for c in listed if c["id"] == conn_id)["backup_storage"] == "pbs-main"

        # 部分更新：沒帶到就不動
        untouched = client.put(
            f"{CONNECTIONS}/{conn_id}",
            headers=superuser_token_headers,
            json={"api_timeout": 45},
        )
        assert untouched.status_code == 200, untouched.text
        assert untouched.json()["backup_storage"] == "pbs-main"

        # 空字串＝清空，回到「這個叢集不開放備份」
        cleared = client.put(
            f"{CONNECTIONS}/{conn_id}",
            headers=superuser_token_headers,
            json={"backup_storage": ""},
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["backup_storage"] is None
    finally:
        db.expire_all()
        row = db.get(ProxmoxConnection, conn_id)
        if row is not None:
            db.delete(row)
            db.commit()
        invalidate_proxmox_client()


def test_connection_without_backup_storage_defaults_to_disabled(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    created = client.post(
        CONNECTIONS,
        headers=superuser_token_headers,
        json={
            "name": f"pytest-backup-{uuid.uuid4().hex[:8]}",
            "host": "proxmox.invalid",
            "user": "root@pam",
            "password": "not-a-real-password",
            "enabled": False,
        },
    )
    assert created.status_code == 200, created.text
    conn_id = created.json()["id"]
    try:
        assert created.json()["backup_storage"] is None
    finally:
        db.expire_all()
        row = db.get(ProxmoxConnection, conn_id)
        if row is not None:
            db.delete(row)
            db.commit()
        invalidate_proxmox_client()


def test_governance_student_backup_limit_round_trip(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    original = client.get(GOVERNANCE, headers=superuser_token_headers).json()
    assert 1 <= original["student_backup_max_count"] <= 10
    try:
        updated = client.put(
            GOVERNANCE,
            headers=superuser_token_headers,
            json={"student_backup_max_count": 4},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["student_backup_max_count"] == 4
        # 沒帶到的快照上限不受影響
        assert (
            updated.json()["student_snapshot_max_count"]
            == original["student_snapshot_max_count"]
        )

        for invalid in (0, 11):
            rejected = client.put(
                GOVERNANCE,
                headers=superuser_token_headers,
                json={"student_backup_max_count": invalid},
            )
            assert rejected.status_code == 422
    finally:
        client.put(
            GOVERNANCE,
            headers=superuser_token_headers,
            json={"student_backup_max_count": original["student_backup_max_count"]},
        )
