import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.features.ai.config import settings as ai_api_settings
from app.models import AIAPIUsage, get_datetime_utc
from app.repositories import user as user_repo
from app.schemas import UserCreate
from tests.utils.user import user_authentication_headers
from tests.utils.utils import random_lower_string


def _unique_email(email: str) -> str:
    """Make email unique per test run so repeated local runs don't collide."""
    local, _, domain = email.partition("@")
    return f"{local}-{uuid.uuid4().hex[:8]}@{domain or 'example.com'}"


def _create_test_user_headers(client: TestClient, db: Session, email: str) -> dict[str, str]:
    email = _unique_email(email)
    password = random_lower_string()
    user_repo.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password),
    )
    db.commit()
    return user_authentication_headers(client=client, email=email, password=password)


def _create_and_approve_ai_api_request(
    *,
    client: TestClient,
    user_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    purpose: str,
    api_key_name: str,
    duration: str = "never",
) -> str:
    create_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests",
        headers=user_headers,
        json={
            "purpose": purpose,
            "api_key_name": api_key_name,
            "duration": duration,
        },
    )
    assert create_response.status_code == 200
    request_id = create_response.json()["id"]

    review_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests/{request_id}/review",
        headers=superuser_token_headers,
        json={"status": "approved"},
    )
    assert review_response.status_code == 200
    return request_id


def test_ai_api_request_review_flow(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    user_headers = _create_test_user_headers(client, db, "ai-api-user@example.com")
    create_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests",
        headers=user_headers,
        json={"purpose": "Use AI API for course project integration testing."},
    )
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["status"] == "pending"
    assert created["purpose"] == "Use AI API for course project integration testing."

    my_requests_response = client.get(
        f"{settings.API_V1_STR}/ai-api/requests/my",
        headers=user_headers,
    )
    assert my_requests_response.status_code == 200
    assert my_requests_response.json()["count"] >= 1

    review_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests/{created['id']}/review",
        headers=superuser_token_headers,
        json={"status": "approved", "review_comment": "Approved for MVP testing."},
    )
    assert review_response.status_code == 200
    reviewed = review_response.json()
    assert reviewed["status"] == "approved"
    assert reviewed["review_comment"] == "Approved for MVP testing."

    credentials_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials/my",
        headers=user_headers,
    )
    assert credentials_response.status_code == 200
    payload = credentials_response.json()
    assert payload["count"] >= 1
    latest = payload["data"][0]
    assert latest["request_id"] == created["id"]
    assert latest["base_url"] == ai_api_settings.resolved_public_base_url
    # Per-user API keys are generated on approval (prefix "ccai_") rather than
    # echoing the upstream shared key.
    assert isinstance(latest["api_key"], str) and latest["api_key"].startswith("ccai_")
    assert latest["api_key_prefix"] == latest["api_key"][:8]


def test_ai_api_proxy_usage_my_uses_jwt_auth(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    email = _unique_email("ai-api-usage-user@example.com")
    password = random_lower_string()
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(email=email, password=password),
    )
    db.commit()
    db.refresh(user)
    user_headers = user_authentication_headers(
        client=client,
        email=email,
        password=password,
    )

    _create_and_approve_ai_api_request(
        client=client,
        user_headers=user_headers,
        superuser_token_headers=superuser_token_headers,
        purpose="Track AI API proxy usage in the user dashboard.",
        api_key_name="usage-key",
    )

    credentials_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials/my",
        headers=user_headers,
    )
    assert credentials_response.status_code == 200
    credential_id = credentials_response.json()["data"][0]["id"]

    now = get_datetime_utc()
    db.add(
        AIAPIUsage(
            user_id=user.id,
            credential_id=uuid.UUID(credential_id),
            model_name="Qwen/Qwen3-14B-FP8",
            request_type="chat_completion",
            input_tokens=123,
            output_tokens=45,
            status="success",
            created_at=now - timedelta(days=1),
        )
    )
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/ai-api/usage/proxy/my",
        headers=user_headers,
        params={
            "start_date": (now - timedelta(days=2)).isoformat(),
            "end_date": now.isoformat(),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_requests"] == 1
    assert payload["total_input_tokens"] == 123
    assert payload["total_output_tokens"] == 45
    assert payload["by_model"]["Qwen/Qwen3-14B-FP8"] == {
        "requests": 1,
        "input_tokens": 123,
        "output_tokens": 45,
    }


def test_ai_api_requests_require_admin_for_review(
    client: TestClient,
    db: Session,
) -> None:
    user_headers = _create_test_user_headers(
        client,
        db,
        "ai-api-reviewer-check@example.com",
    )
    create_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests",
        headers=user_headers,
        json={"purpose": "Use AI API for another classroom workflow."},
    )
    request_id = create_response.json()["id"]

    review_response = client.post(
        f"{settings.API_V1_STR}/ai-api/requests/{request_id}/review",
        headers=user_headers,
        json={"status": "approved"},
    )
    assert review_response.status_code == 403


def test_ai_api_credentials_list_all_for_admin(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    user_headers_a = _create_test_user_headers(
        client, db, "ai-api-admin-list-a@example.com"
    )
    user_headers_b = _create_test_user_headers(
        client, db, "ai-api-admin-list-b@example.com"
    )

    _create_and_approve_ai_api_request(
        client=client,
        user_headers=user_headers_a,
        superuser_token_headers=superuser_token_headers,
        purpose="Admin list test request for user A.",
        api_key_name="user-a-key",
    )
    _create_and_approve_ai_api_request(
        client=client,
        user_headers=user_headers_b,
        superuser_token_headers=superuser_token_headers,
        purpose="Admin list test request for user B.",
        api_key_name="user-b-key",
    )

    # Rotate one key so we get both active and inactive records.
    my_credentials_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials/my",
        headers=user_headers_a,
    )
    assert my_credentials_response.status_code == 200
    old_credential_id = my_credentials_response.json()["data"][0]["id"]

    rotate_response = client.post(
        f"{settings.API_V1_STR}/ai-api/credentials/{old_credential_id}/rotate",
        headers=user_headers_a,
    )
    assert rotate_response.status_code == 200

    all_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials",
        headers=superuser_token_headers,
    )
    assert all_response.status_code == 200
    payload = all_response.json()
    assert payload["count"] >= 3
    assert payload["data"]
    assert any(item["status"] == "inactive" for item in payload["data"])
    assert all("user_email" in item for item in payload["data"])

    inactive_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials?status=inactive",
        headers=superuser_token_headers,
    )
    assert inactive_response.status_code == 200
    inactive_payload = inactive_response.json()
    assert inactive_payload["count"] >= 1
    assert all(item["status"] == "inactive" for item in inactive_payload["data"])

    email_filtered_response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials?user_email=admin-list-a",
        headers=superuser_token_headers,
    )
    assert email_filtered_response.status_code == 200
    email_payload = email_filtered_response.json()
    assert email_payload["count"] >= 1
    assert all(
        "admin-list-a" in (item["user_email"] or "") for item in email_payload["data"]
    )


def test_ai_api_credentials_list_all_requires_admin(
    client: TestClient,
    db: Session,
) -> None:
    user_headers = _create_test_user_headers(
        client,
        db,
        "ai-api-admin-list-authz@example.com",
    )

    response = client.get(
        f"{settings.API_V1_STR}/ai-api/credentials",
        headers=user_headers,
    )
    assert response.status_code == 403
