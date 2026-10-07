import uuid
from html import unescape
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models import User
from app.repositories import user as user_repo
from app.schemas import UserCreate
from tests.utils.user import create_random_user, user_authentication_headers
from tests.utils.utils import random_email, random_lower_string, random_password


def _refresh_superuser_token(
    client: TestClient, headers: dict[str, str], password: str
) -> None:
    """Re-login as FIRST_SUPERUSER and mutate `headers` in place.

    Needed after any operation that bumps the superuser's ``token_version``
    (e.g. password change), so module-scoped fixtures keep a valid token.
    """
    r = client.post(
        f"{settings.API_V1_STR}/login/access-token",
        data={"username": settings.FIRST_SUPERUSER, "password": password},
    )
    headers["Authorization"] = f"Bearer {r.json()['access_token']}"


def test_get_users_superuser_me(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{settings.API_V1_STR}/users/me", headers=superuser_token_headers)
    current_user = r.json()
    assert current_user
    assert current_user["is_active"] is True
    assert current_user["is_superuser"]
    assert current_user["email"] == settings.FIRST_SUPERUSER


def test_get_users_normal_user_me(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{settings.API_V1_STR}/users/me", headers=normal_user_token_headers)
    current_user = r.json()
    assert current_user
    assert current_user["is_active"] is True
    assert current_user["is_superuser"] is False
    assert current_user["email"] == settings.EMAIL_TEST_USER


def test_create_user_new_email(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    with (
        patch("app.utils.send_email", return_value=None),
        patch("app.core.config.settings.SMTP_HOST", "smtp.example.com"),
        patch("app.core.config.settings.SMTP_USER", "admin@example.com"),
    ):
        username = random_email()
        password = random_password()
        data = {"email": username, "password": password}
        r = client.post(
            f"{settings.API_V1_STR}/users/",
            headers=superuser_token_headers,
            json=data,
        )
        assert 200 <= r.status_code < 300
        created_user = r.json()
        user = user_repo.get_user_by_email(session=db, email=username)
        assert user
        assert user.email == created_user["email"]


def test_get_existing_user_as_superuser(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)
    user_id = user.id
    r = client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert 200 <= r.status_code < 300
    api_user = r.json()
    existing_user = user_repo.get_user_by_email(session=db, email=username)
    assert existing_user
    assert existing_user.email == api_user["email"]


def test_get_non_existing_user_as_superuser(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "找不到使用者"}


def test_get_existing_user_current_user(client: TestClient, db: Session) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)
    user_id = user.id

    login_data = {
        "username": username,
        "password": password,
    }
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    a_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {a_token}"}

    r = client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=headers,
    )
    assert 200 <= r.status_code < 300
    api_user = r.json()
    existing_user = user_repo.get_user_by_email(session=db, email=username)
    assert existing_user
    assert existing_user.email == api_user["email"]


def test_get_existing_user_permissions_error(
    db: Session,
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    user = create_random_user(db)

    r = client.get(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "你的權限不足，無法執行這項操作"}


def test_get_non_existing_user_permissions_error(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    user_id = uuid.uuid4()

    r = client.get(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "你的權限不足，無法執行這項操作"}


def test_create_user_existing_username(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    # username = email
    password = random_password()
    user_in = UserCreate(email=username, password=password)
    user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    data = {"email": username, "password": password}
    r = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=superuser_token_headers,
        json=data,
    )
    created_user = r.json()
    assert r.status_code == 409
    assert "_id" not in created_user


def test_create_user_by_normal_user(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    username = random_email()
    password = random_lower_string()
    data = {"email": username, "password": password}
    r = client.post(
        f"{settings.API_V1_STR}/users/",
        headers=normal_user_token_headers,
        json=data,
    )
    assert r.status_code == 403


def test_retrieve_users(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user_repo.create_user(session=db, user_create=user_in)

    username2 = random_email()
    password2 = random_lower_string()
    user_in2 = UserCreate(email=username2, password=password2)
    user_repo.create_user(session=db, user_create=user_in2)
    db.commit()

    r = client.get(f"{settings.API_V1_STR}/users/", headers=superuser_token_headers)
    all_users = r.json()

    assert len(all_users["data"]) > 1
    assert "count" in all_users
    for item in all_users["data"]:
        assert "email" in item


def test_update_user_me(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    full_name = "Updated Name"
    avatar_url = "https://example.com/avatar.png"
    data = {"full_name": full_name, "avatar_url": avatar_url}
    r = client.patch(
        f"{settings.API_V1_STR}/users/me",
        headers=normal_user_token_headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()
    assert updated_user["email"] == settings.EMAIL_TEST_USER
    assert updated_user["full_name"] == full_name
    assert updated_user["avatar_url"] == avatar_url

    user_query = select(User).where(User.email == settings.EMAIL_TEST_USER)
    user_db = db.exec(user_query).first()
    assert user_db
    assert user_db.email == settings.EMAIL_TEST_USER
    assert user_db.full_name == full_name
    assert user_db.avatar_url == avatar_url


def test_email_change_requires_new_mailbox_verification(
    client: TestClient, db: Session
) -> None:
    old_email = random_email()
    password = random_password()
    account = user_repo.create_user(
        session=db, user_create=UserCreate(email=old_email, password=password)
    )
    db.commit()
    db.refresh(account)
    headers = user_authentication_headers(client=client, email=old_email, password=password)
    new_email = random_email()
    direct = client.patch(
        f"{settings.API_V1_STR}/users/me",
        headers=headers,
        json={"email": new_email},
    )
    assert direct.status_code == 400
    assert db.exec(select(User).where(User.email == old_email)).first()

    sent: list[dict[str, str]] = []
    with (
        patch("app.services.user.user_service.send_email", side_effect=lambda **kw: sent.append(kw)),
        patch("app.core.config.settings.SMTP_HOST", "smtp.example.com"),
        patch("app.core.config.settings.EMAILS_FROM_EMAIL", "sender@example.com"),
    ):
        request = client.post(
            f"{settings.API_V1_STR}/users/me/email-change",
            headers=headers,
            json={"email": new_email},
        )
    assert request.status_code == 200
    assert sent[0]["email_to"] == new_email
    assert db.exec(select(User).where(User.email == old_email)).first()

    link = unescape(sent[0]["html_content"].split('href="')[1].split('"')[0])
    assert urlparse(link).path == "/verify-email-change"
    token = parse_qs(urlparse(link).query)["token"][0]
    confirmed = client.post(
        f"{settings.API_V1_STR}/users/email-change/confirm",
        json={"token": token},
    )
    assert confirmed.status_code == 200
    assert db.exec(select(User).where(User.email == new_email)).first()

    replay = client.post(
        f"{settings.API_V1_STR}/users/email-change/confirm",
        json={"token": token},
    )
    assert replay.status_code == 400


def test_update_password_me(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    new_password = random_password()
    data = {
        "current_password": settings.FIRST_SUPERUSER_PASSWORD,
        "new_password": new_password,
    }
    r = client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()
    assert updated_user["message"] == "Password updated successfully"

    user_query = select(User).where(User.email == settings.FIRST_SUPERUSER)
    user_db = db.exec(user_query).first()
    assert user_db
    assert user_db.email == settings.FIRST_SUPERUSER
    verified, _ = verify_password(new_password, user_db.hashed_password)
    assert verified

    # Password change bumps token_version to invalidate old tokens — the
    # module-scoped fixture's token is now stale. Re-login and mutate the
    # fixture dict in place so downstream tests sharing the fixture keep a
    # valid token.
    _refresh_superuser_token(client, superuser_token_headers, new_password)

    # Revert to the old password to keep consistency in test. The .env
    # password need not satisfy the complexity rule, so it cannot be set back
    # through the API — restore the hash directly instead.
    user_db.hashed_password = get_password_hash(settings.FIRST_SUPERUSER_PASSWORD)
    db.add(user_db)
    db.commit()


def test_update_password_me_incorrect_password(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    new_password = random_lower_string()
    data = {"current_password": new_password, "new_password": new_password}
    r = client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 400
    updated_user = r.json()
    assert updated_user["detail"] == "密碼錯誤"


def test_update_user_me_email_exists(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)

    data = {"email": user.email}
    r = client.patch(
        f"{settings.API_V1_STR}/users/me",
        headers=normal_user_token_headers,
        json=data,
    )
    assert r.status_code == 400


def test_update_password_me_same_password_error(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {
        "current_password": settings.FIRST_SUPERUSER_PASSWORD,
        "new_password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = client.patch(
        f"{settings.API_V1_STR}/users/me/password",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 400
    updated_user = r.json()
    assert updated_user["detail"] == "新密碼不可與目前密碼相同"


def test_register_user(client: TestClient, db: Session) -> None:
    username = random_email()
    password = random_password()
    full_name = random_lower_string()
    avatar_url = "https://example.com/register-avatar.png"
    data = {
        "email": username,
        "password": password,
        "full_name": full_name,
        "avatar_url": avatar_url,
    }
    with patch("app.services.user.user_service.settings.ENABLE_SIGNUP", True):
        r = client.post(
            f"{settings.API_V1_STR}/users/signup",
            json=data,
        )
    assert r.status_code == 200
    created_user = r.json()
    assert created_user["email"] == username
    assert created_user["full_name"] == full_name
    assert created_user["avatar_url"] == avatar_url

    user_query = select(User).where(User.email == username)
    user_db = db.exec(user_query).first()
    assert user_db
    assert user_db.email == username
    assert user_db.full_name == full_name
    assert user_db.avatar_url == avatar_url
    verified, _ = verify_password(password, user_db.hashed_password)
    assert verified


def test_register_user_already_exists_error(client: TestClient) -> None:
    password = random_password()
    full_name = random_lower_string()
    data = {
        "email": settings.FIRST_SUPERUSER,
        "password": password,
        "full_name": full_name,
    }
    with patch("app.services.user.user_service.settings.ENABLE_SIGNUP", True):
        r = client.post(
            f"{settings.API_V1_STR}/users/signup",
            json=data,
        )
    assert r.status_code == 409
    assert r.json()["detail"] == "此電子郵件已經有使用者存在於系統中。"


def test_register_user_disabled(client: TestClient) -> None:
    data = {
        "email": random_email(),
        "password": random_lower_string(),
        "full_name": random_lower_string(),
    }
    with patch("app.services.user.user_service.settings.ENABLE_SIGNUP", False):
        r = client.post(
            f"{settings.API_V1_STR}/users/signup",
            json=data,
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "使用者註冊功能目前已停用"


def test_update_user(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)

    data = {"full_name": "Updated_full_name"}
    r = client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 200
    updated_user = r.json()

    assert updated_user["full_name"] == "Updated_full_name"

    user_query = select(User).where(User.email == username)
    user_db = db.exec(user_query).first()
    db.refresh(user_db)
    assert user_db
    assert user_db.full_name == "Updated_full_name"


def test_update_user_not_exists(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"full_name": "Updated_full_name"}
    r = client.patch(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "系統中不存在此 ID 的使用者"


def test_update_user_email_exists(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)

    username2 = random_email()
    password2 = random_lower_string()
    user_in2 = UserCreate(email=username2, password=password2)
    user2 = user_repo.create_user(session=db, user_create=user_in2)
    db.commit()
    db.refresh(user)
    db.refresh(user2)

    data = {"email": user2.email}
    r = client.patch(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=superuser_token_headers,
        json=data,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "此電子郵件的使用者已存在"


def test_delete_user_me(client: TestClient, db: Session) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)
    user_id = user.id

    login_data = {
        "username": username,
        "password": password,
    }
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    a_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {a_token}"}

    r = client.delete(
        f"{settings.API_V1_STR}/users/me",
        headers=headers,
    )
    assert r.status_code == 200
    deleted_user = r.json()
    assert deleted_user["message"] == "User deleted successfully"
    db.expire_all()
    result = db.exec(select(User).where(User.id == user_id)).first()
    assert result is not None
    assert result.deleted_at is not None
    assert result.is_active is False
    assert client.get(f"{settings.API_V1_STR}/users/me", headers=headers).status_code == 401


def test_delete_user_me_as_superuser(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.delete(
        f"{settings.API_V1_STR}/users/me",
        headers=superuser_token_headers,
    )
    assert r.status_code == 403
    response = r.json()
    assert response["detail"] == "管理員不可刪除自己的帳號"


def test_delete_user_super_user(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)
    user_id = user.id
    r = client.delete(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 200
    deleted_user = r.json()
    assert deleted_user["message"] == "User deleted successfully"
    db.expire_all()
    result = db.exec(select(User).where(User.id == user_id)).first()
    assert result is not None
    assert result.deleted_at is not None
    assert result.is_active is False


def test_delete_user_not_found(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.delete(
        f"{settings.API_V1_STR}/users/{uuid.uuid4()}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "找不到使用者"


def test_delete_user_current_super_user_error(
    client: TestClient, superuser_token_headers: dict[str, str], db: Session
) -> None:
    super_user = user_repo.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    assert super_user
    user_id = super_user.id

    r = client.delete(
        f"{settings.API_V1_STR}/users/{user_id}",
        headers=superuser_token_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "管理員不可刪除自己的帳號"


def test_delete_user_without_privileges(
    client: TestClient, normal_user_token_headers: dict[str, str], db: Session
) -> None:
    username = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=username, password=password)
    user = user_repo.create_user(session=db, user_create=user_in)
    db.commit()
    db.refresh(user)

    r = client.delete(
        f"{settings.API_V1_STR}/users/{user.id}",
        headers=normal_user_token_headers,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "你的權限不足，無法執行這項操作"


def test_onboarding_defaults_and_complete(client: TestClient, db: Session) -> None:
    """新帳號的引導精靈旗標預設 False；呼叫完成端點後為 True，且重複呼叫無副作用。"""
    email = random_email()
    password = random_lower_string()
    user = user_repo.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    db.commit()
    db.refresh(user)
    headers = user_authentication_headers(client=client, email=email, password=password)

    r = client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["onboarding_completed"] is False

    r = client.post(
        f"{settings.API_V1_STR}/users/me/onboarding/complete", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["onboarding_completed"] is True
    assert r.json()["email"] == email

    db.refresh(user)
    assert user.onboarding_completed is True

    r = client.post(
        f"{settings.API_V1_STR}/users/me/onboarding/complete", headers=headers
    )
    assert r.status_code == 200
    assert r.json()["onboarding_completed"] is True

    r = client.get(f"{settings.API_V1_STR}/users/me", headers=headers)
    assert r.json()["onboarding_completed"] is True


def test_onboarding_complete_requires_login(client: TestClient) -> None:
    r = client.post(f"{settings.API_V1_STR}/users/me/onboarding/complete")
    assert r.status_code == 401


_PREFLIGHT_CHECKS = [
    {
        "key": "database",
        "status": "ok",
        "components": [{"label": "PostgreSQL", "status": "ok", "latency_ms": 1.0, "detail": None}],
    },
    {
        "key": "pve",
        "status": "fail",
        "components": [
            {"label": "Proxmox VE · lab", "status": "down", "latency_ms": None, "detail": "timeout"}
        ],
    },
    {"key": "gateway", "status": "skipped", "components": []},
]


def test_login_preflight_requires_login(client: TestClient) -> None:
    r = client.get(f"{settings.API_V1_STR}/users/me/preflight")
    assert r.status_code == 401


def test_login_preflight_hides_details_from_non_admin(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    with patch(
        "app.services.monitoring.preflight_service.run_checks",
        return_value=[dict(c) for c in _PREFLIGHT_CHECKS],
    ) as run_checks:
        r = client.get(
            f"{settings.API_V1_STR}/users/me/preflight?refresh=true",
            headers=normal_user_token_headers,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["detailed"] is False
    assert all(check["components"] is None for check in body["checks"])
    assert "timeout" not in r.text
    # 非管理員不能略過快取
    run_checks.assert_called_once_with(use_cache=True)


def test_login_preflight_admin_gets_details(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    with patch(
        "app.services.monitoring.preflight_service.run_checks",
        return_value=[dict(c) for c in _PREFLIGHT_CHECKS],
    ) as run_checks:
        r = client.get(
            f"{settings.API_V1_STR}/users/me/preflight?refresh=true",
            headers=superuser_token_headers,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["detailed"] is True
    pve = next(check for check in body["checks"] if check["key"] == "pve")
    assert pve["components"][0]["detail"] == "timeout"
    run_checks.assert_called_once_with(use_cache=False)


def test_login_preflight_allowed_before_forced_totp_enrollment(
    client: TestClient, db: Session
) -> None:
    """強制兩步驟驗證但尚未綁定的帳號也要能先跑登入檢查（其他端點會 403）。"""
    email = random_email()
    password = random_lower_string()
    user = user_repo.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    user.totp_required = True
    db.add(user)
    db.commit()
    headers = user_authentication_headers(client=client, email=email, password=password)

    with patch(
        "app.services.monitoring.preflight_service.run_checks",
        return_value=[dict(c) for c in _PREFLIGHT_CHECKS],
    ):
        r = client.get(f"{settings.API_V1_STR}/users/me/preflight", headers=headers)
    assert r.status_code == 200
