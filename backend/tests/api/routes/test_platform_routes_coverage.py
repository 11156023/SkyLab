"""平台路由（push、audit_logs、desktop_client、monitoring、login、users、ldap_config、utils）的直接呼叫測試。"""

from __future__ import annotations

import mimetypes
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse

from app.api.routes import audit_logs as audit_route
from app.api.routes import desktop_client as desktop_route
from app.api.routes import ldap_config as ldap_route
from app.api.routes import login as login_route
from app.api.routes import monitoring as monitoring_route
from app.api.routes import push as push_route
from app.api.routes import users as users_route
from app.core import security
from app.core.config import settings
from app.exceptions import BadRequestError
from app.models import LdapConfig

_USER = SimpleNamespace(id=uuid.uuid4(), email="user@example.com")


# ─── push ────────────────────────────────────────────────────────────────────


def _fake_socket(result: Any) -> SimpleNamespace:
    def getaddrinfo(host: str, port: int, proto: int) -> Any:
        if isinstance(result, Exception):
            raise result
        return [(None, None, None, "", (address, port)) for address in result]

    return SimpleNamespace(getaddrinfo=getaddrinfo, IPPROTO_TCP=socket.IPPROTO_TCP)


@pytest.mark.parametrize(
    ("endpoint", "resolved", "expected"),
    [
        ("https:///no-host", ["8.8.8.8"], True),
        ("https://push.example.com/x", OSError("NXDOMAIN"), False),
        ("https://push.example.com/x", UnicodeError("bad idna"), False),
        ("https://push.example.com/x", ["not-an-ip"], True),
        ("https://push.example.com/x", ["10.0.0.5"], True),
        ("https://push.example.com/x", ["fe80::1%eth0"], True),
        ("https://push.example.com/x", ["8.8.8.8", "1.1.1.1"], False),
    ],
)
def test_push_endpoint_internal_address_detection(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, resolved: Any, expected: bool
) -> None:
    monkeypatch.setattr(push_route, "socket", _fake_socket(resolved))
    assert push_route._resolves_to_internal_address(endpoint) is expected


