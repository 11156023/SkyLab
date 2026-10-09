"""AI 使用量 CSV 匯出的查詢、串流與隱私邊界。"""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import AIAPIUsage, User
from app.services.llm_gateway import ai_usage_export


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def _user(session: Session, email: str) -> User:
    user = User(email=email, hashed_password="test-hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _export(session: Session, **overrides: object) -> list[dict[str, str]]:
    params: dict[str, object] = {
        "session": session,
        "start_date": None,
        "end_date": datetime.now(UTC),
        "source": "all",
        "status": None,
        "model_name": None,
        "call_type": None,
        "user_id": None,
    }
    params.update(overrides)
    text = "".join(ai_usage_export.export_csv_chunks(**params))  # type: ignore[arg-type]
    rows = list(csv.DictReader(io.StringIO(text.removeprefix("\ufeff"))))
    return rows


def test_export_contains_both_sources_and_redacts_sensitive_error(session: Session) -> None:
    user = _user(session, "usage-export@example.com")
    now = datetime.now(UTC).replace(microsecond=0)
    session.add_all(
        [
            AIAPIUsage(
                id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                user_id=user.id,
                credential_id=uuid.uuid4(),
                model_name="api-model",
                call_type="chat_completion",
                input_tokens=4,
                output_tokens=6,
                stream=True,
                usage_reported=True,
                status="error",
                error_message=(
                    'Authorization: Bearer ccai_secret-value; '
                    '=formula, keep this detail'
                ),
                created_at=now,
            ),
            AIAPIUsage(
                id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
                user_id=user.id,
                source="platform",
                model_name="platform-model",
                call_type="ai_nav",
                input_tokens=2,
                output_tokens=3,
                status="success",
                created_at=now - timedelta(seconds=1),
            ),
        ]
    )
    session.commit()

    rows = _export(session)

    assert [row["source"] for row in rows] == ["api_key", "platform"]
    assert rows[0]["total_tokens"] == "10"
    assert rows[0]["stream"] == "true"
    assert rows[0]["usage_reported"] == "true"
    assert "ccai_secret-value" not in rows[0]["error_message"]
    assert rows[0]["error_message"].startswith("[REDACTED]")
    assert rows[0]["model_name"] == "api-model"


def test_export_filters_are_applied_in_database_and_empty_result_has_header(
    session: Session,
) -> None:
    user_a = _user(session, "a@example.com")
    user_b = _user(session, "b@example.com")
    now = datetime.now(UTC).replace(microsecond=0)
    session.add_all(
        [
            AIAPIUsage(
                user_id=user_a.id,
                source="platform",
                model_name="target",
                call_type="target_call",
                status="success",
                created_at=now - timedelta(hours=1),
            ),
            AIAPIUsage(
                user_id=user_b.id,
                source="platform",
                model_name="target",
                call_type="other_call",
                status="error",
                created_at=now - timedelta(hours=1),
            ),
            AIAPIUsage(
                user_id=user_a.id,
                source="api_key",
                credential_id=uuid.uuid4(),
                model_name="target",
                call_type="target_call",
                status="success",
                created_at=now - timedelta(days=2),
            ),
        ]
    )
    session.commit()

    rows = _export(
        session,
        start_date=now - timedelta(days=1),
        end_date=now,
        source="platform",
        status="success",
        model_name="target",
        call_type="target_call",
        user_id=user_a.id,
    )
    assert len(rows) == 1
    assert rows[0]["user_email"] == "a@example.com"

    empty = _export(session, source="platform", model_name="missing")
    assert empty == []


def test_export_keyset_has_no_duplicates_across_batch_boundary(session: Session) -> None:
    user = _user(session, "many@example.com")
    created_at = datetime.now(UTC).replace(microsecond=0)
    rows = [
        AIAPIUsage(
            id=uuid.uuid4(),
            user_id=user.id,
            source="platform",
            model_name="bulk",
            call_type="bulk",
            status="success",
            created_at=created_at,
        )
        for _ in range(501)
    ]
    session.add_all(rows)
    session.commit()

    exported = _export(session)

    assert len(exported) == 501
    assert len({row["id"] for row in exported}) == 501
    assert [row["id"] for row in exported] == sorted(
        (str(row.id) for row in rows), reverse=True
    )


def test_error_message_is_truncated() -> None:
    value = ai_usage_export._sanitize_error_message("x" * 5000)
    assert len(value) == ai_usage_export.ERROR_MESSAGE_MAX_LENGTH


def test_standalone_known_api_key_is_redacted() -> None:
    value = ai_usage_export._sanitize_error_message("upstream returned ccai_secret_value")
    assert "ccai_secret_value" not in value
