"""Deleted accounts stop accepting work while retained IDs allow late AI accounting."""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select, update

from app.api.deps import auth
from app.api.deps.ai_api_key import get_current_user_by_ai_api_key
from app.api.deps.database import get_db
from app.api.routes import ai_proxy, users
from app.core import security
from app.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.infrastructure.ldap import LdapUserInfo
from app.main import app as campus_app
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    AuditLog,
    FirewallLayout,
    User,
    UserRole,
    get_datetime_utc,
)
from app.repositories import user as user_repo
from app.schemas import (
    AIAPIRequestCreate,
    AIAPIRequestReview,
    TokenPayload,
    UserCreate,
    UserRegister,
    UserUpdate,
)
from app.services.llm_gateway import ai_gateway_service, relay_service
from app.services.user import auth_service, ldap_auth_service, user_service


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(relay_service, "engine", engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def account(db: Session, *, admin: bool = False) -> User:
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(
            email=f"{uuid.uuid4().hex}@example.com",
            password="StrongPassword123!",
            role=UserRole.admin if admin else UserRole.student,
        ),
    )
    db.commit()
    db.refresh(user)
    return user


def credential(db: Session, user: User) -> tuple[str, AIAPICredential]:
    request = AIAPIRequest(
        user_id=user.id,
        purpose="Retained accounting",
        duration="30d",
        status=AIAPIRequestStatus.approved,
    )
    db.add(request)
    db.flush()
    key = "ccai_" + uuid.uuid4().hex
    item = AIAPICredential(
        user_id=user.id,
        request_id=request.id,
        base_url="https://example.test",
        api_key_prefix=key[:16],
        api_key_encrypted=security.encrypt_value(key),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return key, item


@pytest.mark.parametrize("self_delete", [False, True])
@pytest.mark.parametrize("status", ["success", "error", "cancelled"])
@pytest.mark.parametrize("stream", [False, True])
def test_delete_retains_history_and_accepts_late_ai_usage(
    db: Session,
    self_delete: bool,
    status: str,
    stream: bool,
) -> None:
    owner, admin = account(db), account(db, admin=True)
    key, item = credential(db, owner)
    user_id, credential_id, request_id = owner.id, item.id, item.request_id
    db.add(
        FirewallLayout(
            user_id=user_id,
            node_type="gateway",
            created_at=get_datetime_utc(),
            updated_at=get_datetime_utc(),
        )
    )
    db.add(
        AuditLog(
            user_id=user_id,
            action="login_success",
            details="Retain attribution",
            created_at=get_datetime_utc(),
        )
    )
    db.commit()
    ai_gateway_service.record_usage(
        session=db,
        user_id=user_id,
        credential_id=credential_id,
        model_name="model",
        request_type="chat_completion",
        input_tokens=2,
    )
    historical_usage_id = db.exec(select(AIAPIUsage)).one().id
    # Authentication releases its read transaction; another session deletes the account.
    with Session(db.get_bind()) as accepted_session:
        accepted_user, accepted_key = get_current_user_by_ai_api_key(
            session=accepted_session, authorization=f"Bearer {key}"
        )
        if self_delete:
            user_service.delete_me(session=db, current_user=owner)
        else:
            user_service.delete_user(session=db, user_id=user_id, current_user=admin)
        with pytest.raises(HTTPException) as denied:
            with Session(db.get_bind()) as new_session:
                get_current_user_by_ai_api_key(
                    session=new_session, authorization=f"Bearer {key}"
                )
        assert denied.value.status_code == 401
        # The real safe recorder uses its independent DB session, as after relay completion.
        relay_service.record_usage_safely(
            user_id=accepted_user.id,
            credential_id=accepted_key.id,
            model_name="model",
            request_type="chat_completion",
            request_id="late-call",
            input_tokens=3,
            output_tokens=4,
            record_status=status,
            stream=stream,
        )
    ai_gateway_service.record_template_call(
        session=db,
        user_id=user_id,
        call_type="chat",
        model_name="model",
        input_tokens=5,
    )
    db.expire_all()
    stored = db.get(User, user_id)
    assert stored is not None and stored.deleted_at is not None
    assert not stored.is_active and stored.token_version == 1
    assert db.get(AIAPICredential, credential_id).revoked_at is not None
    assert db.get(AIAPIRequest, request_id) is not None
    assert db.get(AIAPIUsage, historical_usage_id) is not None
    late = db.exec(select(AIAPIUsage).where(AIAPIUsage.request_id == "late-call")).one()
    assert (late.input_tokens, late.output_tokens, late.status, late.stream) == (
        3,
        4,
        status,
        stream,
    )
    assert len(db.exec(select(AIAPIUsage)).all()) == 3
    assert db.exec(select(FirewallLayout)).one().user_id == user_id
    assert all(log.user_id is not None for log in db.exec(select(AuditLog)).all())
    before = stored.token_version
    user_service.delete_user(session=db, user_id=user_id, current_user=admin)
    assert db.get(User, user_id).token_version == before


def test_deleted_account_cannot_be_reactivated_or_registered_again(
    db: Session, monkeypatch
) -> None:
    owner, admin = account(db), account(db, admin=True)
    email = owner.email
    with Session(db.get_bind()) as stale_session:
        stale = stale_session.get(User, owner.id)
        user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
        with pytest.raises(NotFoundError):
            user_repo.update_user(
                session=stale_session, db_user=stale, user_in=UserUpdate(is_active=True)
            )
    with pytest.raises(NotFoundError):
        user_service.update_user(
            session=db,
            user_id=owner.id,
            current_user_id=admin.id,
            user_in=UserUpdate(is_active=True),
        )
    with pytest.raises(IntegrityError):
        db.exec(update(User).where(User.id == owner.id).values(is_active=True))
        db.commit()
    db.rollback()
    monkeypatch.setattr(user_service.settings, "ENABLE_SIGNUP", True)
    with pytest.raises(ConflictError):
        user_service.register_user(
            session=db, user_in=UserRegister(email=email, password="StrongPassword123!")
        )
    with pytest.raises(ConflictError):
        user_service.create_user(
            session=db,
            user_in=UserCreate(email=email, password="StrongPassword123!"),
            current_user_id=admin.id,
        )


def test_deleted_account_hidden_from_user_management_but_preserved_for_audit(
    db: Session,
) -> None:
    owner, admin = account(db), account(db, admin=True)
    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    listed = user_service.list_users(session=db)
    assert listed.count == 1 and [user.id for user in listed.data] == [admin.id]
    with pytest.raises(NotFoundError):
        user_service.get_user_by_id(session=db, user_id=owner.id, current_user=admin)
    assert user_repo.get_user_by_email(session=db, email=owner.email).id == owner.id


def test_deleted_account_cannot_receive_new_ai_credentials(db: Session) -> None:
    owner, admin = account(db), account(db, admin=True)
    pending = AIAPIRequest(user_id=owner.id, purpose="Pending", duration="30d")
    db.add(pending)
    db.commit()
    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    with pytest.raises(NotFoundError):
        ai_gateway_service.review_request(
            session=db,
            request_id=pending.id,
            review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
            reviewer=admin,
        )
    assert db.exec(select(AIAPICredential)).all() == []
    assert db.get(AIAPIRequest, pending.id).status == AIAPIRequestStatus.pending
    with pytest.raises(NotFoundError):
        ai_gateway_service.create_request(
            session=db, user=owner,
            request_in=AIAPIRequestCreate(purpose="After deletion", duration="30d"),
        )


def test_delete_revokes_rotated_and_other_keys_without_rewriting_history(db: Session) -> None:
    owner, admin = account(db), account(db, admin=True)
    _, old = credential(db, owner)
    rotated = ai_gateway_service.rotate_credential(
        session=db, credential_id=old.id, current_user=owner
    )
    revoked_at = db.get(AIAPICredential, old.id).revoked_at
    _, other = credential(db, owner)
    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    db.expire_all()
    assert db.get(AIAPICredential, old.id).revoked_at == revoked_at
    assert db.get(AIAPICredential, rotated.id).revoked_at is not None
    assert db.get(AIAPICredential, other.id).revoked_at is not None
    with pytest.raises(NotFoundError):
        ai_gateway_service.rotate_credential(
            session=db, credential_id=rotated.id, current_user=admin
        )
    assert len(db.exec(select(AIAPICredential)).all()) == 3


def test_deleted_account_blocks_password_google_ldap_and_recovery(
    db: Session, monkeypatch
) -> None:
    owner, admin = account(db), account(db, admin=True)
    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    with pytest.raises(BadRequestError):
        auth_service.login(session=db, email=owner.email, password="StrongPassword123!")
    with pytest.raises(BadRequestError):
        auth_service._complete_google_login(db, owner.email, {})
    monkeypatch.setattr(
        ldap_auth_service, "get_ldap_config", lambda **_: SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(
        ldap_auth_service.ldap_client,
        "authenticate_user",
        lambda *_: LdapUserInfo(
            dn="uid=deleted", email=owner.email, full_name="Deleted", groups=[]
        ),
    )
    with pytest.raises(BadRequestError):
        ldap_auth_service.login_ldap(
            session=db, username="deleted", password="password"
        )
    sent = []
    monkeypatch.setattr(
        auth_service, "send_email", lambda **kwargs: sent.append(kwargs)
    )
    auth_service.recover_password(session=db, email=owner.email)
    assert sent == []


@pytest.mark.asyncio
async def test_deleted_account_blocks_access_and_refresh_tokens(
    db: Session, monkeypatch
) -> None:
    owner, admin = account(db), account(db, admin=True)
    refresh = security.create_refresh_token(owner.id, expires_delta=timedelta(days=1))
    user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    with pytest.raises(AuthenticationError):
        auth._check_token_user(owner, TokenPayload(sub=str(owner.id), ver=0))
    import app.infrastructure.redis as redis_module

    async def no_redis():
        return None

    async def not_revoked(*_):
        return False

    monkeypatch.setattr(redis_module, "get_redis", no_redis)
    monkeypatch.setattr(redis_module, "is_jti_revoked", not_revoked)
    # SQLite's UUID binder requires UUID objects, unlike the PostgreSQL driver.
    original_get = db.get
    monkeypatch.setattr(
        db, "get", lambda model, key: original_get(model, uuid.UUID(key))
    )
    with pytest.raises(AuthenticationError):
        await auth_service.refresh_access_token(session=db, refresh_token=refresh)


@pytest.mark.parametrize("self_delete", [False, True])
def test_delete_http_routes_and_reactivation_are_enforced(
    db: Session, self_delete: bool
) -> None:
    owner, admin = account(db), account(db, admin=True)
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1")
    app.exception_handlers.update(campus_app.exception_handlers)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[auth.get_current_user] = lambda: (
        owner if self_delete else admin
    )
    client = TestClient(app)
    url = "/api/v1/users/me" if self_delete else f"/api/v1/users/{owner.id}"
    response = client.delete(url)
    assert response.status_code == 200, response.text
    assert db.get(User, owner.id).deleted_at is not None
    app.dependency_overrides[auth.get_current_user] = lambda: admin
    assert (
        client.patch(f"/api/v1/users/{owner.id}", json={"is_active": True}).status_code
        == 404
    )
    listing = client.get("/api/v1/users/").json()
    assert listing["count"] == 1 and listing["data"][0]["id"] == str(admin.id)


def test_delete_rolls_back_account_and_credentials_if_audit_fails(
    db: Session, monkeypatch
) -> None:
    owner, admin = account(db), account(db, admin=True)
    _, item = credential(db, owner)

    def fail(**_):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(user_service.audit_service, "log_action", fail)
    with pytest.raises(RuntimeError, match="audit failed"):
        user_service.delete_user(session=db, user_id=owner.id, current_user=admin)
    assert db.get(User, owner.id).is_active is True
    assert db.get(User, owner.id).deleted_at is None
    assert db.get(AIAPICredential, item.id).revoked_at is None


def test_http_auth_rejects_old_access_token_and_ai_key_after_self_delete(db: Session, monkeypatch) -> None:
    owner = account(db)
    key, _ = credential(db, owner)
    access = security.create_access_token(owner.id, expires_delta=timedelta(minutes=5))
    app = FastAPI()
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(ai_proxy.router, prefix="/api/v1")
    app.exception_handlers.update(campus_app.exception_handlers)
    app.dependency_overrides[get_db] = lambda: db

    async def no_redis():
        return None

    async def not_revoked(*_):
        return False

    monkeypatch.setattr(auth, "get_redis", no_redis)
    monkeypatch.setattr(auth, "is_jti_revoked", not_revoked)
    # Keep real token validation and the DB lookup; adapt only SQLite UUID binding.
    original_load = auth._load_user_and_release
    monkeypatch.setattr(
        auth, "_load_user_and_release", lambda session, key: original_load(session, uuid.UUID(key))
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {access}"}
    assert client.get("/api/v1/users/me", headers=headers).status_code == 200
    response = client.delete("/api/v1/users/me", headers=headers)
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401
    denied = client.post(
        "/api/v1/ai-proxy/chat/completions", headers={"Authorization": f"Bearer {key}"},
        json={"model": "model", "messages": [{"role": "user", "content": "test"}]},
    )
    assert denied.status_code == 401
