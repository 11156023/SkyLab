"""AI API 期限限制：隔離資料庫驗證申請、審核與 HTTP 契約。"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deps import get_current_user, get_db
from app.api.deps.auth import get_current_ai_api_reviewer
from app.api.routes.ai_api import router
from app.core.security import encrypt_value
from app.exceptions import AppError, BadRequestError
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AuditLog,
    User,
    UserRole,
)
from app.schemas import AIAPIRequestCreate
from app.services.llm_gateway import ai_gateway_service


@pytest.fixture
def isolated_session(monkeypatch: pytest.MonkeyPatch) -> Generator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            AIAPIRequest.__table__,
            AIAPICredential.__table__,
            AuditLog.__table__,
        ],
    )
    monkeypatch.setattr(
        ai_gateway_service.ai_api_settings,
        "ai_api_public_base_url",
        "https://campus.example.test",
    )
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _user(session: Session, role: UserRole) -> User:
    user = User(
        email=f"duration-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-used",
        role=role,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture
def api_client(
    isolated_session: Session,
) -> Generator[tuple[TestClient, User]]:
    applicant = _user(isolated_session, UserRole.student)
    reviewer = _user(isolated_session, UserRole.admin)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: isolated_session
    app.dependency_overrides[get_current_user] = lambda: applicant
    app.dependency_overrides[get_current_ai_api_reviewer] = lambda: reviewer

    @app.exception_handler(AppError)
    async def handle_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message}
        )

    with TestClient(app) as client:
        yield client, applicant


def _assert_no_writes(session: Session) -> None:
    assert session.exec(select(AIAPIRequest)).all() == []
    assert session.exec(select(AIAPICredential)).all() == []
    assert session.exec(select(AuditLog)).all() == []


@pytest.mark.parametrize(
    ("role", "duration"),
    [(role, duration) for role in UserRole for duration in ("1d", "7d", "30d")]
    + [
        (UserRole.student, "90d"),
        (UserRole.teacher, "never"),
        (UserRole.admin, "never"),
    ],
)
def test_allowed_request_is_approved_with_expiry_from_approval(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    monkeypatch: pytest.MonkeyPatch,
    role: UserRole,
    duration: str,
) -> None:
    client, applicant = api_client
    applicant.role = role
    isolated_session.commit()
    response = client.post(
        "/ai-api/requests",
        json={"purpose": "Test the AI API duration policy", "duration": duration},
    )
    assert response.status_code == 200
    request_id = uuid.UUID(response.json()["id"])
    request = isolated_session.get(AIAPIRequest, request_id)
    assert request is not None
    # 等候審核的時間不能消耗金鑰效期。
    request.created_at = datetime(2026, 10, 1, tzinfo=UTC)
    isolated_session.commit()
    approved_at = datetime(2026, 10, 4, 8, 15, tzinfo=UTC)
    monkeypatch.setattr(ai_gateway_service, "get_datetime_utc", lambda: approved_at)
    response = client.post(
        f"/ai-api/requests/{request_id}/review", json={"status": "approved"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    [credential] = isolated_session.exec(select(AIAPICredential)).all()
    if duration == "never":
        assert credential.expires_at is None
    else:
        assert credential.expires_at is not None
        assert credential.expires_at.replace(tzinfo=UTC) == approved_at + timedelta(
            days=int(duration[:-1])
        )


@pytest.mark.parametrize("duration", ["never", None])
def test_student_permanent_or_omitted_duration_is_rejected_without_writes(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    duration: str | None,
) -> None:
    client, _ = api_client
    payload = {"purpose": "Student requests must have a finite duration"}
    if duration is not None:
        payload["duration"] = duration
    response = client.post("/ai-api/requests", json=payload)
    assert response.status_code == 400
    assert "90" in response.json()["detail"]
    _assert_no_writes(isolated_session)


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.admin])
def test_non_student_omitted_duration_remains_permanent(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    role: UserRole,
) -> None:
    client, applicant = api_client
    applicant.role = role
    isolated_session.commit()
    response = client.post(
        "/ai-api/requests",
        json={"purpose": "Preserve the existing non-student default"},
    )
    assert response.status_code == 200
    assert response.json()["duration"] == "never"


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.admin])
def test_non_student_options_are_not_extended_to_ninety_days(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    role: UserRole,
) -> None:
    client, applicant = api_client
    applicant.role = role
    isolated_session.commit()
    response = client.post(
        "/ai-api/requests",
        json={"purpose": "Keep the existing non-student options", "duration": "90d"},
    )
    assert response.status_code == 400
    _assert_no_writes(isolated_session)


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("duration", ["1h", "2h", "91d", "180d", "", None])
def test_invalid_http_duration_is_rejected_without_writes(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    role: UserRole,
    duration: str | None,
) -> None:
    client, applicant = api_client
    applicant.role = role
    isolated_session.commit()
    response = client.post(
        "/ai-api/requests",
        json={"purpose": "Reject invalid key validity periods", "duration": duration},
    )
    assert response.status_code == 422
    _assert_no_writes(isolated_session)


@pytest.mark.parametrize(
    ("role", "duration"),
    [(role, "1h") for role in UserRole] + [(UserRole.student, "never")],
)
def test_service_rejects_disallowed_duration_even_if_schema_is_bypassed(
    isolated_session: Session, role: UserRole, duration: str
) -> None:
    applicant = _user(isolated_session, role)
    with pytest.raises(BadRequestError):
        ai_gateway_service.create_request(
            session=isolated_session,
            user=applicant,
            request_in=AIAPIRequestCreate.model_construct(
                purpose="Validate direct service calls",
                api_key_name="test",
                duration=duration,
            ),
        )
    _assert_no_writes(isolated_session)


@pytest.mark.parametrize(
    ("role", "duration"),
    [(role, "1h") for role in UserRole]
    + [(UserRole.student, "never"), (UserRole.student, "180d")],
)
def test_legacy_pending_request_cannot_be_approved_but_can_be_rejected(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    role: UserRole,
    duration: str,
) -> None:
    client, applicant = api_client
    applicant.role = role
    request = AIAPIRequest(
        user_id=applicant.id, purpose="Legacy pending duration", duration=duration
    )
    isolated_session.add(request)
    isolated_session.commit()
    response = client.post(
        f"/ai-api/requests/{request.id}/review", json={"status": "approved"}
    )
    assert response.status_code == 400
    # 即使呼叫方在失敗後提交，也不應留下半套審核狀態。
    isolated_session.commit()
    isolated_session.refresh(request)
    assert request.status == AIAPIRequestStatus.pending
    assert request.reviewer_id is None
    assert request.reviewed_at is None
    assert isolated_session.exec(select(AIAPICredential)).all() == []
    assert isolated_session.exec(select(AuditLog)).all() == []
    response = client.post(
        f"/ai-api/requests/{request.id}/review", json={"status": "rejected"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert isolated_session.exec(select(AIAPICredential)).all() == []


@pytest.mark.parametrize(
    ("initial_role", "current_role", "duration"),
    [
        (UserRole.teacher, UserRole.student, "never"),
        (UserRole.student, UserRole.teacher, "90d"),
    ],
)
def test_approval_uses_current_applicant_role_instead_of_reviewer_role(
    isolated_session: Session,
    api_client: tuple[TestClient, User],
    initial_role: UserRole,
    current_role: UserRole,
    duration: str,
) -> None:
    client, applicant = api_client
    applicant.role = initial_role
    isolated_session.commit()
    response = client.post(
        "/ai-api/requests",
        json={
            "purpose": "Check the applicant role again at approval",
            "duration": duration,
        },
    )
    assert response.status_code == 200
    applicant.role = current_role
    isolated_session.commit()
    response = client.post(
        f"/ai-api/requests/{response.json()['id']}/review", json={"status": "approved"}
    )
    assert response.status_code == 400
    assert isolated_session.exec(select(AIAPICredential)).all() == []


def test_existing_student_permanent_key_can_still_be_rotated_without_shortening(
    isolated_session: Session, api_client: tuple[TestClient, User]
) -> None:
    client, applicant = api_client
    legacy_request = AIAPIRequest(
        user_id=applicant.id,
        purpose="Existing approved permanent request",
        duration="never",
        status=AIAPIRequestStatus.approved,
    )
    isolated_session.add(legacy_request)
    isolated_session.commit()
    old = AIAPICredential(
        user_id=applicant.id,
        request_id=legacy_request.id,
        api_key_name="legacy",
        base_url="https://campus.example.test",
        api_key_prefix="ccai_old_duration",
        api_key_encrypted=encrypt_value("ccai_old_duration_example"),
        expires_at=None,
    )
    isolated_session.add(old)
    isolated_session.commit()
    response = client.post(f"/ai-api/credentials/{old.id}/rotate")
    assert response.status_code == 200
    assert response.json()["expires_at"] is None
    isolated_session.refresh(old)
    assert old.expires_at is None
    assert old.revoked_at is not None
    assert len(isolated_session.exec(select(AIAPICredential)).all()) == 2
