"""平台路由層修正的回歸測試：

- 稽核紀錄／使用者清單的分頁參數有上下限（負值、過大都回 422）
- LDAP 設定改了 server_uri／bind_dn 卻沒重新輸入密碼時，不可沿用已存密碼
- 推播 endpoint 的主機名稱解析到內網位址時拒絕訂閱
- 刪除帳號後頭像檔一併移除，頭像端點回 404
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session

from app.api.routes import ldap_config as ldap_routes
from app.api.routes import push as push_routes
from app.core.config import settings
from app.core.security import encrypt_value
from app.exceptions import BadRequestError
from app.models import LdapConfig
from app.schemas.ldap import LdapConfigUpdate
from app.schemas.push import PushSubscriptionCreate
from app.services.user import avatar_service, ldap_auth_service
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR


# ── 分頁上下限 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    ["skip=-1", "limit=-1", "limit=0", "limit=100000"],
)
def test_user_list_rejects_out_of_range_pagination(
    client: TestClient, superuser_token_headers: dict[str, str], query: str
) -> None:
    r = client.get(f"{API}/users/?{query}", headers=superuser_token_headers)
    assert r.status_code == 422, r.text


def test_user_list_accepts_frontend_page_size(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{API}/users/?skip=0&limit=200", headers=superuser_token_headers)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(
    "path",
    ["/audit-logs/", "/audit-logs/my"],
)
@pytest.mark.parametrize(
    "query",
    ["skip=-1", "limit=-1", "limit=100000"],
)
def test_audit_log_lists_reject_out_of_range_pagination(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    path: str,
    query: str,
) -> None:
    r = client.get(f"{API}{path}?{query}", headers=superuser_token_headers)
    assert r.status_code == 422, r.text


def test_audit_log_list_accepts_normal_page(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{API}/audit-logs/my?skip=0&limit=50", headers=superuser_token_headers
    )
    assert r.status_code == 200, r.text


# ── LDAP bind 密碼不可被帶去新的伺服器 ────────────────────────────────


def _stored_ldap_config() -> LdapConfig:
    return LdapConfig(
        id=1,
        enabled=True,
        server_uri="ldaps://ldap.campus.example:636",
        bind_dn="cn=svc,dc=campus,dc=example",
        encrypted_bind_password=encrypt_value("stored-secret"),
    )


@pytest.fixture
def ldap_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {"bind": [], "update": []}
    stored = _stored_ldap_config()

    monkeypatch.setattr(
        ldap_auth_service, "get_ldap_config", lambda session: stored
    )

    def fake_update(*, session: Any, data: dict[str, Any]) -> LdapConfig:
        calls["update"].append(data)
        return stored

    monkeypatch.setattr(ldap_auth_service, "update_ldap_config", fake_update)
    monkeypatch.setattr(
        ldap_auth_service.audit_service, "log_action", lambda **kwargs: None
    )
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "test_bind",
        lambda config: calls["bind"].append(config),
    )
    return calls


_ADMIN = SimpleNamespace(id=uuid.uuid4())


@pytest.mark.parametrize(
    "override",
    [
        {"server_uri": "ldap://attacker.example:389"},
        {"bind_dn": "cn=other,dc=attacker,dc=example"},
    ],
)
def test_ldap_test_refuses_stored_password_for_new_target(
    ldap_calls: dict[str, Any], override: dict[str, str]
) -> None:
    result = ldap_routes.test_connection(
        session=object(), _=_ADMIN, config_in=LdapConfigUpdate(**override)
    )

    assert result.ok is False
    assert ldap_calls["bind"] == []


def test_ldap_test_allows_new_target_with_fresh_password(
    ldap_calls: dict[str, Any],
) -> None:
    result = ldap_routes.test_connection(
        session=object(),
        _=_ADMIN,
        config_in=LdapConfigUpdate(
            server_uri="ldaps://ldap2.campus.example:636", bind_password="new-secret"
        ),
    )

    assert result.ok is True
    assert len(ldap_calls["bind"]) == 1
    assert ldap_calls["bind"][0].server_uri == "ldaps://ldap2.campus.example:636"


def test_ldap_test_allows_unchanged_target_without_password(
    ldap_calls: dict[str, Any],
) -> None:
    # 前端每次都送整份表單：位址沒變時沿用已存密碼是正常行為
    result = ldap_routes.test_connection(
        session=object(),
        _=_ADMIN,
        config_in=LdapConfigUpdate(
            server_uri="ldaps://ldap.campus.example:636",
            bind_dn="cn=svc,dc=campus,dc=example",
            enabled=True,
        ),
    )

    assert result.ok is True
    assert len(ldap_calls["bind"]) == 1


def test_ldap_update_refuses_new_target_without_password(
    ldap_calls: dict[str, Any],
) -> None:
    with pytest.raises(BadRequestError):
        ldap_routes.update_config(
            session=object(),
            current_user=_ADMIN,
            config_in=LdapConfigUpdate(server_uri="ldap://attacker.example:389"),
        )
    assert ldap_calls["update"] == []


def test_ldap_update_allows_new_target_with_password(
    ldap_calls: dict[str, Any],
) -> None:
    ldap_routes.update_config(
        session=object(),
        current_user=_ADMIN,
        config_in=LdapConfigUpdate(
            server_uri="ldaps://ldap2.campus.example:636", bind_password="new-secret"
        ),
    )
    assert len(ldap_calls["update"]) == 1


# ── 推播 endpoint 解析到內網位址 ───────────────────────────────────────


def _fake_resolver(address: str) -> Any:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET

    def resolve(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    return resolve


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "192.168.100.20", "169.254.169.254", "100.64.0.1", "::1"],
)
def test_push_endpoint_resolving_to_internal_address_is_rejected(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    monkeypatch.setattr(push_routes.socket, "getaddrinfo", _fake_resolver(address))
    assert push_routes._resolves_to_internal_address("https://10.0.0.5.nip.io/x")


def test_push_endpoint_resolving_to_public_address_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(push_routes.socket, "getaddrinfo", _fake_resolver("142.250.72.10"))
    assert not push_routes._resolves_to_internal_address(
        "https://fcm.googleapis.com/fcm/send/abc"
    )


def test_push_subscribe_rejects_internal_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(push_routes.socket, "getaddrinfo", _fake_resolver("10.1.2.3"))
    saved: list[Any] = []
    monkeypatch.setattr(
        push_routes.push_repo, "upsert_subscription", lambda **kw: saved.append(kw)
    )
    body = PushSubscriptionCreate(
        # 用白名單內的網域才能走到路由層的 DNS 解析檢查（getaddrinfo 已換成回內網位址）
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        keys={"p256dh": "k", "auth": "a"},
    )

    with pytest.raises(BadRequestError):
        push_routes.save_subscription(
            session=object(), current_user=SimpleNamespace(id=uuid.uuid4()), body=body
        )
    assert saved == []


def test_push_subscribe_rejects_non_push_service_host_at_schema() -> None:
    with pytest.raises(ValidationError):
        PushSubscriptionCreate(
            endpoint="https://intranet-host.lab/x",
            keys={"p256dh": "k", "auth": "a"},
        )


# ── 刪除帳號後頭像一併移除 ─────────────────────────────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _new_user_headers(client: TestClient, superuser_headers: dict[str, str]) -> tuple[str, dict[str, str]]:
    email = random_email()
    password = random_lower_string()
    r = client.post(
        f"{API}/users/",
        headers=superuser_headers,
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    headers = user_authentication_headers(client=client, email=email, password=password)
    return r.json()["id"], headers


def _upload_avatar(client: TestClient, headers: dict[str, str]) -> None:
    r = client.post(
        f"{API}/users/me/avatar",
        headers=headers,
        files={"file": ("a.png", _PNG, "image/png")},
    )
    assert r.status_code == 200, r.text


def test_admin_delete_removes_avatar(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", tmp_path)
    user_id, headers = _new_user_headers(client, superuser_token_headers)
    _upload_avatar(client, headers)
    assert client.get(f"{API}/users/{user_id}/avatar").status_code == 200

    r = client.delete(f"{API}/users/{user_id}", headers=superuser_token_headers)
    assert r.status_code == 200, r.text

    assert list(tmp_path.glob(f"{user_id}.*")) == []
    assert client.get(f"{API}/users/{user_id}/avatar").status_code == 404


def test_self_delete_removes_avatar(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", tmp_path)
    user_id, headers = _new_user_headers(client, superuser_token_headers)
    _upload_avatar(client, headers)

    r = client.delete(f"{API}/users/me", headers=headers)
    assert r.status_code == 200, r.text

    assert list(tmp_path.glob(f"{user_id}.*")) == []
    assert client.get(f"{API}/users/{user_id}/avatar").status_code == 404


def test_orphaned_avatar_of_missing_user_is_not_served(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(avatar_service, "AVATAR_DIR", tmp_path)
    orphan_id = uuid.uuid4()
    (tmp_path / f"{orphan_id}.png").write_bytes(_PNG)

    assert client.get(f"{API}/users/{orphan_id}/avatar").status_code == 404
