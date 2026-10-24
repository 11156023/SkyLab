"""Registration and approval checks with an isolated in-memory user table."""

from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.api.deps.auth import _check_token_user
from app.core.config import settings
from app.core.security import ALGORITHM, verify_password
from app.exceptions import BadRequestError, ConflictError
from app.models import User
from app.models.registration_policy import RegistrationPolicy
from app.repositories import user as user_repo
from app.schemas.common import TokenPayload
from app.schemas.user import UserRegister, UserUpdate
from app.services.user import auth_service, user_service
from app.services.user.registration_policy_service import get_policy, update_policy


@pytest.fixture
def school_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    User.__table__.create(engine)
    RegistrationPolicy.__table__.create(engine)
    monkeypatch.setattr(settings, "ENABLE_SIGNUP", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "noreply@example.test")
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_signup_accepts_any_email_domain(school_db, monkeypatch):
    monkeypatch.setattr(user_service, "send_email", lambda **kwargs: None)
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@gmail.com")
    )
    assert user.email == "student@gmail.com"
    assert user.registration_status == "pending_email"


def test_signup_cannot_be_enabled_without_mail(school_db, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    assert get_policy(school_db).signup_enabled is False
    with pytest.raises(BadRequestError):
        update_policy(
            school_db,
            signup_enabled=True,
            approval_required=False,
            google_signup_enabled=True,
        )


def test_signup_and_approval_switches_are_independent(school_db):
    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=True,
        google_signup_enabled=False,
    )
    assert policy.signup_enabled is False
    assert policy.approval_required is True
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=False,
        google_signup_enabled=False,
    )
    assert policy.approval_required is False
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=True,
        google_signup_enabled=False,
    )
    assert policy.approval_required is True
    policy = update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    assert policy.signup_enabled is True
    assert policy.approval_required is True


def test_google_signup_switch_is_independent_of_email_signup(school_db):
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=False,
        google_signup_enabled=True,
    )
    assert policy.signup_enabled is False
    assert policy.google_signup_enabled is True
    policy = update_policy(
        school_db,
        signup_enabled=True,
        approval_required=False,
        google_signup_enabled=False,
    )
    assert policy.signup_enabled is True
    assert policy.google_signup_enabled is False


def test_google_signup_can_be_enabled_without_mail(school_db, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=False,
        google_signup_enabled=True,
    )
    assert policy.google_signup_enabled is True


def test_student_email_domain_is_unset_until_configured(school_db):
    assert get_policy(school_db).student_email_domain is None


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        (" @School.EDU.tw ", "school.edu.tw"),
        ("mail.school.edu.tw", "mail.school.edu.tw"),
        ("", None),
        (None, None),
    ],
)
def test_student_email_domain_is_normalized(school_db, value, stored):
    policy = update_policy(
        school_db,
        signup_enabled=False,
        approval_required=False,
        google_signup_enabled=True,
        student_email_domain=value,
    )
    assert policy.student_email_domain == stored


@pytest.mark.parametrize(
    "value",
    ["school", "a@school.edu.tw", "school..edu.tw", "-school.edu.tw", "school edu.tw"],
)
def test_invalid_student_email_domain_is_rejected(school_db, value):
    with pytest.raises(BadRequestError):
        update_policy(
            school_db,
            signup_enabled=False,
            approval_required=False,
            google_signup_enabled=True,
            student_email_domain=value,
        )
    assert get_policy(school_db).student_email_domain is None


def test_google_signup_default_follows_signup_default(school_db, monkeypatch):
    assert get_policy(school_db).google_signup_enabled is True
    monkeypatch.setattr(settings, "ENABLE_SIGNUP", False)
    assert get_policy(school_db).google_signup_enabled is False


def test_signup_stays_inactive_until_recipient_sets_password(school_db, monkeypatch):
    mails = []
    monkeypatch.setattr(user_service, "send_email", lambda **mail: mails.append(mail))
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
    )
    assert user.is_active is False
    assert mails[0]["email_to"] == "student@ntub.edu.tw"
    url = unescape(mails[0]["html_content"].split('href="')[1].split('"')[0])
    token = parse_qs(urlparse(url).fragment)["token"][0]
    with pytest.raises(BadRequestError):
        user_service.confirm_signup(
            session=school_db, token=token, password="weakpassword1"
        )
    assert user.is_active is False
    user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
    )
    new_url = unescape(mails[1]["html_content"].split('href="')[1].split('"')[0])
    new_token = parse_qs(urlparse(new_url).fragment)["token"][0]
    password = "StrongPass123!"
    with pytest.raises(BadRequestError):
        user_service.confirm_signup(session=school_db, token=token, password=password)
    user_service.confirm_signup(session=school_db, token=new_token, password=password)
    school_db.refresh(user)
    assert user.is_active is True
    assert verify_password(password, user.hashed_password)[0] is True
    with pytest.raises(BadRequestError):
        user_service.confirm_signup(
            session=school_db, token=new_token, password=password
        )


