from __future__ import annotations

import uuid

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AIAPICredential, AIAPIRequest, User, UserRole
from app.services.llm_gateway import ai_gateway_service
from scripts.ensure_ai_api_smoke_credential import (
    SMOKE_KEY_NAME,
    SMOKE_KEY_PURPOSE,
    SMOKE_KEY_RATE_LIMIT,
    ensure_ai_api_smoke_credential,
)


@pytest.fixture
def isolated_session(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            AIAPIRequest.__table__,
            AIAPICredential.__table__,
        ],
    )
    monkeypatch.setattr(
        ai_gateway_service.audit_service,
        "log_action",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        ai_gateway_service.ai_api_settings,
        "ai_api_public_base_url",
        "https://campus.example.test/api/v1",
    )
    with Session(engine) as session:
        yield session


def test_smoke_credential_is_created_through_request_flow_and_reused(
    isolated_session: Session,
) -> None:
    owner = User(
        email=f"ai-api-smoke-{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="not-used-by-this-test",
        role=UserRole.admin,
    )
    isolated_session.add(owner)
    isolated_session.commit()
    isolated_session.refresh(owner)

    first_key = ensure_ai_api_smoke_credential(isolated_session)
    second_key = ensure_ai_api_smoke_credential(isolated_session)

    requests = list(
        isolated_session.exec(
            select(AIAPIRequest)
            .where(AIAPIRequest.user_id == owner.id)
            .where(AIAPIRequest.purpose == SMOKE_KEY_PURPOSE)
        ).all()
    )
    credentials = list(
        isolated_session.exec(
            select(AIAPICredential)
            .where(AIAPICredential.user_id == owner.id)
            .where(AIAPICredential.api_key_name == SMOKE_KEY_NAME)
        ).all()
    )

    assert first_key.startswith("ccai_")
    assert second_key == first_key
    assert len(requests) == 1
    assert len(credentials) == 1
    assert requests[0].rate_limit == SMOKE_KEY_RATE_LIMIT
    assert credentials[0].rate_limit == SMOKE_KEY_RATE_LIMIT