@pytest.mark.parametrize("available", [True, False])
def test_push_vapid_public_key(
    monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    service = MagicMock()
    service.is_available.return_value = available
    repo = MagicMock()
    repo.get_web_push_config.return_value = SimpleNamespace(vapid_public_key="BPk")
    monkeypatch.setattr(push_route, "web_push_service", service)
    monkeypatch.setattr(push_route, "push_repo", repo)

    result = push_route.get_vapid_public_key(session=MagicMock(), _=_USER)

    assert result.enabled is available
    assert result.public_key == ("BPk" if available else None)


def _subscription_body(endpoint: str = "https://fcm.googleapis.com/x") -> Any:
    return SimpleNamespace(
        endpoint=endpoint,
        keys=SimpleNamespace(p256dh="p", auth="a"),
        user_agent="UA",
        language=None,
    )


def test_push_save_subscription_upserts(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.normalize_language.return_value = "en"
    repo = MagicMock()
    subscription_id = uuid.uuid4()
    repo.upsert_subscription.return_value = SimpleNamespace(
        id=subscription_id, endpoint="https://fcm.googleapis.com/x", language="en"
    )
    monkeypatch.setattr(push_route, "_resolves_to_internal_address", lambda e: False)
    monkeypatch.setattr(push_route, "web_push_service", service)
    monkeypatch.setattr(push_route, "push_repo", repo)

    result = push_route.save_subscription(
        session=MagicMock(), current_user=_USER, body=_subscription_body()
    )

    assert result.id == subscription_id
    assert result.language == "en"
    kwargs = repo.upsert_subscription.call_args.kwargs
    assert kwargs["user_id"] == _USER.id
    assert kwargs["p256dh"] == "p"
    assert kwargs["language"] == "en"


@pytest.mark.parametrize("owned", [True, False])
def test_push_remove_subscription_only_deletes_own(
    monkeypatch: pytest.MonkeyPatch, owned: bool
) -> None:
    repo = MagicMock()
    subscription = SimpleNamespace(user_id=_USER.id if owned else uuid.uuid4())
    repo.get_subscription_by_endpoint.return_value = subscription
    monkeypatch.setattr(push_route, "push_repo", repo)

    response = push_route.remove_subscription(
        session=MagicMock(),
        current_user=_USER,
        body=SimpleNamespace(endpoint="https://fcm.googleapis.com/x"),
    )

    assert response.status_code == 204
    assert repo.delete_subscription.called is owned


def test_push_send_test(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.send_test.return_value = SimpleNamespace(sent=2, removed=1)
    monkeypatch.setattr(push_route, "web_push_service", service)

    result = push_route.send_test_push(session=MagicMock(), current_user=_USER)

    assert (result.sent, result.removed) == (2, 1)


# ─── audit_logs ──────────────────────────────────────────────────────────────


def test_audit_parse_user_id() -> None:
    assert audit_route._parse_user_id(None) is None
    assert audit_route._parse_user_id("") is None
    user_id = uuid.uuid4()
    assert audit_route._parse_user_id(str(user_id)) == user_id
    with pytest.raises(BadRequestError):
        audit_route._parse_user_id("not-a-uuid")


def test_audit_routes_delegate_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.export_csv_chunks.return_value = iter(["a,b\n"])
    monkeypatch.setattr(audit_route, "audit_service", service)
    session = MagicMock()
    user_id = uuid.uuid4()

    assert (
        audit_route.get_all_audit_logs(
            session=session,
            current_user=_USER,
            skip=0,
            limit=10,
            vmid=101,
            user_id=str(user_id),
            action=None,
            start_time=None,
            end_time=None,
            ip_address="1.2.3.4",
            search="x",
        )
        is service.get_all.return_value
    )
    assert service.get_all.call_args.kwargs["user_id"] == user_id

    assert (
        audit_route.get_audit_log_stats(
            session=session, current_user=_USER, start_time=None, end_time=None
        )
        is service.get_stats.return_value
    )
    assert (
        audit_route.list_audit_actions(current_user=_USER)
        is service.list_action_metas.return_value
    )
    assert (
        audit_route.list_audit_users(session=session, current_user=_USER)
        is service.list_audit_users.return_value
    )

    response = audit_route.export_audit_logs(
        session=session,
        current_user=_USER,
        vmid=None,
        user_id=None,
        action=None,
        start_time=None,
        end_time=None,
        ip_address=None,
        search=None,
        limit=50,
    )
    assert isinstance(response, StreamingResponse)
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="audit-logs-'
    )
    assert service.export_csv_chunks.call_args.kwargs["with_bom"] is True

    service.get_all.reset_mock()
    audit_route.get_resource_audit_logs(
        vmid=101,
        session=session,
        current_user=_USER,
        resource_info={},
        skip=5,
        limit=20,
    )
    service.get_all.assert_called_once_with(
        session=session, resource_vmid=101, skip=5, limit=20
    )


# ─── desktop_client ──────────────────────────────────────────────────────────


class _FakeKV:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.deleted: list[str] = []

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.data.pop(key, None)


def test_desktop_approve_unknown_code_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_route, "_device_codes", _FakeKV())
    with pytest.raises(HTTPException) as exc_info:
        desktop_route.approve_device_code(
            body=SimpleNamespace(device_code="missing"), current_user=_USER
        )
    assert exc_info.value.status_code == 404


def test_desktop_approve_expired_code_is_410(monkeypatch: pytest.MonkeyPatch) -> None:
    kv = _FakeKV()
    kv.data["old"] = {"token": None, "created_at": time.time() - 3600}
    monkeypatch.setattr(desktop_route, "_device_codes", kv)

    with pytest.raises(HTTPException) as exc_info:
        desktop_route.approve_device_code(
            body=SimpleNamespace(device_code="old"), current_user=_USER
        )

    assert exc_info.value.status_code == 410
    assert kv.deleted == ["old"]


def test_desktop_wireguard_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MagicMock()
    service.disconnect.return_value = True
    monkeypatch.setattr(desktop_route, "wireguard_service", service)
    session = MagicMock()

    assert (
        desktop_route.connect_wireguard(
            body=SimpleNamespace(device_id="dev", public_key="pk"),
            session=session,
            current_user=_USER,
        )
        is service.connect.return_value
    )
    assert (
        desktop_route.refresh_wireguard(
            body=SimpleNamespace(device_id="dev"), session=session, current_user=_USER
        )
        is service.refresh.return_value
    )
    result = desktop_route.disconnect_wireguard(
        body=SimpleNamespace(device_id="dev"), session=session, current_user=_USER
    )
    assert result.disconnected is True
    service.connect.assert_called_once_with(
        session=session, user_id=_USER.id, device_id="dev", public_key="pk"
    )


def test_desktop_download_missing_asset_is_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        desktop_route, "settings", SimpleNamespace(DESKTOP_CLIENT_DOWNLOAD_URL=None)
    )
    monkeypatch.setattr(desktop_route, "_STATIC_DIR", tmp_path / "no-static")

    with pytest.raises(HTTPException) as exc_info:
        desktop_route.download_desktop_client()

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("filename", ["client.zip", "client.AppImage"])
def test_desktop_download_serves_local_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, filename: str
) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / filename).write_bytes(b"installer")
    monkeypatch.setattr(
        desktop_route, "settings", SimpleNamespace(DESKTOP_CLIENT_DOWNLOAD_URL=None)
    )
    monkeypatch.setattr(desktop_route, "_STATIC_DIR", static_dir)

    response = desktop_route.download_desktop_client()

    assert isinstance(response, FileResponse)
    assert response.filename == filename
    # 對應表因平台而異（Windows 會回 x-zip-compressed），以標準庫結果為準
    expected = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    assert response.media_type == expected


