"""金鑰詳細資料只對擁有者解密，清單仍不包含明文。"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_current_user, get_db
from app.api.routes.ai_api import router
from app.core.security import encrypt_value
from app.exceptions import NotFoundError
from app.models import AIAPICredential
from app.services.llm_gateway import ai_gateway_service


def _credential() -> AIAPICredential:
    return AIAPICredential(
        user_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        base_url="https://api.example.edu",
        api_key_encrypted=encrypt_value("ccai_detail_example"),
        api_key_prefix="ccai_detail",
        api_key_name="detail",
    )


def test_owner_can_read_complete_key_without_exposing_encrypted_value() -> None:
    credential = _credential()
    session = Mock(spec=Session)
    session.get.return_value = credential
    detail = ai_gateway_service.get_credential(
        session=session,
        credential_id=credential.id,
        current_user=SimpleNamespace(id=credential.user_id),
    )
    assert detail.api_key == "ccai_detail_example"
    assert detail.api_key_name == "detail"
    assert "api_key_encrypted" not in detail.model_dump()
    assert "api_key" not in ai_gateway_service._to_credential_public(
        credential
    ).model_dump()


@pytest.mark.parametrize("is_superuser", [False, True])
def test_other_users_and_admins_cannot_decrypt_key(
    monkeypatch: pytest.MonkeyPatch, is_superuser: bool
) -> None:
    credential = _credential()
    session = Mock(spec=Session)
    session.get.return_value = credential
    decrypt = Mock()
    monkeypatch.setattr(ai_gateway_service, "decrypt_value", decrypt)
    with pytest.raises(NotFoundError):
        ai_gateway_service.get_credential(
            session=session,
            credential_id=credential.id,
            current_user=SimpleNamespace(id=uuid.uuid4(), is_superuser=is_superuser),
        )
    decrypt.assert_not_called()


def test_unknown_key_is_not_found() -> None:
    session = Mock(spec=Session)
    session.get.return_value = None
    with pytest.raises(NotFoundError):
        ai_gateway_service.get_credential(
            session=session,
            credential_id=uuid.uuid4(),
            current_user=SimpleNamespace(id=uuid.uuid4()),
        )


def test_detail_endpoint_returns_secret_with_no_store_header() -> None:
    credential = _credential()
    session = Mock(spec=Session)
    session.get.return_value = credential
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=credential.user_id
    )
    with TestClient(app) as client:
        response = client.get(f"/ai-api/credentials/{credential.id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["api_key"] == "ccai_detail_example"
    assert "api_key_encrypted" not in response.json()


def test_detail_endpoint_requires_login() -> None:
    session = Mock(spec=Session)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as client:
        response = client.get(f"/ai-api/credentials/{uuid.uuid4()}")
    assert response.status_code == 401
    session.get.assert_not_called()
