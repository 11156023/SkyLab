"""/policies/username 端點，以及申請 VM 時帳號違規回 422 且不碰 PVE。"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import settings
from app.services.proxmox import provisioning_service


def test_get_username_rules(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = client.get(
        f"{settings.API_V1_STR}/policies/username", headers=normal_user_token_headers
    )
    assert response.status_code == 200
    data = response.json()
    assert data["pattern"] == "^[a-z][a-z0-9_-]{0,31}$"
    assert data["max_length"] == 32
    assert "admin" in data["reserved"]
    assert "systemd-" in data["reserved_prefixes"]
    assert "skylab" in data["platform_reserved"]
    assert "ubuntu" in data["default_user_warn"]


def test_username_rules_require_login(client: TestClient) -> None:
    response = client.get(f"{settings.API_V1_STR}/policies/username")
    assert response.status_code == 401


def test_check_returns_all_violations(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/policies/username/check",
        headers=normal_user_token_headers,
        json={"username": "Skylab"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert [v["code"] for v in data["violations"]] == [
        "LNX_FORMAT",
        "PLATFORM_RESERVED",
    ]
    assert all(v["severity"] == "error" and v["message"] for v in data["violations"])


def test_check_warning_only_is_ok(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/policies/username/check",
        headers=normal_user_token_headers,
        json={"username": "ubuntu"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert [(v["code"], v["severity"]) for v in data["violations"]] == [
        ("LNX_DEFAULT_USER", "warning")
    ]


def test_vm_request_with_reserved_username_is_422_without_pve(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    monkeypatch: MonkeyPatch,
) -> None:
    pve = MagicMock()
    monkeypatch.setattr(provisioning_service, "proxmox_service", pve)
    start_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)

    response = client.post(
        f"{settings.API_V1_STR}/vm-requests/",
        headers=normal_user_token_headers,
        json={
            "reason": "Need a VM for the networking lab assignment.",
            "resource_type": "vm",
            "hostname": "net-lab-vm",
            "cores": 2,
            "memory": 2048,
            "password": "Strongpass123!",
            "template_id": 9000,
            "disk_size": 20,
            "username": "admin",
            "mode": "scheduled",
            "start_at": start_at.isoformat(),
            "end_at": (start_at + timedelta(hours=2)).isoformat(),
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(err["loc"][-1] == "username" for err in detail)
    assert pve.mock_calls == []