# ─── monitoring ──────────────────────────────────────────────────────────────


class _PassthroughSchema:
    @staticmethod
    def model_validate(data: Any) -> Any:
        return ("validated", data)


async def test_monitoring_system_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        monitoring_route.system_health_service,
        "collect_system_health",
        lambda: {"status": "ok"},
    )
    monkeypatch.setattr(monitoring_route, "SystemHealth", _PassthroughSchema)

    assert await monitoring_route.get_system_health(_=_USER) == (
        "validated",
        {"status": "ok"},
    )


async def test_monitoring_overview_uses_redis_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = object()
    get_overview = AsyncMock(return_value="overview")
    monkeypatch.setattr(monitoring_route, "get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr(
        monitoring_route.monitoring_service, "get_overview_cached", get_overview
    )

    assert await monitoring_route.get_overview(_=_USER) == "overview"
    get_overview.assert_awaited_once_with(redis=redis)


def test_monitoring_rrd_and_alert_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monitoring_service = MagicMock()
    alert_service = MagicMock()
    monkeypatch.setattr(monitoring_route, "monitoring_service", monitoring_service)
    monkeypatch.setattr(monitoring_route, "alert_service", alert_service)
    session = MagicMock()
    alert_id = uuid.uuid4()

    assert (
        monitoring_route.get_node_rrd(node="pve1", _=_USER, timeframe="day")
        is monitoring_service.get_node_rrd.return_value
    )
    monitoring_service.get_node_rrd.assert_called_once_with("pve1", "day")
    assert (
        monitoring_route.get_vm_rrd(
            vmid=101, session=session, current_user=_USER, timeframe="week"
        )
        is monitoring_service.get_vm_rrd.return_value
    )
    assert (
        monitoring_route.acknowledge_alert(
            alert_id=alert_id, session=session, current_user=_USER
        )
        is alert_service.acknowledge_alert.return_value
    )
    alert_service.acknowledge_alert.assert_called_once_with(
        session, alert_id=alert_id, user_id=_USER.id
    )


# ─── login ───────────────────────────────────────────────────────────────────


def test_totp_claims_reject_non_totp_token() -> None:
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "type": "access",
            "jti": "jti-1",
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        },
        settings.SECRET_KEY,
        algorithm=security.ALGORITHM,
    )
    assert login_route._totp_challenge_claims(token) is None


async def test_clear_totp_failures_logs_redis_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = MagicMock()
    redis.delete = AsyncMock(side_effect=ConnectionError("redis gone"))

    await login_route._clear_totp_failures(redis, "user-1")

    redis.delete.assert_awaited_once()
    assert "Failed to reset TOTP failure counter" in caplog.text