@pytest.mark.parametrize("email", ["student@gmail.com", "student@ntub.edu.tw"])
def test_registration_policy_blocks_signup_and_reviews_verified_email(
    school_db, monkeypatch, email
):
    update_policy(
        school_db,
        signup_enabled=False,
        approval_required=True,
        google_signup_enabled=False,
    )
    with pytest.raises(BadRequestError):
        user_service.register_user(
            session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
        )
    assert get_policy(school_db).signup_enabled is False
    assert get_policy(school_db).approval_required is True

    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    mails = []
    monkeypatch.setattr(user_service, "send_email", lambda **mail: mails.append(mail))
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email=email)
    )
    url = unescape(mails[0]["html_content"].split('href="')[1].split('"')[0])
    token = parse_qs(urlparse(url).fragment)["token"][0]
    assert (
        user_service.confirm_signup(
            session=school_db, token=token, password="StrongPass123!"
        )
        == "pending_review"
    )
    school_db.refresh(user)
    assert user.registration_status == "pending_review"
    assert user.is_active is False
    assert user_service.list_pending_registrations(session=school_db).count == 1

    actor = User(email="admin@ntub.edu.tw", hashed_password="unused", role="admin")
    school_db.add(actor)
    school_db.commit()
    monkeypatch.setattr(user_service.audit_service, "log_action", lambda **kwargs: None)
    with pytest.raises(BadRequestError):
        user_service.update_user(
            session=school_db,
            user_id=user.id,
            user_in=UserUpdate(is_active=True),
            current_user_id=actor.id,
        )
    approved = user_service.review_registration(
        session=school_db, user_id=user.id, actor=actor, approve=True
    )
    assert approved.is_active is True
    assert approved.registration_status == "approved"
    assert user_service.list_pending_registrations(session=school_db).count == 0
    with pytest.raises(BadRequestError):
        user_service.review_registration(
            session=school_db, user_id=user.id, actor=actor, approve=True
        )


@pytest.mark.asyncio
async def test_google_first_login_waits_for_review(school_db, monkeypatch):
    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)

    async def tokeninfo(_):
        return {
            "aud": "client-id",
            "email_verified": True,
            "email": "student@ntub.edu.tw",
            "hd": "ntub.edu.tw",
        }

    monkeypatch.setattr(auth_service, "fetch_id_token_info", tokeninfo)
    with pytest.raises(BadRequestError):
        await auth_service.google_login(session=school_db, id_token="google-token")
    user = user_repo.get_user_by_email(session=school_db, email="student@ntub.edu.tw")
    assert user is not None
    assert user.registration_status == "pending_review"
    assert user.is_active is False


def test_rejected_registration_cannot_retry_verification(school_db, monkeypatch):
    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    mails = []
    monkeypatch.setattr(user_service, "send_email", lambda **mail: mails.append(mail))
    monkeypatch.setattr(user_service.audit_service, "log_action", lambda **kwargs: None)
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
    )
    url = unescape(mails[0]["html_content"].split('href="')[1].split('"')[0])
    token = parse_qs(urlparse(url).fragment)["token"][0]
    user_service.confirm_signup(
        session=school_db, token=token, password="StrongPass123!"
    )
    actor = User(email="admin@ntub.edu.tw", hashed_password="unused", role="admin")
    school_db.add(actor)
    school_db.commit()
    rejected = user_service.review_registration(
        session=school_db, user_id=user.id, actor=actor, approve=False
    )
    assert rejected.registration_status == "rejected"
    assert rejected.is_active is False
    with pytest.raises(ConflictError):
        user_service.register_user(
            session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
        )


def test_soft_deleted_registration_cannot_be_confirmed_or_approved(
    school_db, monkeypatch
):
    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=True,
        google_signup_enabled=True,
    )
    mails = []
    monkeypatch.setattr(user_service, "send_email", lambda **mail: mails.append(mail))
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
    )
    url = unescape(mails[0]["html_content"].split('href="')[1].split('"')[0])
    token = parse_qs(urlparse(url).fragment)["token"][0]
    user.deleted_at = datetime.now(timezone.utc)
    school_db.add(user)
    school_db.commit()
    with pytest.raises(BadRequestError):
        user_service.confirm_signup(
            session=school_db, token=token, password="StrongPass123!"
        )

    user.registration_status = "pending_review"
    actor = User(email="admin@ntub.edu.tw", hashed_password="unused", role="admin")
    school_db.add_all([user, actor])
    school_db.commit()
    assert user_service.list_pending_registrations(session=school_db).count == 0
    with pytest.raises(BadRequestError):
        user_service.review_registration(
            session=school_db, user_id=user.id, actor=actor, approve=True
        )


