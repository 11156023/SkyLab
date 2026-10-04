"""閘道頁「平台入口」API（/gateway/platform-entry*）：權限與路由接線。

業務規則在 tests/services/test_platform_entry_service.py；這裡只確認管理員才進得來、
錯誤會轉成對應的 HTTP 狀態，以及狀態端點有把後端看到的來源 IP 帶回去。
"""

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.schemas.gateway import PlatformEntryStatus

API = f"{settings.API_V1_STR}/gateway/platform-entry"


def test_platform_entry_requires_admin(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    assert client.get(API).status_code == 401
    assert client.get(API, headers=normal_user_token_headers).status_code == 403
    assert (
        client.put(API, json={"enabled": False}, headers=normal_user_token_headers).status_code
        == 403
    )
    assert client.get(f"{API}/status", headers=normal_user_token_headers).status_code == 403


def test_platform_entry_get_returns_config_shape(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(API, headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {
        "enabled",
        "domain",
        "upstream_host",
        "upstream_port",
        "enable_https",
        "updated_at",
        "gateway_ready",
        "cloudflare_ready",
        "gateway_host",
    }


def test_platform_entry_put_rejects_invalid_domain(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.put(
        API,
        json={"enabled": True, "domain": "not a domain", "upstream_host": "10.0.0.5"},
        headers=superuser_token_headers,
    )
    assert r.status_code == 400, r.text


def test_platform_entry_status_passes_observed_client(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    seen: dict[str, object] = {}

    def fake_status(_session: object, **kwargs: object) -> PlatformEntryStatus:
        seen.update(kwargs)
        return PlatformEntryStatus(applied=True, checked_at=datetime.now(timezone.utc))

    with patch(
        "app.services.network.platform_entry_service.get_status", side_effect=fake_status
    ):
        r = client.get(
            f"{API}/status",
            headers={
                **superuser_token_headers,
                "X-Real-IP": "203.0.113.7",
                "X-Forwarded-Proto": "https",
            },
        )
    assert r.status_code == 200, r.text
    assert seen == {"observed_client_ip": "203.0.113.7", "observed_scheme": "https"}
