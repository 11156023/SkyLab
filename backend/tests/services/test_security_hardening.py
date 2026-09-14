"""2026-09 安全審查修復的回歸測試（純單元，不需 DB / Redis / PVE）。

涵蓋：
- refresh token 輪替（同一 refresh token 超過寬限期後不可重複兌換）
- 密碼重設 token 綁定 token_version（一封信只能成功重設一次）
- GPU hostpci 的 mdev 規格白名單（不可夾帶額外 hostpci 選項）
- PortSpec.protocol 正規化與白名單
- 反向代理 / NAT 目標 IP 的白名單檢查
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from pydantic import ValidationError

import app.infrastructure.redis as redis_module
from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError, ProxmoxError
from app.infrastructure.redis.token_blacklist import mark_refresh_token_used
from app.schemas.firewall import PortSpec
from app.services.network.publish_target_policy import validate_publish_target_ip
from app.services.proxmox import gpu_service, provisioning_service
from app.services.user import auth_service
from app.utils.token import (
    decode_password_reset_token,
    generate_password_reset_token,
    verify_password_reset_token,
)

_USER_ID = uuid.uuid4()


class _FakeRedis:
    """只支援本測試用到的 set(nx/ex) / get / exists。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def exists(self, key: str) -> int:
        return int(key in self.store)


# ---------------------------------------------------------------------------
# refresh token rotation
# ---------------------------------------------------------------------------


async def test_mark_refresh_token_used_allows_first_use_and_grace_window() -> None:
    redis = _FakeRedis()
    exp = int(time.time()) + 3600

    assert await mark_refresh_token_used(redis, "jti-1", exp) is True
    # 寬限期內（多分頁競態）仍允許
    assert await mark_refresh_token_used(redis, "jti-1", exp) is True


async def test_mark_refresh_token_used_rejects_stale_reuse() -> None:
    redis = _FakeRedis()
    exp = int(time.time()) + 3600
    redis.store["refresh_used:jti-2"] = str(int(time.time()) - 600)

    assert await mark_refresh_token_used(redis, "jti-2", exp) is False


async def test_mark_refresh_token_used_fails_open_without_redis() -> None:
    assert await mark_refresh_token_used(None, "jti-3", int(time.time()) + 60) is True


def _make_refresh_token(*, jti: str) -> str:
    payload: dict[str, Any] = {
        "exp": datetime.now(timezone.utc) + timedelta(days=1),
        "sub": str(_USER_ID),
        "type": "refresh",
        "ver": 0,
        "jti": jti,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeSession:
    def __init__(self, user: Any) -> None:
        self._user = user

    def get(self, model: Any, key: Any) -> Any:  # noqa: ARG002
        return self._user


async def test_refresh_access_token_rejects_rotated_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()
    fake_redis.store["refresh_used:old-jti"] = str(int(time.time()) - 600)

    async def fake_get_redis() -> _FakeRedis:
        return fake_redis

    monkeypatch.setattr(redis_module, "get_redis", fake_get_redis)
    session = _FakeSession(
        SimpleNamespace(id=_USER_ID, token_version=0, is_active=True)
    )

    with pytest.raises(AuthenticationError):
        await auth_service.refresh_access_token(
            session=session, refresh_token=_make_refresh_token(jti="old-jti")
        )


async def test_refresh_access_token_first_exchange_succeeds_and_records_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    async def fake_get_redis() -> _FakeRedis:
        return fake_redis

    monkeypatch.setattr(redis_module, "get_redis", fake_get_redis)
    session = _FakeSession(
        SimpleNamespace(id=_USER_ID, token_version=0, is_active=True)
    )

    token = await auth_service.refresh_access_token(
        session=session, refresh_token=_make_refresh_token(jti="fresh-jti")
    )
    assert token.access_token
    assert "refresh_used:fresh-jti" in fake_redis.store


# ---------------------------------------------------------------------------
# password reset token is single-use via token_version binding
# ---------------------------------------------------------------------------


def test_password_reset_token_carries_token_version() -> None:
    token = generate_password_reset_token(email="user@example.com", token_version=3)
    assert decode_password_reset_token(token) == ("user@example.com", 3)
    assert verify_password_reset_token(token) == "user@example.com"


def test_reset_password_rejects_token_issued_before_version_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=_USER_ID, email="user@example.com", token_version=4, is_active=True
    )
    monkeypatch.setattr(
        auth_service.user_repo, "get_user_by_email", lambda *, session, email: user
    )
    stale_token = generate_password_reset_token(email=user.email, token_version=3)

    with pytest.raises(BadRequestError):
        auth_service.reset_password(
            session=SimpleNamespace(), token=stale_token, new_password="N3wPassw0rd!"
        )


