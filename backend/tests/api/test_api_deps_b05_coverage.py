"""Coverage for app.api deps (proxmox, ai_api_key, auth, rate_limit), ai_capacity and request_body."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, WebSocketException
from pydantic import BaseModel

from app.api import ai_capacity, request_body
from app.api.deps import ai_api_key, auth, rate_limit
from app.api.deps import proxmox as proxmox_deps
from app.exceptions import AuthenticationError
from app.models import get_datetime_utc

# --- deps/proxmox -------------------------------------------------------------


@pytest.fixture
def proxmox_calls(monkeypatch):
    calls: list[tuple[str, int]] = []

    def access(name):
        def _fn(*, session, user, vmid):
            calls.append((name, vmid))

        return _fn

    for name in (
        "require_resource_ownership",
        "require_resource_use",
        "require_resource_console_access",
        "require_resource_management",
    ):
        monkeypatch.setattr(proxmox_deps, name, access(name))
    monkeypatch.setattr(
        proxmox_deps.proxmox_service,
        "find_resource",
        lambda vmid: {"vmid": vmid, "kind": "resource"},
    )
    monkeypatch.setattr(
        proxmox_deps.proxmox_service,
        "find_lxc",
        lambda vmid: {"vmid": vmid, "kind": "lxc"},
    )
    return calls


def test_proxmox_resource_deps(proxmox_calls, monkeypatch) -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    session = object()

    assert proxmox_deps.get_resource_info(101, user, session)["kind"] == "resource"
    assert proxmox_deps.get_resource_info_controllable(102, user, session)["vmid"] == (
        102
    )
    assert proxmox_deps.get_vm_info_controllable(103, user, session)["vmid"] == 103
    assert proxmox_deps.get_lxc_info_controllable(104, user, session)["kind"] == "lxc"
    assert proxmox_deps.check_firewall_access(105, user, session) is None

    from app.services.teaching import access as teaching_access

    teaching_calls: list[int] = []
    monkeypatch.setattr(
        teaching_access,
        "require_vm_teaching_access",
        lambda _session, _user, vmid: teaching_calls.append(vmid),
    )
    assert proxmox_deps.get_resource_info_teaching(106, user, session)["vmid"] == 106
    assert teaching_calls == [106]
    assert proxmox_calls == [
        ("require_resource_ownership", 101),
        ("require_resource_use", 102),
        ("require_resource_console_access", 103),
        ("require_resource_console_access", 104),
        ("require_resource_management", 105),
    ]


# --- deps/ai_api_key ----------------------------------------------------------


class _FakeSession:
    def __init__(self, candidates, user=None) -> None:
        self.candidates = candidates
        self.user = user

    def exec(self, _statement):
        return SimpleNamespace(all=lambda: list(self.candidates))

    def get(self, _model, _id):
        return self.user


def _credential(**overrides):
    fields = {
        "id": uuid.uuid4(),
        "api_key_encrypted": "enc",
        "expires_at": None,
        "user_id": uuid.uuid4(),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_ai_api_key_rejects_non_bearer_and_empty() -> None:
    with pytest.raises(HTTPException) as caught:
        ai_api_key.get_current_user_by_ai_api_key(_FakeSession([]), "Token abc")
    assert caught.value.status_code == 401
    with pytest.raises(HTTPException) as caught:
        ai_api_key.get_current_user_by_ai_api_key(_FakeSession([]), "Bearer    ")
    assert caught.value.status_code == 401


def test_ai_api_key_skips_undecryptable_candidates(monkeypatch, caplog) -> None:
    def _decrypt(_value):
        raise ValueError("bad key")

    monkeypatch.setattr(ai_api_key, "decrypt_value", _decrypt)
    with pytest.raises(HTTPException) as caught:
        ai_api_key.get_current_user_by_ai_api_key(
            _FakeSession([_credential()]), "Bearer ccai_secret"
        )
    assert caught.value.status_code == 401
    assert "Failed to decrypt AI API credential" in caplog.text


def test_ai_api_key_user_missing_or_inactive(monkeypatch) -> None:
    monkeypatch.setattr(ai_api_key, "decrypt_value", lambda _value: "ccai_secret")
    credential = _credential(expires_at=get_datetime_utc() + timedelta(days=1))

    with pytest.raises(HTTPException) as caught:
        ai_api_key.get_current_user_by_ai_api_key(
            _FakeSession([credential], user=None), "Bearer ccai_secret"
        )
    assert caught.value.status_code == 401

    inactive = SimpleNamespace(is_active=False)
    with pytest.raises(HTTPException) as caught:
        ai_api_key.get_current_user_by_ai_api_key(
            _FakeSession([credential], user=inactive), "Bearer ccai_secret"
        )
    assert caught.value.status_code == 401


# --- deps/auth ------------------------------------------------------------------


async def test_decode_access_token_rejects_garbage() -> None:
    with pytest.raises(AuthenticationError):
        await auth._decode_access_token("not-a-jwt")


def test_check_token_user_requires_user() -> None:
    with pytest.raises(AuthenticationError):
        auth._check_token_user(None, SimpleNamespace(ver=0))


@pytest.mark.parametrize("token", ["", "   ", "x" * 4097])
async def test_ws_auth_rejects_empty_or_oversized_token(token: str) -> None:
    with pytest.raises(WebSocketException) as caught:
        await auth.get_ws_current_user(None, token)
    assert caught.value.code == 1008


# --- deps/rate_limit --------------------------------------------------------------


def _request(headers: dict[str, str] | None = None, client=None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (key.lower().encode(), value.encode())
            for key, value in (headers or {}).items()
        ],
        "client": client,
    }
    return Request(scope)


def test_client_ip_prefers_real_ip_then_last_forwarded_hop() -> None:
    assert rate_limit._client_ip(_request({"X-Real-IP": " 10.0.0.9 "})) == "10.0.0.9"
    assert (
        rate_limit._client_ip(
            _request({"X-Forwarded-For": "1.1.1.1, 10.0.0.7 "}, ("9.9.9.9", 1))
        )
        == "10.0.0.7"
    )
    assert (
        rate_limit._client_ip(_request({"X-Forwarded-For": " , "}, ("9.9.9.9", 1)))
        == "9.9.9.9"
    )
    assert rate_limit._client_ip(_request()) == "unknown"


async def test_rate_limit_by_ip_raises_429(monkeypatch) -> None:
    async def _redis():
        return object()

    async def _deny(_redis, **kwargs):
        assert kwargs["key"] == "ip:login:10.0.0.9"
        return False, {"window_seconds": 42}

    monkeypatch.setattr(rate_limit, "get_redis", _redis)
    monkeypatch.setattr(rate_limit, "check_rate_limit_by_key", _deny)
    dep = rate_limit.rate_limit_by_ip(scope="login", limit=1, window_seconds=60)
    with pytest.raises(HTTPException) as caught:
        await dep(_request({"X-Real-IP": "10.0.0.9"}))
    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "42"}


# --- ai_capacity --------------------------------------------------------------------


def test_start_ai_capacity_refuses_while_previous_workers_pending(monkeypatch) -> None:
    monkeypatch.setattr(ai_capacity, "_closed", True)
    monkeypatch.setattr(ai_capacity, "_pending", {object()})
    with pytest.raises(RuntimeError, match="not drained"):
        ai_capacity.start_ai_capacity()


# --- request_body ---------------------------------------------------------------------


class _Inner(BaseModel):
    value: int


class _Outer(BaseModel):
    inner: _Inner
    items: list[_Inner] = []


def test_limited_json_openapi_inlines_local_defs() -> None:
    spec = request_body.limited_json_openapi(_Outer)
    schema = spec["requestBody"]["content"]["application/json"]["schema"]
    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    assert schema["properties"]["inner"]["properties"]["value"]["type"] == "integer"
    assert schema["properties"]["items"]["items"]["properties"]["value"]
