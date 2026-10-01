"""帳號密碼複雜度：所有設定密碼的 API 入口都擋弱密碼，登入不受影響。"""

from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.security import verify_password
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.utils import generate_password_reset_token
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_password

# 長度足夠（過得了 schema 的 min_length），但沒有大寫與特殊符號
WEAK_PASSWORD = "weakpassword1"
TOO_WEAK_DETAIL = "密碼至少需要 8 個字元，並同時包含大寫英文字母、小寫英文字母、數字與特殊符號"


def _create_weak_user(db: Session) -> str:
    """直接走 repository 建帳號（比照 .env 預設管理員、既有帳號）：密碼不符合新規則。"""
    email = random_email()
    user_repo.create_user(
        session=db, user_create=UserCreate(email=email, password=WEAK_PASSWORD)
    )
    db.commit()
    return email


def test_signup_rejects_weak_password(client: TestClient, db: Session) -> None:
    email = random_email()
    with patch("app.services.user.user_service.settings.ENABLE_SIGNUP", True):
        r = client.post(
            f"{settings.API_V1_STR}/users/signup",
            json={"email": email, "password": WEAK_PASSWORD, "full_name": "Weak"},
        )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TOO_WEAK_DETAIL
    assert user_repo.get_user_by_email(session=db, email=email) is None


def test_admin_create_user_rejects_weak_password(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    email = random_email()
    r = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json={"email": email, "password": WEAK_PASSWORD},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TOO_WEAK_DETAIL
    assert user_repo.get_user_by_email(session=db, email=email) is None


def test_admin_update_user_rejects_weak_password_but_allows_other_edits(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    email = _create_weak_user(db)
    user = user_repo.get_user_by_email(session=db, email=email)
    assert user is not None

    r = client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json={"password": "anotherweak1"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TOO_WEAK_DETAIL
    db.refresh(user)
    verified, _ = verify_password(WEAK_PASSWORD, user.hashed_password)
    assert verified

    # 不改密碼的編輯不受影響：既有弱密碼帳號照樣能改姓名
    r = client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json={"full_name": "Renamed"},
    )
    assert r.status_code == 200, r.text

    r = client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json={"password": random_password()},
    )
    assert r.status_code == 200, r.text


def test_existing_weak_password_can_still_login_but_must_change_to_strong(
    client: TestClient, db: Session
) -> None:
    email = _create_weak_user(db)
    # 登入不檢查複雜度：規則上線前建立的帳號照常登入
    headers = user_authentication_headers(
        client=client, email=email, password=WEAK_PASSWORD
    )

    r = client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=headers,
        json={"current_password": WEAK_PASSWORD, "new_password": "anotherweak1"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TOO_WEAK_DETAIL

    new_password = random_password()
    r = client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=headers,
        json={"current_password": WEAK_PASSWORD, "new_password": new_password},
    )
    assert r.status_code == 200, r.text
    user_authentication_headers(client=client, email=email, password=new_password)


def test_reset_password_rejects_weak_password_and_keeps_token_usable(
    client: TestClient, db: Session
) -> None:
    email = _create_weak_user(db)
    token = generate_password_reset_token(email=email)

    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        json={"token": token, "new_password": "anotherweak1"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == TOO_WEAK_DETAIL

    # 被擋下的那次不消耗重設連結：改成合規密碼後同一個 token 仍可用
    new_password = random_password()
    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        json={"token": token, "new_password": new_password},
    )
    assert r.status_code == 200, r.text
    user_authentication_headers(client=client, email=email, password=new_password)
