from unittest.mock import patch

from fastapi.testclient import TestClient
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlmodel import Session

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.models import User
from app.repositories.user import create_user, get_user_by_email
from app.schemas import UserCreate
from app.utils import generate_password_reset_token
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_email, random_lower_string


def test_get_access_token(client: TestClient) -> None:
    login_data = {
        "username": settings.FIRST_SUPERUSER,
        "password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    tokens = r.json()
    assert r.status_code == 200
    assert "access_token" in tokens
    assert tokens["access_token"]


def test_get_access_token_incorrect_password(client: TestClient) -> None:
    login_data = {
        "username": settings.FIRST_SUPERUSER,
        "password": "incorrect",
    }
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    assert r.status_code == 400


def test_recovery_password(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    with (
        patch("app.core.config.settings.SMTP_HOST", "smtp.example.com"),
        patch("app.core.config.settings.SMTP_USER", "admin@example.com"),
    ):
        email = "test@example.com"
        r = client.post(
            f"{settings.API_V1_STR}/password-recovery/{email}",
            headers=normal_user_token_headers,
        )
        assert r.status_code == 200
        assert r.json() == {
            "message": "If that email is registered, we sent a password recovery link"
        }


def test_recovery_password_user_not_exits(
    client: TestClient, normal_user_token_headers: dict[str, str]
) -> None:
    email = "jVgQr@example.com"
    r = client.post(
        f"{settings.API_V1_STR}/password-recovery/{email}",
        headers=normal_user_token_headers,
    )
    # Should return 200 with generic message to prevent email enumeration attacks
    assert r.status_code == 200
    assert r.json() == {
        "message": "If that email is registered, we sent a password recovery link"
    }


def test_reset_password(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    new_password = random_lower_string()

    user_create = UserCreate(
        email=email,
        full_name="Test User",
        password=password,
        is_active=True,
    )
    user = create_user(session=db, user_create=user_create)
    db.commit()
    db.refresh(user)
    token = generate_password_reset_token(email=email)
    headers = user_authentication_headers(client=client, email=email, password=password)
    data = {"new_password": new_password, "token": token}

    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        headers=headers,
        json=data,
    )

    assert r.status_code == 200
    assert r.json() == {"message": "Password updated successfully"}

    db.refresh(user)
    verified, _ = verify_password(new_password, user.hashed_password)
    assert verified


def test_reset_password_invalid_token(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    data = {"new_password": "changethis", "token": "invalid"}
    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        headers=superuser_token_headers,
        json=data,
    )
    response = r.json()

    assert "detail" in response
    assert r.status_code == 400
    assert response["detail"] == "無效的 token"


def test_login_with_bcrypt_password_upgrades_to_argon2(
    client: TestClient, db: Session
) -> None:
    """Test that logging in with a bcrypt password hash upgrades it to argon2."""
    email = random_email()
    password = random_lower_string()

    # Create a bcrypt hash directly (simulating legacy password)
    bcrypt_hasher = BcryptHasher()
    bcrypt_hash = bcrypt_hasher.hash(password)
    assert bcrypt_hash.startswith("$2")  # bcrypt hashes start with $2

    user = User(email=email, hashed_password=bcrypt_hash, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    assert user.hashed_password.startswith("$2")

    login_data = {"username": email, "password": password}
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    assert r.status_code == 200
    tokens = r.json()
    assert "access_token" in tokens

    db.refresh(user)

    # Verify the hash was upgraded to argon2
    assert user.hashed_password.startswith("$argon2")

    verified, updated_hash = verify_password(password, user.hashed_password)
    assert verified
    # Should not need another update since it's already argon2
    assert updated_hash is None


def test_login_with_argon2_password_keeps_hash(client: TestClient, db: Session) -> None:
    """Test that logging in with an argon2 password hash does not update it."""
    email = random_email()
    password = random_lower_string()

    # Create an argon2 hash (current default)
    argon2_hash = get_password_hash(password)
    assert argon2_hash.startswith("$argon2")

    # Create user with argon2 hash
    user = User(email=email, hashed_password=argon2_hash, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    original_hash = user.hashed_password

    login_data = {"username": email, "password": password}
    r = client.post(f"{settings.API_V1_STR}/login/access-token", data=login_data)
    assert r.status_code == 200
    tokens = r.json()
    assert "access_token" in tokens

    db.refresh(user)

    assert user.hashed_password == original_hash
    assert user.hashed_password.startswith("$argon2")


def _mock_google_tokeninfo(monkeypatch, data: dict[str, str]) -> None:
    async def fake_fetch_id_token_info(id_token: str) -> dict[str, str]:
        return data

    monkeypatch.setattr(
        "app.services.user.auth_service.fetch_id_token_info", fake_fetch_id_token_info
    )


def test_google_login_is_case_insensitive_for_existing_user(
    client: TestClient, db: Session, monkeypatch
) -> None:
    email = "MixedCaseGoogle@example.com"
    user_create = UserCreate(
        email=email,
        full_name="Google User",
        password=random_lower_string(),
        is_active=True,
    )
    create_user(session=db, user_create=user_create)
    db.commit()

    with patch("app.services.user.auth_service.settings.GOOGLE_CLIENT_ID", "google-client"):
        _mock_google_tokeninfo(
            monkeypatch,
            {
                "aud": "google-client",
                "email": email.lower(),
                "email_verified": "true",
            },
        )
        r = client.post(
            f"{settings.API_V1_STR}/login/google",
            json={"id_token": "valid-google-token"},
        )

    assert r.status_code == 200
    tokens = r.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]


def test_google_login_unregistered_email_has_clear_error(
    client: TestClient, monkeypatch
) -> None:
    with (
        patch(
            "app.services.user.auth_service.settings.GOOGLE_CLIENT_ID", "google-client"
        ),
        patch("app.services.user.auth_service.settings.ENABLE_SIGNUP", True),
    ):
        _mock_google_tokeninfo(
            monkeypatch,
            {
                "aud": "google-client",
                "email": "missing-google-user@example.com",
                "email_verified": "true",
            },
        )
        r = client.post(
            f"{settings.API_V1_STR}/login/google",
            json={"id_token": "valid-google-token"},
        )

    assert r.status_code == 400
    assert r.json()["detail"] == "此 Google 帳號尚未註冊"


def test_google_login_auto_registers_edu_tw_student(
    client: TestClient, db: Session, monkeypatch
) -> None:
    email = f"{random_lower_string()}@campus.edu.tw"
    with (
        patch(
            "app.services.user.auth_service.settings.GOOGLE_CLIENT_ID", "google-client"
        ),
        patch("app.services.user.auth_service.settings.ENABLE_SIGNUP", True),
    ):
        _mock_google_tokeninfo(
            monkeypatch,
            {
                "aud": "google-client",
                "email": email,
                "email_verified": "true",
                "name": "First Login Student",
                "picture": "https://example.com/avatar.png",
            },
        )
        r = client.post(
            f"{settings.API_V1_STR}/login/google",
            json={"id_token": "valid-google-token"},
        )

    assert r.status_code == 200
    assert r.json()["access_token"]
    user = get_user_by_email(session=db, email=email)
    assert user is not None
    assert user.role.value == "student"
    assert user.is_active is True
    assert user.auth_source == "google"
    assert user.full_name == "First Login Student"
    assert user.avatar_url == "https://example.com/avatar.png"


def test_google_login_does_not_auto_register_edu_when_signup_disabled(
    client: TestClient, db: Session, monkeypatch
) -> None:
    email = f"{random_lower_string()}@university.edu"
    with (
        patch(
            "app.services.user.auth_service.settings.GOOGLE_CLIENT_ID", "google-client"
        ),
        patch("app.services.user.auth_service.settings.ENABLE_SIGNUP", False),
    ):
        _mock_google_tokeninfo(
            monkeypatch,
            {
                "aud": "google-client",
                "email": email,
                "email_verified": "true",
            },
        )
        r = client.post(
            f"{settings.API_V1_STR}/login/google",
            json={"id_token": "valid-google-token"},
        )

    assert r.status_code == 400
    assert get_user_by_email(session=db, email=email) is None


def test_google_login_keeps_existing_ldap_account_source(
    client: TestClient, db: Session, monkeypatch
) -> None:
    email = f"{random_lower_string()}@campus.edu.tw"
    user = create_user(
        session=db,
        user_create=UserCreate(
            email=email,
            full_name="LDAP Student",
            password=random_lower_string(),
            is_active=True,
        ),
    )
    user.auth_source = "ldap"
    db.add(user)
    db.commit()

    with patch(
        "app.services.user.auth_service.settings.GOOGLE_CLIENT_ID", "google-client"
    ):
        _mock_google_tokeninfo(
            monkeypatch,
            {
                "aud": "google-client",
                "email": email,
                "email_verified": "true",
            },
        )
        r = client.post(
            f"{settings.API_V1_STR}/login/google",
            json={"id_token": "valid-google-token"},
        )

    assert r.status_code == 200
    db.refresh(user)
    assert user.auth_source == "ldap"
