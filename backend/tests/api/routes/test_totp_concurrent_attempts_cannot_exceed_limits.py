"""/login/totp 同時送出的多個驗證碼仍受帳號與挑戰 token 的次數上限約束。

以前是先唯讀檢查計數、驗證失敗後才記錄，同時抵達的請求都讀到「還沒錯過」
而全部拿去驗證。這裡把 Redis 相關函式換成記憶體版（計數的 check 在 event
loop 內一次做完，等同 Lua 的原子性），並讓驗證在 worker thread 裡停一下，
確認真正進到驗證的次數不超過上限。
"""

from __future__ import annotations

import asyncio
import time
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
_ATTEMPTS = 10


def _challenge() -> str:
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "sub": _USER_ID,
        "type": "totp",
        "ver": 0,
        "jti": uuid.uuid4().hex,
        "method": "password",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.revoked: set[str] = set()
        # list.append 在多執行緒下不會漏記
        self.verified: list[str] = []

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

    def slow_wrong_code(*, session: Any, totp_token: str, code: str) -> Token:
        redis.verified.append(totp_token)
        # 驗證拖一點時間，讓其他請求在這期間都抵達
        time.sleep(0.05)
        raise BadRequestError("invalid code")

    monkeypatch.setattr(login_routes, "get_redis", get_redis)
    monkeypatch.setattr(login_routes, "is_jti_revoked", is_jti_revoked)
    monkeypatch.setattr(login_routes, "revoke_jti", revoke_jti)
    monkeypatch.setattr(login_routes, "peek_rate_limit_by_key", peek)
    monkeypatch.setattr(login_routes, "check_rate_limit_by_key", check)
    monkeypatch.setattr(login_routes.totp_service, "complete_login", slow_wrong_code)
    return redis


async def _attempt(token: str) -> Token:
    return await login_routes.login_totp(
        session=object(), body=TotpLoginRequest(totp_token=token, code="000000")
    )


async def test_concurrent_codes_on_one_challenge_stop_at_the_challenge_limit(
    fake_redis: _FakeRedis,
) -> None:
    token = _challenge()

    results = await asyncio.gather(
        *(_attempt(token) for _ in range(_ATTEMPTS)), return_exceptions=True
    )

    limit = login_routes._TOTP_CHALLENGE_FAIL_LIMIT
    assert len(fake_redis.verified) <= limit
    assert sum(isinstance(r, BadRequestError) for r in results) == len(fake_redis.verified)
    assert sum(isinstance(r, AuthenticationError) for r in results) == (
        _ATTEMPTS - len(fake_redis.verified)
    )
    # 名額用完的挑戰 token 已作廢，之後再送也不會進到驗證
    claims = login_routes._totp_challenge_claims(token)
    assert claims is not None and claims.jti in fake_redis.revoked


async def test_concurrent_codes_across_challenges_stop_at_the_account_limit(
    fake_redis: _FakeRedis,
) -> None:
    tokens = [_challenge() for _ in range(_ATTEMPTS)]

    results = await asyncio.gather(
        *(_attempt(token) for token in tokens), return_exceptions=True
    )

    assert len(fake_redis.verified) <= login_routes._TOTP_USER_FAIL_LIMIT
    locked = [r for r in results if isinstance(r, HTTPException)]
    assert len(locked) == _ATTEMPTS - len(fake_redis.verified)
    assert all(r.status_code == 429 for r in locked)
