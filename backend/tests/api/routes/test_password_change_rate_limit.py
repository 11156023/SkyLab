"""`PATCH /users/me/password` 依帳號節流：每 15 分鐘 5 次，不看來源 IP。

改密碼要先驗目前密碼；不節流的話，拿到登入狀態的人可以暴力猜原密碼。
測試環境沒開 Redis，比照 test_login_account_rate_limit 換成記憶體版限流。
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import rate_limit as rate_limit_deps
from app.core.config import settings
from app.repositories import user as user_repo
from app.schemas import UserCreate
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_password

URL = f"{settings.API_V1_STR}/users/me/password"


class _FakeLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.counts: dict[str, int] = {}

    async def check(
        self,
        _redis: Any,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        scope: str = "",
    ) -> tuple[bool, dict[str, Any]]:
        self.calls.append((key, limit, window_seconds))
        current = self.counts.get(key, 0)
        if current >= limit:
            return False, {"current": current, "window_seconds": window_seconds}
        self.counts[key] = current + 1
        return True, {"current": current + 1, "window_seconds": window_seconds}


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> _FakeLimiter:
    fake = _FakeLimiter()

    async def get_redis() -> object:
        return object()

    monkeypatch.setattr(rate_limit_deps, "get_redis", get_redis)
    monkeypatch.setattr(rate_limit_deps, "check_rate_limit_by_key", fake.check)
    return fake


def _login_new_user(client: TestClient, db: Session) -> tuple[dict[str, str], str]:
    email = random_email()
    password = random_password()
    user_repo.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    db.commit()
    return user_authentication_headers(client=client, email=email, password=password), password


def test_password_change_is_rate_limited_per_user(
    client: TestClient, db: Session, limiter: _FakeLimiter
) -> None:
    headers, password = _login_new_user(client, db)
    for _ in range(5):
        r = client.patch(
            URL,
            headers=headers,
            json={"current_password": "wrong-guess-1A!", "new_password": random_password()},
        )
        assert r.status_code == 400, r.text

    # 第 6 次就算猜對也擋下，攻擊者無從得知
    r = client.patch(
        URL,
        headers=headers,
        json={"current_password": password, "new_password": random_password()},
    )
    assert r.status_code == 429, r.text
    assert "Retry-After" in r.headers

    # 同一個來源 IP 的其他帳號不受影響
    other_headers, other_password = _login_new_user(client, db)
    r = client.patch(
        URL,
        headers=other_headers,
        json={"current_password": other_password, "new_password": random_password()},
    )
    assert r.status_code == 200, r.text

    # 依使用者計次（key 是 user id，不是 IP），5 次／15 分鐘；登入本身另有節流
    calls = [c for c in limiter.calls if c[0].startswith("user:password-change:")]
    assert len({key for key, _limit, _window in calls}) == 2
    assert {(limit, window) for _key, limit, window in calls} == {(5, 900)}
