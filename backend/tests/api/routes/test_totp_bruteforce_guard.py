"""/login/totp 依帳號與挑戰 token 限制驗證碼錯誤次數。

以前只有依 IP 的節流，換來源 IP 就能無限猜 6 位數驗證碼。這裡把 Redis 相關
函式換成記憶體版，直接呼叫路由函式驗證鎖定規則（不需要 DB 或 Redis）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import pytest
from fastapi import HTTPException

from app.api.routes import login as login_routes
from app.core import security
from app.core.config import settings
from app.exceptions import AuthenticationError, BadRequestError
from app.schemas import Token, TotpLoginRequest

_USER_ID = str(uuid.uuid4())
_GOOD_CODE = "246810"


def _challenge(user_id: str = _USER_ID) -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": user_id,
        "type": "totp",
        "ver": 0,
        "jti": uuid.uuid4().hex,
        "method": "password",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeRedis:
    """記錄每個 rate limit key 的計數與被撤銷的 jti。"""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.revoked: set[str] = set()

    async def delete(self, key: str) -> int:
        prefix = login_routes.rate_limiter._KEY_PREFIX
        return 1 if self.counts.pop(key.removeprefix(prefix), None) else 0


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    redis = _FakeRedis()

    async def get_redis() -> _FakeRedis:
        return redis

    async def is_jti_revoked(_redis: Any, jti: str) -> bool:
        return jti in redis.revoked

    async def revoke_jti(_redis: Any, jti: str, exp_unix: int) -> bool:
        redis.revoked.add(jti)
        return True

    async def peek(_redis: Any, *, key: str, window_seconds: int) -> int:
        return redis.counts.get(key, 0)

    async def check(
        _redis: Any, *, key: str, limit: int, window_seconds: int, scope: str = ""
    ) -> tuple[bool, dict[str, Any]]:
        current = redis.counts.get(key, 0)
        if current >= limit:
            return False, {"current": current}
        redis.counts[key] = current + 1
        return True, {"current": current + 1}

    def complete_login(*, session: Any, totp_token: str, code: str) -> Token:
        if code != _GOOD_CODE:
            raise BadRequestError("invalid code")
        return Token(access_token="access", refresh_token="refresh")

    monkeypatch.setattr(login_routes, "get_redis", get_redis)
    monkeypatch.setattr(login_routes, "is_jti_revoked", is_jti_revoked)
    monkeypatch.setattr(login_routes, "revoke_jti", revoke_jti)
    monkeypatch.setattr(login_routes, "peek_rate_limit_by_key", peek)
    monkeypatch.setattr(login_routes, "check_rate_limit_by_key", check)
    monkeypatch.setattr(login_routes.totp_service, "complete_login", complete_login)
    return redis


async def _attempt(token: str, code: str) -> Token:
    return await login_routes.login_totp(
        session=object(), body=TotpLoginRequest(totp_token=token, code=code)
    )


async def test_account_is_locked_after_repeated_failures_across_challenges(
    fake_redis: _FakeRedis,
) -> None:
    # 每次都換新的挑戰 token（等同攻擊者重走密碼步驟、換 IP）
    for _ in range(login_routes._TOTP_USER_FAIL_LIMIT):
        with pytest.raises(BadRequestError):
            await _attempt(_challenge(), "000000")

    with pytest.raises(HTTPException) as exc_info:
        await _attempt(_challenge(), "000000")
    assert exc_info.value.status_code == 429

    # 鎖定期間連正確的驗證碼也不接受
    with pytest.raises(HTTPException) as exc_info:
        await _attempt(_challenge(), _GOOD_CODE)
    assert exc_info.value.status_code == 429


async def test_challenge_is_revoked_after_three_wrong_codes(
    fake_redis: _FakeRedis,
) -> None:
    token = _challenge()
    for _ in range(login_routes._TOTP_CHALLENGE_FAIL_LIMIT):
        with pytest.raises(BadRequestError):
            await _attempt(token, "000000")

    with pytest.raises(AuthenticationError):
        await _attempt(token, _GOOD_CODE)

    # 重新走第一階段拿到的新挑戰 token 仍可登入
    result = await _attempt(_challenge(), _GOOD_CODE)
    assert result.access_token == "access"


async def test_success_resets_the_account_failure_counter(
    fake_redis: _FakeRedis,
) -> None:
    for _ in range(login_routes._TOTP_USER_FAIL_LIMIT - 1):
        with pytest.raises(BadRequestError):
            await _attempt(_challenge(), "000000")

    await _attempt(_challenge(), _GOOD_CODE)

    assert fake_redis.counts.get(login_routes._totp_user_fail_key(_USER_ID)) is None
    # 歸零後再錯一次不會被鎖
    with pytest.raises(BadRequestError):
        await _attempt(_challenge(), "000000")


async def test_invalid_challenge_token_is_rejected_before_any_counting(
    fake_redis: _FakeRedis,
) -> None:
    with pytest.raises(AuthenticationError):
        await _attempt("not-a-jwt", _GOOD_CODE)
    assert fake_redis.counts == {}
