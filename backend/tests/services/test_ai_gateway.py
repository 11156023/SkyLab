"""AI API 申請審核與「我的用量」統計。"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.exceptions import BadRequestError
from app.models import (
    USAGE_SOURCE_PLATFORM,
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    User,
)
from app.repositories import user as user_repo
from app.schemas import AIAPIRequestCreate, AIAPIRequestReview, UserCreate
from app.services.llm_gateway import ai_gateway_service


def _user(db: Session) -> User:
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(
            email=f"b15-ai-{uuid.uuid4().hex[:10]}@example.com",
            password="strongpass123",
        ),
    )
    db.commit()
    db.refresh(user)
    return user


def _admin(db: Session) -> User:
    admin = user_repo.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    assert admin is not None
    return admin


def _request(db: Session, user: User, *, duration: str = "never") -> uuid.UUID:
    created = ai_gateway_service.create_request(
        session=db,
        request_in=AIAPIRequestCreate(
            purpose="Gateway regression test request purpose",
            api_key_name="b15",
            duration=duration,
        ),
        user=user,
    )
    return created.id


def _credentials(db: Session, request_id: uuid.UUID) -> list[AIAPICredential]:
    return list(
        db.exec(
            select(AIAPICredential).where(AIAPICredential.request_id == request_id)
        ).all()
    )


# ---- 效期 ------------------------------------------------------------


def test_unknown_duration_is_rejected_on_submit(db: Session) -> None:
    user = _user(db)
    # schema 已收斂成 Literal；服務層另外把關（model_construct 繞過驗證）
    with pytest.raises(BadRequestError):
        ai_gateway_service.create_request(
            session=db,
            request_in=AIAPIRequestCreate.model_construct(
                purpose="Gateway regression test request purpose",
                api_key_name="b15",
                duration="2h",
            ),
            user=user,
        )


def test_seven_day_key_expires_in_seven_days(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user, duration="7d")

    ai_gateway_service.review_request(
        session=db,
        request_id=request_id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
        reviewer=_admin(db),
    )

    [credential] = _credentials(db, request_id)
    assert credential.expires_at is not None
    remaining = credential.expires_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)


def test_legacy_unknown_duration_cannot_be_approved_as_never_expiring(
    db: Session,
) -> None:
    user = _user(db)
    legacy = AIAPIRequest(
        user_id=user.id,
        purpose="legacy row with a free-text duration",
        api_key_name="legacy",
        duration="1y",
    )
    db.add(legacy)
    db.commit()

    with pytest.raises(BadRequestError):
        ai_gateway_service.review_request(
            session=db,
            request_id=legacy.id,
            review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
            reviewer=_admin(db),
        )
    db.rollback()
    assert _credentials(db, legacy.id) == []
    # 駁回仍然可以
    rejected = ai_gateway_service.review_request(
        session=db,
        request_id=legacy.id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.rejected),
        reviewer=_admin(db),
    )
    assert rejected.status == AIAPIRequestStatus.rejected


# ---- 審核狀態與重複核准 ---------------------------------------------


def test_review_with_pending_status_is_rejected(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user)

    with pytest.raises(BadRequestError):
        ai_gateway_service.review_request(
            session=db,
            request_id=request_id,
            # model_construct：schema 之後若收斂成 Literal，服務層仍要擋
            review_data=AIAPIRequestReview.model_construct(
                status=AIAPIRequestStatus.pending, review_comment=None
            ),
            reviewer=_admin(db),
        )

    db.expire_all()
    stored = db.get(AIAPIRequest, request_id)
    assert stored is not None
    assert stored.status == AIAPIRequestStatus.pending
    assert stored.reviewer_id is None
    assert stored.reviewed_at is None


def test_second_approval_issues_no_second_credential(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user)
    review = AIAPIRequestReview(status=AIAPIRequestStatus.approved)

    ai_gateway_service.review_request(
        session=db, request_id=request_id, review_data=review, reviewer=_admin(db)
    )
    with pytest.raises(BadRequestError):
        ai_gateway_service.review_request(
            session=db, request_id=request_id, review_data=review, reviewer=_admin(db)
        )

    assert len(_credentials(db, request_id)) == 1


def test_review_reads_the_row_with_a_lock(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    request_id = _request(db, user)
    statements: list[str] = []
    original_exec = db.exec

    def spy(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=db.get_bind().dialect)))
        return original_exec(statement, *args, **kwargs)

    monkeypatch.setattr(db, "exec", spy)
    ai_gateway_service.review_request(
        session=db,
        request_id=request_id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.rejected),
        reviewer=_admin(db),
    )

    assert any(
        "FROM ai_api_requests" in sql and "FOR UPDATE" in sql for sql in statements
    )


# ---- 我的用量 ----------------------------------------------------


def _approved_credential(db: Session, user: User) -> uuid.UUID:
    request_id = _request(db, user)
    ai_gateway_service.review_request(
        session=db,
        request_id=request_id,
        review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
        reviewer=_admin(db),
    )
    [credential] = _credentials(db, request_id)
    return credential.id


def _usage(
    user: User, credential_id: uuid.UUID, model: str, at: datetime, tokens: int
) -> AIAPIUsage:
    return AIAPIUsage(
        user_id=user.id,
        credential_id=credential_id,
        model_name=model,
        call_type="chat_completion",
        input_tokens=tokens,
        output_tokens=tokens // 2,
        status="success",
        created_at=at,
    )


def test_usage_stats_are_aggregated_per_model_and_day(db: Session) -> None:
    user = _user(db)
    credential_id = _approved_credential(db, user)
    day1 = datetime(2026, 9, 7, 3, tzinfo=UTC)
    db.add_all(
        [
            _usage(user, credential_id, "model-a", day1, 100),
            _usage(user, credential_id, "model-a", day1 + timedelta(days=1), 40),
            _usage(user, credential_id, "model-b", day1 + timedelta(days=2), 10),
            _usage(user, credential_id, "model-b", day1 + timedelta(days=2, hours=1), 20),
            # 區間外
            _usage(user, credential_id, "model-a", day1 + timedelta(days=10), 999),
        ]
    )
    db.commit()

    stats = ai_gateway_service.get_user_usage_stats(
        session=db,
        user_id=user.id,
        start_date=datetime(2026, 9, 6, tzinfo=UTC),
        end_date=datetime(2026, 9, 9, 23, tzinfo=UTC),
    )

    assert stats["total_requests"] == 4
    assert stats["total_input_tokens"] == 170
    assert stats["total_output_tokens"] == 50 + 20 + 5 + 10
    assert stats["by_model"] == {
        "model-a": {"requests": 2, "input_tokens": 140, "output_tokens": 70},
        "model-b": {"requests": 2, "input_tokens": 30, "output_tokens": 15},
    }
    assert [(p["date"], p["requests"], p["input_tokens"]) for p in stats["daily"]] == [
        (date(2026, 9, 6), 0, 0),
        (date(2026, 9, 7), 1, 100),
        (date(2026, 9, 8), 1, 40),
        (date(2026, 9, 9), 2, 30),
    ]


def test_daily_buckets_follow_the_viewer_timezone(db: Session) -> None:
    user = _user(db)
    credential_id = _approved_credential(db, user)
    # 台灣時間 2026-09-10 07:30
    db.add(
        _usage(user, credential_id, "model-a", datetime(2026, 9, 9, 23, 30, tzinfo=UTC), 7)
    )
    db.commit()
    window = {
        "session": db,
        "user_id": user.id,
        "start_date": datetime(2026, 9, 8, 16, tzinfo=UTC),  # 台北 09-09 00:00
        "end_date": datetime(2026, 9, 10, 15, tzinfo=UTC),  # 台北 09-10 23:00
    }

    taipei = ai_gateway_service.get_user_usage_stats(**window, tz="Asia/Taipei")
    utc = ai_gateway_service.get_user_usage_stats(**window)
    bogus = ai_gateway_service.get_user_usage_stats(**window, tz="Not/AZone")

    assert [(p["date"], p["requests"]) for p in taipei["daily"]] == [
        (date(2026, 9, 9), 0),
        (date(2026, 9, 10), 1),
    ]
    assert (date(2026, 9, 9), 1) in [(p["date"], p["requests"]) for p in utc["daily"]]
    assert bogus["daily"] == utc["daily"]


def test_template_usage_stats_use_the_same_aggregation(db: Session) -> None:
    user = _user(db)
    at = datetime(2026, 9, 9, 23, 30, tzinfo=UTC)
    db.add_all(
        [
            AIAPIUsage(
                source=USAGE_SOURCE_PLATFORM,
                user_id=user.id,
                call_type="chat",
                model_name="m",
                input_tokens=3,
                output_tokens=1,
                status="success",
                created_at=at,
            ),
            AIAPIUsage(
                source=USAGE_SOURCE_PLATFORM,
                user_id=user.id,
                call_type="recommend",
                model_name="m",
                input_tokens=5,
                output_tokens=2,
                status="error",
                created_at=at,
            ),
        ]
    )
    db.commit()

    stats = ai_gateway_service.get_user_template_usage_stats(
        session=db,
        user_id=user.id,
        start_date=at - timedelta(hours=1),
        end_date=at + timedelta(hours=1),
        tz="Asia/Taipei",
    )

    assert stats["total_calls"] == 2
    assert stats["total_input_tokens"] == 8
    assert stats["by_call_type"]["recommend"] == {
        "calls": 1,
        "input_tokens": 5,
        "output_tokens": 2,
    }
    assert [(p["date"], p["requests"]) for p in stats["daily"]] == [
        (date(2026, 9, 10), 2)
    ]


def test_sqlite_fallback_buckets_by_viewer_offset() -> None:
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    user_id = uuid.uuid4()
    at = datetime(2026, 9, 9, 23, 30, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            AIAPIUsage(
                source=USAGE_SOURCE_PLATFORM,
                user_id=user_id,
                call_type="chat",
                model_name="m",
                input_tokens=4,
                output_tokens=2,
                status="success",
                created_at=at,
            )
        )
        session.commit()
        stats = ai_gateway_service.get_user_template_usage_stats(
            session=session,
            user_id=user_id,
            start_date=at - timedelta(hours=1),
            end_date=at + timedelta(hours=1),
            tz="Asia/Taipei",
        )
    engine.dispose()

    assert stats["total_calls"] == 1
    assert [(p["date"], p["requests"]) for p in stats["daily"]] == [
        (date(2026, 9, 10), 1)
    ]
