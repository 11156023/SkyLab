"""回歸：LDAP 帶進來的非 RFC 信箱不讓使用者 API 500，短的現有密碼可以改。"""

import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.models import User


def _insert_user(db: Session, *, email: str, password: str, **extra) -> User:
    # 直接寫表，繞過 UserCreate（LDAP 自動建帳號與 .env 預設管理員都不經 schema）
    user = User(
        email=email,
        hashed_password=security.get_password_hash(password),
        onboarding_completed=True,
        **extra,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    token = security.create_access_token(
        user.id, expires_delta=timedelta(minutes=5), token_version=user.token_version
    )
    return {"Authorization": f"Bearer {token}"}


def test_ldap_local_domain_email_does_not_break_user_endpoints(
    client: TestClient, db: Session, superuser_token_headers: dict[str, str]
) -> None:
    email = f"test-b18-{uuid.uuid4().hex[:8]}@school.local"
    user = _insert_user(db, email=email, password="irrelevant-pw", auth_source="ldap")
    try:
        me = client.get(f"{settings.API_V1_STR}/users/me", headers=_headers(user))
        assert me.status_code == 200, me.text
        assert me.json()["email"] == email

        listing = client.get(
            f"{settings.API_V1_STR}/users/",
            headers=superuser_token_headers,
            params={"limit": 500},
        )
        assert listing.status_code == 200, listing.text
        body = listing.json()
        if body["count"] <= 500:
            assert email in {row["email"] for row in body["data"]}
    finally:
        db.delete(user)
        db.commit()


def test_short_current_password_can_be_changed(
    client: TestClient, db: Session
) -> None:
    email = f"test-b18-{uuid.uuid4().hex[:8]}@example.com"
    user = _insert_user(db, email=email, password="abc12")
    try:
        response = client.patch(
            f"{settings.API_V1_STR}/users/me/password",
            headers=_headers(user),
            json={"current_password": "abc12", "new_password": "NewPassw0rd!"},
        )
        assert response.status_code == 200, response.text
    finally:
        db.expire_all()
        stored = db.get(User, user.id)
        if stored is not None:
            db.delete(stored)
            db.commit()