def test_failed_mail_does_not_reserve_address(school_db, monkeypatch):
    def fail_mail(**kwargs):
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(user_service, "send_email", fail_mail)
    with pytest.raises(OSError):
        user_service.register_user(
            session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
        )
    assert (
        user_repo.get_user_by_email(session=school_db, email="student@ntub.edu.tw")
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["student@gmail.com", "student@ntub.edu.tw"])
async def test_google_login_accepts_verified_email_without_hosted_domain(
    school_db, monkeypatch, email
):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)

    async def tokeninfo(_):
        return {
            "aud": "client-id",
            "email_verified": "true",
            "email": email,
        }

    monkeypatch.setattr(auth_service, "fetch_id_token_info", tokeninfo)
    token = await auth_service.google_login(session=school_db, id_token="google-token")
    assert token.access_token


@pytest.mark.asyncio
async def test_google_login_rejects_unverified_email(school_db, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)

    async def tokeninfo(_):
        return {
            "aud": "client-id",
            "email_verified": False,
            "email": "student@gmail.com",
        }

    monkeypatch.setattr(auth_service, "fetch_id_token_info", tokeninfo)
    with pytest.raises(BadRequestError):
        await auth_service.google_login(session=school_db, id_token="google-token")


@pytest.mark.asyncio
async def test_google_login_accepts_school_workspace(school_db, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)

    async def tokeninfo(_):
        return {
            "aud": "client-id",
            "email_verified": "true",
            "email": "student@ntub.edu.tw",
            "hd": "ntub.edu.tw",
        }

    monkeypatch.setattr(auth_service, "fetch_id_token_info", tokeninfo)
    tokens = await auth_service.google_login(session=school_db, id_token="google-token")
    assert tokens.access_token
    user = user_repo.get_user_by_email(session=school_db, email="student@ntub.edu.tw")
    assert user is not None and user.auth_source == "google"


def _google_tokeninfo(monkeypatch, email):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)

    async def tokeninfo(_):
        return {"aud": "client-id", "email_verified": "true", "email": email}

    monkeypatch.setattr(auth_service, "fetch_id_token_info", tokeninfo)


@pytest.mark.asyncio
async def test_google_first_login_blocked_when_google_signup_off(
    school_db, monkeypatch
):
    _google_tokeninfo(monkeypatch, "student@gmail.com")
    update_policy(
        school_db,
        signup_enabled=True,
        approval_required=False,
        google_signup_enabled=False,
    )
    with pytest.raises(BadRequestError):
        await auth_service.google_login(session=school_db, id_token="google-token")
    assert (
        user_repo.get_user_by_email(session=school_db, email="student@gmail.com")
        is None
    )


@pytest.mark.asyncio
async def test_google_first_login_creates_account_with_only_google_signup_on(
    school_db, monkeypatch
):
    _google_tokeninfo(monkeypatch, "student@gmail.com")
    update_policy(
        school_db,
        signup_enabled=False,
        approval_required=False,
        google_signup_enabled=True,
    )
    token = await auth_service.google_login(session=school_db, id_token="google-token")
    assert token.access_token
    user = user_repo.get_user_by_email(session=school_db, email="student@gmail.com")
    assert user is not None and user.auth_source == "google"


def test_expired_signup_link_cannot_activate(school_db, monkeypatch):
    monkeypatch.setattr(user_service, "send_email", lambda **kwargs: None)
    user = user_service.register_user(
        session=school_db, user_in=UserRegister(email="student@ntub.edu.tw")
    )
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "type": "signup_verify",
            "email": str(user.email),
            "ver": user.token_version,
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    with pytest.raises(BadRequestError):
        user_service.confirm_signup(
            session=school_db, token=expired, password="StrongPass123!"
        )
    assert user.is_active is False


def test_existing_account_can_use_password_and_access_token_regardless_of_domain(
    school_db, monkeypatch
):
    user = User(
        email="outsider@other.edu.tw",
        hashed_password=user_service.get_password_hash("StrongPass123!"),
        is_active=True,
    )
    school_db.add(user)
    school_db.commit()
    monkeypatch.setattr(auth_service.audit_service, "log_action", lambda **kwargs: None)
    token = auth_service.login(
        session=school_db, email="outsider@other.edu.tw", password="StrongPass123!"
    )
    assert token.access_token
    assert (
        _check_token_user(
            user, TokenPayload(sub=str(user.id), type="access", ver=user.token_version)
        )
        == user
    )
