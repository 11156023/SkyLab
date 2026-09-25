"""兩步驟驗證（TOTP）端到端：綁定 → 確認 → 登入需驗證碼 → 停用／管理員重設。

驗證碼 30 秒內不可重放，所以每個需要驗證碼的步驟都把 TOTP 模組看到的時鐘
固定在「測試開始時間 + N 分鐘」（只撥 ``app.utils.totp`` 用的時鐘，JWT 等
其他時間不受影響），步驟之間不會因為真實時鐘跨過 30 秒邊界而變成 flaky。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.models import User
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.utils import totp as totp_util
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR


@contextmanager
def _totp_clock(now: float) -> Iterator[None]:
    """把 TOTP 模組看到的時間固定在 ``now``。"""
    with patch("app.utils.totp.time") as fake_time:
        fake_time.time = lambda: now
        yield


def _code_at(secret: str, now: float) -> str:
    return totp_util.totp_code(secret, now=now)


def _create_user(db: Session) -> tuple[User, str]:
    email = random_email()
    password = random_lower_string()
    user = user_repo.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    db.commit()
    db.refresh(user)
    return user, password


def _login(client: TestClient, email: str, password: str) -> dict:
    r = client.post(
        f"{API}/login/access-token",
        data={"username": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _enable_totp(client: TestClient, headers: dict[str, str], *, now: float) -> str:
    r = client.post(f"{API}/users/me/totp/setup", headers=headers)
    assert r.status_code == 200, r.text
    setup = r.json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert f"secret={secret}" in setup["otpauth_uri"]
    with _totp_clock(now):
        r = client.post(
            f"{API}/users/me/totp/confirm",
            headers=headers,
            json={"code": _code_at(secret, now)},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"totp_enabled": True}
    return secret


def test_totp_full_flow(client: TestClient, db: Session) -> None:
    base = time.time()
    user, password = _create_user(db)
    tokens = _login(client, user.email, password)
    headers = _bearer(tokens["access_token"])

    # 未綁定：/users/me 顯示未啟用
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_enabled"] is False

    # 綁定：錯誤的驗證碼不能確認
    r = client.post(f"{API}/users/me/totp/setup", headers=headers)
    assert r.status_code == 200
    r = client.post(
        f"{API}/users/me/totp/confirm", headers=headers, json={"code": "000000"}
    )
    assert r.status_code == 400
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_enabled"] is False

    # 重新 setup 會換一把新金鑰；用正確驗證碼確認
    secret = _enable_totp(client, headers, now=base)
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_enabled"] is True
    # 金鑰不會以明文回到 API
    assert "totp_secret_encrypted" not in r.json()
    assert "secret" not in r.json()

    # 已啟用就不能再 setup（要先停用）
    r = client.post(f"{API}/users/me/totp/setup", headers=headers)
    assert r.status_code == 400

    # 密碼登入：只拿到挑戰 token
    challenge = _login(client, user.email, password)
    assert challenge["totp_required"] is True
    assert challenge["totp_token"]
    assert "access_token" not in challenge

    # 挑戰 token 不能當 access token 用
    r = client.get(f"{API}/users/me", headers=_bearer(challenge["totp_token"]))
    assert r.status_code == 401

    # 錯誤驗證碼 → 400
    with _totp_clock(base + 60):
        r = client.post(
            f"{API}/login/totp",
            json={"totp_token": challenge["totp_token"], "code": "123456"},
        )
    assert r.status_code == 400

    # 剛剛確認綁定用過的那組驗證碼（同一 step）不能重放
    with _totp_clock(base):
        r = client.post(
            f"{API}/login/totp",
            json={
                "totp_token": challenge["totp_token"],
                "code": _code_at(secret, base),
            },
        )
    assert r.status_code == 400

    # 下一個 step 的驗證碼 → 正式 token
    with _totp_clock(base + 60):
        r = client.post(
            f"{API}/login/totp",
            json={
                "totp_token": challenge["totp_token"],
                "code": _code_at(secret, base + 60),
            },
        )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    headers = _bearer(tokens["access_token"])
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["email"] == user.email

    # 亂湊的挑戰 token → 401
    r = client.post(
        f"{API}/login/totp", json={"totp_token": "not-a-jwt", "code": "123456"}
    )
    assert r.status_code == 401

    # 停用：錯誤驗證碼不行，正確的可以；之後登入直接拿 token
    with _totp_clock(base + 120):
        r = client.post(
            f"{API}/users/me/totp/disable", headers=headers, json={"code": "000000"}
        )
    assert r.status_code == 400
    with _totp_clock(base + 120):
        r = client.post(
            f"{API}/users/me/totp/disable",
            headers=headers,
            json={"code": _code_at(secret, base + 120)},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"totp_enabled": False}
    tokens = _login(client, user.email, password)
    assert "access_token" in tokens
    assert "totp_required" not in tokens


def test_admin_reset_totp(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    base = time.time()
    user, password = _create_user(db)
    tokens = _login(client, user.email, password)
    headers = _bearer(tokens["access_token"])
    _enable_totp(client, headers, now=base)

    challenge = _login(client, user.email, password)
    assert challenge["totp_required"] is True

    # 未啟用的人重設 → 400；啟用中的人 → 200
    other, _ = _create_user(db)
    r = client.delete(f"{API}/users/{other.id}/totp", headers=superuser_token_headers)
    assert r.status_code == 400
    r = client.delete(f"{API}/users/{user.id}/totp", headers=superuser_token_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"totp_enabled": False}

    # 重設後 token_version +1：舊挑戰 token 與舊 access token 都失效
    r = client.post(
        f"{API}/login/totp",
        json={"totp_token": challenge["totp_token"], "code": "123456"},
    )
    assert r.status_code == 401
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.status_code == 401

    # 重新登入不再需要驗證碼
    tokens = _login(client, user.email, password)
    assert "access_token" in tokens
    r = client.get(f"{API}/users/me", headers=_bearer(tokens["access_token"]))
    assert r.json()["totp_enabled"] is False


def test_admin_reset_totp_requires_superuser(
    client: TestClient, db: Session, normal_user_token_headers: dict[str, str]
) -> None:
    user, _ = _create_user(db)
    r = client.delete(f"{API}/users/{user.id}/totp", headers=normal_user_token_headers)
    assert r.status_code == 403


def test_user_totp_required_enforces_enrollment(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    """管理員在使用者資料勾「強制兩步驟驗證」後：未綁定者除帳號／登入端點外一律 403，
    綁定後恢復；要求中不可自行停用；管理員取消要求後又可自行停用。"""
    base = time.time()
    user, password = _create_user(db)
    tokens = _login(client, user.email, password)
    headers = _bearer(tokens["access_token"])

    # 預設不要求
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["totp_required"] is False
    assert r.json()["totp_setup_required"] is False
    r = client.get(f"{API}/users/{user.id}", headers=headers)
    assert r.status_code == 200

    # 一般使用者不能替自己或別人勾強制
    r = client.patch(
        f"{API}/users/{user.id}", headers=headers, json={"totp_required": True}
    )
    assert r.status_code == 403

    r = client.patch(
        f"{API}/users/{user.id}",
        headers=superuser_token_headers,
        json={"totp_required": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["totp_required"] is True

    # 未綁定：/users/me 標記需要綁定；其他 API 403；綁定相關端點仍可用
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["totp_required"] is True
    assert r.json()["totp_setup_required"] is True
    r = client.get(f"{API}/users/{user.id}", headers=headers)
    assert r.status_code == 403

    secret = _enable_totp(client, headers, now=base)

    # 綁定後恢復存取（同一顆 access token 即可，不必重新登入）
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_setup_required"] is False
    assert r.json()["totp_enabled"] is True
    r = client.get(f"{API}/users/{user.id}", headers=headers)
    assert r.status_code == 200

    # 要求中不可自行停用（即使驗證碼正確）
    with _totp_clock(base + 60):
        r = client.post(
            f"{API}/users/me/totp/disable",
            headers=headers,
            json={"code": _code_at(secret, base + 60)},
        )
    assert r.status_code == 400
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_enabled"] is True

    # 管理員取消要求後可自行停用
    r = client.patch(
        f"{API}/users/{user.id}",
        headers=superuser_token_headers,
        json={"totp_required": False},
    )
    assert r.status_code == 200
    assert r.json()["totp_required"] is False
    with _totp_clock(base + 120):
        r = client.post(
            f"{API}/users/me/totp/disable",
            headers=headers,
            json={"code": _code_at(secret, base + 120)},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"totp_enabled": False}


def test_create_user_with_totp_required(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    """新增使用者時直接勾強制：對方首次登入就只能進綁定畫面。"""
    email = f"totp-req-{uuid.uuid4().hex[:8]}@example.com"
    password = "Passw0rd!123"
    r = client.post(
        f"{API}/users/",
        headers=superuser_token_headers,
        json={"email": email, "password": password, "totp_required": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["totp_required"] is True
    assert r.json()["totp_enabled"] is False

    tokens = _login(client, email, password)
    headers = _bearer(tokens["access_token"])
    r = client.get(f"{API}/users/me", headers=headers)
    assert r.json()["totp_setup_required"] is True
    r = client.get(f"{API}/users/{r.json()['id']}", headers=headers)
    assert r.status_code == 403