def test_reset_password_accepts_current_version_and_bumps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=_USER_ID, email="user@example.com", token_version=4, is_active=True
    )
    monkeypatch.setattr(
        auth_service.user_repo, "get_user_by_email", lambda *, session, email: user
    )
    monkeypatch.setattr(
        auth_service.user_repo, "update_user", lambda *, session, db_user, user_in: db_user
    )
    monkeypatch.setattr(
        auth_service.audit_service, "log_action", lambda **kwargs: None
    )
    session = SimpleNamespace(add=lambda obj: None, commit=lambda: None)
    token = generate_password_reset_token(email=user.email, token_version=4)

    auth_service.reset_password(session=session, token=token, new_password="N3wPassw0rd!")
    assert user.token_version == 5

    # 同一個連結第二次使用必須失敗
    with pytest.raises(BadRequestError):
        auth_service.reset_password(
            session=session, token=token, new_password="An0therPass!"
        )


# ---------------------------------------------------------------------------
# GPU hostpci option smuggling
# ---------------------------------------------------------------------------


def _mapping(profiles: list[Any]) -> SimpleNamespace:
    return SimpleNamespace(
        available_count=1, used_count=0, capacity_count=1, profiles=profiles
    )


def test_build_gpu_hostpci_rejects_option_smuggling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_service, "get_gpu_mapping", lambda mapping_id: _mapping([]))

    with pytest.raises(ProxmoxError):
        provisioning_service._build_gpu_hostpci(
            "gpu0", "nvidia-1,romfile=/etc/passwd,rombar=0"
        )


def test_build_gpu_hostpci_rejects_profile_on_raw_passthrough_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gpu_service, "get_gpu_mapping", lambda mapping_id: _mapping([]))

    with pytest.raises(ProxmoxError):
        provisioning_service._build_gpu_hostpci("gpu0", "nvidia-1")


def test_build_gpu_hostpci_accepts_known_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = SimpleNamespace(
        mdev_type="nvidia-1", creatable=True, vram_mb=1024, name="A"
    )
    monkeypatch.setattr(
        gpu_service, "get_gpu_mapping", lambda mapping_id: _mapping([profile])
    )

    assert (
        provisioning_service._build_gpu_hostpci("gpu0", "nvidia-1")
        == "mapping=gpu0,mdev=nvidia-1"
    )


# ---------------------------------------------------------------------------
# PortSpec.protocol
# ---------------------------------------------------------------------------


def test_port_spec_protocol_is_normalised_to_lowercase() -> None:
    assert PortSpec(port=80, protocol=" TCP ").protocol == "tcp"


@pytest.mark.parametrize("protocol", ["tcp\nfoo", "tcp;rm -rf /", "a" * 17, ""])
def test_port_spec_protocol_rejects_invalid_values(protocol: str) -> None:
    with pytest.raises(ValidationError):
        PortSpec(port=80, protocol=protocol)


# ---------------------------------------------------------------------------
# publish target IP policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "nope"],
)
def test_publish_target_rejects_special_addresses(ip: str) -> None:
    with pytest.raises(BadRequestError):
        validate_publish_target_ip(ip)


def test_publish_target_rejects_infrastructure_and_blocked_ranges() -> None:
    with pytest.raises(BadRequestError):
        validate_publish_target_ip("10.10.0.1", blocked_ips=["10.10.0.1"])
    with pytest.raises(BadRequestError):
        validate_publish_target_ip("192.168.100.125", blocked_cidrs=["192.168.100.0/24"])


def test_publish_target_enforces_vm_subnet_when_configured() -> None:
    with pytest.raises(BadRequestError):
        validate_publish_target_ip("192.168.1.50", allowed_cidrs=["10.10.0.0/16"])
    assert str(validate_publish_target_ip("10.10.3.7", allowed_cidrs=["10.10.0.0/16"])) == "10.10.3.7"


def test_publish_target_allows_normal_ip_without_subnet_config() -> None:
    assert str(validate_publish_target_ip("10.10.3.7")) == "10.10.3.7"