async def test_login_totp_account_limit_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = login_route._TotpChallengeClaims(user_id="u1", jti="j1", exp=9999999999)
    check = AsyncMock(side_effect=[(True, {"current": 1}), (False, {})])
    complete = MagicMock()
    monkeypatch.setattr(login_route, "_totp_challenge_claims", lambda token: claims)
    monkeypatch.setattr(login_route, "get_redis", AsyncMock(return_value=None))
    monkeypatch.setattr(login_route, "is_jti_revoked", AsyncMock(return_value=False))
    monkeypatch.setattr(
        login_route, "peek_rate_limit_by_key", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(login_route, "check_rate_limit_by_key", check)
    monkeypatch.setattr(login_route.totp_service, "complete_login", complete)

    with pytest.raises(HTTPException) as exc_info:
        await login_route.login_totp(
            session=MagicMock(),
            body=SimpleNamespace(totp_token="challenge", code="123456"),
        )

    assert exc_info.value.status_code == 429
    assert check.await_count == 2
    complete.assert_not_called()


async def test_refresh_token_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    refresh = AsyncMock(return_value="new-token")
    monkeypatch.setattr(login_route.auth_service, "refresh_access_token", refresh)
    session = MagicMock()

    result = await login_route.refresh_token(
        session=session, body=SimpleNamespace(refresh_token="rt")
    )

    assert result == "new-token"
    refresh.assert_awaited_once_with(session=session, refresh_token="rt")


@pytest.mark.parametrize("with_body", [True, False])
async def test_logout_revokes_tokens_and_clears_grafana_cookie(
    monkeypatch: pytest.MonkeyPatch, with_body: bool
) -> None:
    logout = AsyncMock()
    monkeypatch.setattr(login_route.auth_service, "logout", logout)
    response = Response()
    body = SimpleNamespace(refresh_token="rt") if with_body else None

    result = await login_route.logout(
        token="access", current_user=_USER, response=response, body=body
    )

    assert result.message == "Logged out"
    logout.assert_awaited_once_with("access", "rt" if with_body else None)
    cookie = response.headers["set-cookie"]
    assert login_route.grafana_service.SESSION_COOKIE in cookie
    assert "Max-Age=0" in cookie


# ─── users ───────────────────────────────────────────────────────────────────


def test_user_registration_routes_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_service = MagicMock()
    user_service = MagicMock()
    monkeypatch.setattr(users_route, "registration_policy_service", policy_service)
    monkeypatch.setattr(users_route, "user_service", user_service)
    session = MagicMock()
    user_id = uuid.uuid4()

    assert (
        users_route.read_registration_policy(session=session)
        is policy_service.get_policy.return_value
    )
    assert (
        users_route.write_registration_policy(
            session=session,
            body=SimpleNamespace(
                signup_enabled=True,
                approval_required=False,
                google_signup_enabled=True,
                student_email_domain="school.edu.tw",
            ),
        )
        is policy_service.update_policy.return_value
    )
    policy_service.update_policy.assert_called_once_with(
        session,
        signup_enabled=True,
        approval_required=False,
        google_signup_enabled=True,
        student_email_domain="school.edu.tw",
    )
    assert (
        users_route.read_pending_registrations(session=session)
        is user_service.list_pending_registrations.return_value
    )
    users_route.approve_registration(
        user_id=user_id, session=session, current_user=_USER
    )
    users_route.reject_registration(
        user_id=user_id, session=session, current_user=_USER
    )
    approvals = [
        call.kwargs["approve"]
        for call in user_service.review_registration.call_args_list
    ]
    assert approvals == [True, False]


# ─── ldap_config / utils ─────────────────────────────────────────────────────


def test_ldap_get_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LdapConfig(
        enabled=True,
        server_uri="ldaps://ldap.example.edu",
        encrypted_bind_password="cipher",
    )
    monkeypatch.setattr(
        ldap_route.ldap_config_repo, "get_ldap_config", lambda session: config
    )

    result = ldap_route.get_config(session=MagicMock(), _=_USER)

    assert result.enabled is True
    assert result.server_uri == "ldaps://ldap.example.edu"
    assert result.bind_password_set is True
