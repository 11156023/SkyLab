"""app/services/llm_gateway/ai_gateway_service.py 的補強測試（審核、金鑰管理、監控聚合）。"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, select

from app.core.config import settings
from app.exceptions import BadRequestError, NotFoundError
from app.features.ai.config import settings as ai_api_settings
from app.models import (
    USAGE_SOURCE_API_KEY,
    USAGE_SOURCE_PLATFORM,
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    User,
    UserRole,
)
from app.repositories import user as user_repo
from app.schemas import AIAPIRequestCreate, AIAPIRequestReview, UserCreate
from app.services.llm_gateway import ai_gateway_service as svc

# 監控測試的資料放在遠古時段，避免與同一測試庫其他資料互相干擾
WINDOW_START = datetime(2001, 3, 10, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2001, 3, 12, 0, 0, tzinfo=UTC)


def _user(db: Session, *, role: UserRole = UserRole.student) -> User:
    user = user_repo.create_user(
        session=db,
        user_create=UserCreate(
            email=f"b17-ai-{uuid.uuid4().hex[:10]}@example.com",
            password="strongpass123",
            full_name="B17 Coverage",
        ),
    )
    user.role = role
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _admin(db: Session) -> User:
    admin = user_repo.get_user_by_email(session=db, email=settings.FIRST_SUPERUSER)
    assert admin is not None
    return admin


def _request(db: Session, user: User) -> uuid.UUID:
    return svc.create_request(
        session=db,
        request_in=AIAPIRequestCreate(
            purpose="Gateway coverage test request purpose",
            api_key_name="b17",
            duration="30d",
        ),
        user=user,
    ).id


def _credential(db: Session, user: User) -> AIAPICredential:
    request_id = _request(db, user)
    svc.review_request(
        session=db,
        request_id=request_id,
        review_data=AIAPIRequestReview(
            status=AIAPIRequestStatus.approved, review_comment="  ok  "
        ),
        reviewer=_admin(db),
    )
    return db.exec(
        select(AIAPICredential).where(AIAPICredential.request_id == request_id)
    ).one()


@pytest.fixture
def usage_cleanup(db: Session) -> Iterator[list[uuid.UUID]]:
    user_ids: list[uuid.UUID] = []
    yield user_ids
    db.rollback()
    if user_ids:
        db.exec(delete(AIAPIUsage).where(AIAPIUsage.user_id.in_(user_ids)))  # type: ignore[attr-defined]
        db.commit()


# ---- 申請與審核 --------------------------------------------------------


def test_list_all_requests_with_and_without_status(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user)

    pending = svc.list_all_requests(
        session=db, status=AIAPIRequestStatus.pending, limit=1000
    )
    assert any(item.id == request_id for item in pending.data)
    assert pending.count >= 1
    everything = svc.list_all_requests(session=db, limit=1000)
    assert everything.count >= pending.count


def test_get_request_found_and_missing(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user)

    public = svc.get_request(session=db, request_id=request_id, current_user=user)
    assert public.id == request_id
    assert public.user_email == user.email

    with pytest.raises(NotFoundError):
        svc.get_request(session=db, request_id=uuid.uuid4(), current_user=user)


def test_review_unknown_request_is_not_found(db: Session) -> None:
    with pytest.raises(NotFoundError):
        svc.review_request(
            session=db,
            request_id=uuid.uuid4(),
            review_data=AIAPIRequestReview(status=AIAPIRequestStatus.rejected),
            reviewer=_admin(db),
        )
    db.rollback()


def test_approval_requires_public_base_url(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    request_id = _request(db, user)
    monkeypatch.setattr(ai_api_settings, "ai_api_public_base_url", "   ")

    with pytest.raises(BadRequestError):
        svc.review_request(
            session=db,
            request_id=request_id,
            review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
            reviewer=_admin(db),
        )
    db.rollback()
    assert db.get(AIAPIRequest, request_id).status == AIAPIRequestStatus.pending  # type: ignore[union-attr]


# ---- 金鑰管理 ----------------------------------------------------------


def test_manageable_credential_not_found_paths(db: Session) -> None:
    user = _user(db)
    with pytest.raises(NotFoundError):
        svc.rotate_credential(session=db, credential_id=uuid.uuid4(), current_user=user)
    db.rollback()
    with pytest.raises(NotFoundError):
        svc.update_credential_name(
            session=db, credential_id=uuid.uuid4(), name="x", current_user=user
        )
    db.rollback()


def test_rotate_revoked_credential_is_rejected(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)
    credential.revoked_at = datetime.now(UTC)
    db.add(credential)
    db.commit()

    with pytest.raises(BadRequestError):
        svc.rotate_credential(
            session=db, credential_id=credential.id, current_user=user
        )
    db.rollback()


def test_admin_rotation_on_behalf_returns_no_secret(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)

    rotated = svc.rotate_credential(
        session=db, credential_id=credential.id, current_user=_admin(db)
    )
    assert rotated.api_key is None
    assert rotated.id != credential.id


def test_rotation_integrity_error_after_concurrent_rotation(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    credential = _credential(db, user)
    original_rollback = db.rollback

    def fail_commit() -> None:
        raise IntegrityError("insert", {}, Exception("duplicate"))

    monkeypatch.setattr(db, "commit", fail_commit)
    # 不真的 rollback：讓 session.get 拿到「另一個交易已撤銷」的來源金鑰
    monkeypatch.setattr(db, "rollback", lambda: None)
    try:
        with pytest.raises(BadRequestError):
            svc.rotate_credential(
                session=db, credential_id=credential.id, current_user=user
            )
    finally:
        monkeypatch.undo()
        original_rollback()


def test_rotation_integrity_error_is_reraised_when_source_still_active(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _user(db)
    credential = _credential(db, user)

    def fail_commit() -> None:
        raise IntegrityError("insert", {}, Exception("other"))

    monkeypatch.setattr(db, "commit", fail_commit)
    try:
        with pytest.raises(IntegrityError):
            svc.rotate_credential(
                session=db, credential_id=credential.id, current_user=user
            )
    finally:
        monkeypatch.undo()
        db.rollback()


def test_delete_credential_on_behalf_and_twice(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)
    admin = _admin(db)

    result = svc.delete_credential(
        session=db, credential_id=credential.id, current_user=admin
    )
    assert "deleted" in result.message
    db.refresh(credential)
    assert credential.deleted_at is not None
    assert credential.revoked_at is not None
    assert credential.api_key_encrypted == ""

    # 已刪除再刪一次：不改欄位，但仍留稽核
    first_deleted_at = credential.deleted_at
    svc.delete_credential(session=db, credential_id=credential.id, current_user=user)
    db.refresh(credential)
    assert credential.deleted_at == first_deleted_at


def test_delete_keeps_existing_revocation_time(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)
    revoked_at = datetime(2020, 1, 1, tzinfo=UTC)
    credential.revoked_at = revoked_at
    db.add(credential)
    db.commit()

    svc.delete_credential(session=db, credential_id=credential.id, current_user=user)
    db.refresh(credential)
    assert credential.revoked_at == revoked_at


def test_update_credential_name(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)

    renamed = svc.update_credential_name(
        session=db, credential_id=credential.id, name="renamed", current_user=user
    )
    assert renamed.api_key_name == "renamed"

    svc.update_credential_name(
        session=db,
        credential_id=credential.id,
        name="by-admin",
        current_user=_admin(db),
    )
    db.refresh(credential)
    assert credential.api_key_name == "by-admin"

    credential.deleted_at = datetime.now(UTC)
    credential.revoked_at = credential.deleted_at
    db.add(credential)
    db.commit()
    with pytest.raises(NotFoundError):
        svc.update_credential_name(
            session=db, credential_id=credential.id, name="x", current_user=user
        )
    db.rollback()


def test_list_all_credentials_filters(db: Session) -> None:
    user = _user(db, role=UserRole.student)
    credential = _credential(db, user)
    since = credential.created_at - timedelta(seconds=1)

    active = svc.list_all_credentials(
        session=db,
        status="active",
        query=user.email,
        user_roles=["student"],
        created_after=since,
    )
    assert [item.id for item in active.data] == [credential.id]
    assert active.data[0].status == "active"
    assert active.active_count == 1

    inactive = svc.list_all_credentials(
        session=db,
        status="inactive",
        user_email=user.email,
        user_roles=["student"],
        created_after=since,
    )
    assert inactive.data == []
    assert inactive.total_count == 1


def test_resolve_credential_status_marks_deleted_rows() -> None:
    credential = AIAPICredential(
        user_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        base_url="http://x",
        api_key_encrypted="",
        api_key_prefix="ccai_",
        api_key_name="n",
        deleted_at=datetime.now(UTC),
    )
    assert svc._resolve_credential_status(
        credential=credential, now=datetime.now(UTC)
    ) == ("inactive", "deleted")


def test_get_credential_owner_only(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)

    detail = svc.get_credential(
        session=db, credential_id=credential.id, current_user=user
    )
    assert detail.api_key and detail.api_key.startswith("ccai_")
    with pytest.raises(NotFoundError):
        svc.get_credential(
            session=db, credential_id=credential.id, current_user=_admin(db)
        )
    with pytest.raises(NotFoundError):
        svc.get_credential(session=db, credential_id=uuid.uuid4(), current_user=user)


def test_user_listings_and_usage_records(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)
    at = datetime(2001, 6, 1, 12, tzinfo=UTC)
    svc.record_usage(
        session=db,
        user_id=user.id,
        credential_id=credential.id,
        model_name="b17-m",
        request_type="chat_completion",
        input_tokens=3,
        output_tokens=4,
        request_duration_ms=2000,
        usage_reported=True,
        started_at=at,
        completed_at=at,
    )
    svc.record_template_call(
        session=db, user_id=user.id, call_type="chat", model_name="b17-m"
    )
    usage = db.exec(
        select(AIAPIUsage).where(
            AIAPIUsage.user_id == user.id, AIAPIUsage.source == USAGE_SOURCE_API_KEY
        )
    ).one()
    usage.created_at = at
    db.add(usage)
    db.commit()

    requests_page = svc.list_requests_by_user(session=db, user_id=user.id)
    assert requests_page.count == 1
    credentials_page = svc.list_credentials_by_user(session=db, user_id=user.id)
    assert [item.id for item in credentials_page.data] == [credential.id]

    records = svc.list_user_usage_records(
        session=db,
        user_id=user.id,
        start_date=at - timedelta(hours=1),
        end_date=at + timedelta(hours=1),
    )
    assert records["count"] == 1
    [record] = records["data"]
    assert record["total_tokens"] == 7
    assert record["e2e_output_tokens_per_second"] == 2.0
    assert record["route"] == svc.ROUTE_MODEL

    db.exec(delete(AIAPIUsage).where(AIAPIUsage.user_id == user.id))  # type: ignore[arg-type]
    db.commit()


def test_default_usage_window() -> None:
    end = datetime(2026, 1, 31, tzinfo=UTC)
    assert svc.default_usage_window(None, end) == (end - timedelta(days=30), end)
    start, now_end = svc.default_usage_window(None, None)
    assert now_end - start == timedelta(days=30)


@pytest.mark.parametrize(
    ("role", "duration"),
    [(UserRole.student, "never"), (UserRole.teacher, "90d"), (UserRole.student, "2h")],
)
def test_validate_request_duration_rejections(role: UserRole, duration: str) -> None:
    with pytest.raises(BadRequestError):
        svc._validate_request_duration(duration, User(email="x@example.com", role=role))


def test_pending_request_limit_and_deleted_applicant(db: Session) -> None:
    from app.exceptions import ConflictError

    user = _user(db)
    for _ in range(svc.MAX_PENDING_REQUESTS_PER_USER):
        _request(db, user)
    with pytest.raises(ConflictError):
        _request(db, user)
    db.rollback()

    ghost = User(id=uuid.uuid4(), email="ghost@example.com")
    with pytest.raises(NotFoundError):
        _request(db, ghost)
    db.rollback()


def test_approval_of_deleted_applicant_is_not_found(db: Session) -> None:
    user = _user(db)
    request_id = _request(db, user)
    user.deleted_at = datetime.now(UTC)
    user.is_active = False
    db.add(user)
    db.commit()
    try:
        with pytest.raises(NotFoundError):
            svc.review_request(
                session=db,
                request_id=request_id,
                review_data=AIAPIRequestReview(status=AIAPIRequestStatus.approved),
                reviewer=_admin(db),
            )
        db.rollback()
        # 已刪帳號的金鑰也不能再操作
        with pytest.raises(NotFoundError):
            svc.rotate_credential(
                session=db,
                credential_id=_orphan_credential(db, user, request_id),
                current_user=_admin(db),
            )
    finally:
        db.rollback()


def _orphan_credential(db: Session, user: User, request_id: uuid.UUID) -> uuid.UUID:
    credential = AIAPICredential(
        user_id=user.id,
        request_id=request_id,
        base_url="http://x",
        api_key_encrypted="",
        api_key_prefix=f"ccai_{uuid.uuid4().hex[:6]}",
        api_key_name="orphan",
    )
    db.add(credential)
    db.commit()
    return credential.id


def test_rotate_expired_credential_is_rejected(db: Session) -> None:
    user = _user(db)
    credential = _credential(db, user)
    credential.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.add(credential)
    db.commit()

    with pytest.raises(BadRequestError):
        svc.rotate_credential(
            session=db, credential_id=credential.id, current_user=user
        )
    db.rollback()


def test_resolve_credential_status_revoked_expired_active() -> None:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "request_id": uuid.uuid4(),
        "base_url": "http://x",
        "api_key_encrypted": "",
        "api_key_prefix": "ccai_",
        "api_key_name": "n",
    }
    assert svc._resolve_credential_status(
        credential=AIAPICredential(**base, revoked_at=now), now=now
    ) == ("inactive", "revoked")
    assert svc._resolve_credential_status(
        credential=AIAPICredential(**base, expires_at=now), now=now
    ) == ("inactive", "expired")
    assert svc._resolve_credential_status(
        credential=AIAPICredential(**base), now=now
    ) == ("active", None)


# ---- 日期工具 ----------------------------------------------------------


def test_as_date_accepts_datetime_date_and_string() -> None:
    assert svc._as_date(datetime(2026, 1, 2, 3, 4)) == date(2026, 1, 2)
    assert svc._as_date(date(2026, 1, 3)) == date(2026, 1, 3)
    assert svc._as_date("2026-01-04T00:00:00") == date(2026, 1, 4)


def test_daily_buckets_skip_zero_fill_for_huge_ranges() -> None:
    buckets = svc._daily_usage_buckets(
        [(date(2026, 1, 5), 2, 10, 5), ("2026-01-05", None, None, None)],
        start_date=datetime(2020, 1, 1, tzinfo=UTC),
        end_date=datetime(2026, 2, 1, tzinfo=UTC),
    )
    assert buckets == [
        {
            "date": date(2026, 1, 5),
            "requests": 2,
            "input_tokens": 10,
            "output_tokens": 5,
        }
    ]


def test_monitoring_bucket_datetime_parsing() -> None:
    naive = datetime(2026, 1, 1, 10)
    aware = datetime(2026, 1, 1, 10, tzinfo=UTC)
    assert svc._monitoring_bucket_datetime(naive) == aware
    assert svc._monitoring_bucket_datetime(aware) is aware
    assert svc._monitoring_bucket_datetime("2026-01-01 10:00:00") == aware
    assert svc._monitoring_bucket_datetime("2026-01-01T10:00:00Z") == aware
    assert svc._monitoring_bucket_datetime("not-a-date") is None
    assert svc._monitoring_bucket_datetime(12345) is None


def test_monitoring_percent_delta() -> None:
    assert svc._monitoring_percent_delta(5, 0) is None
    assert svc._monitoring_percent_delta(15, 10) == 50.0


def test_monitoring_overview_rejects_unknown_bucket(db: Session) -> None:
    with pytest.raises(ValueError):
        svc.get_monitoring_overview(session=db, bucket="week")


# ---- 監控聚合（真 PostgreSQL） ----------------------------------------


def _seed_monitoring_data(
    db: Session, cleanup: list[uuid.UUID]
) -> tuple[User, User, AIAPICredential]:
    proxy_user = _user(db)
    template_user = _user(db, role=UserRole.teacher)
    cleanup.extend([proxy_user.id, template_user.id])
    credential = _credential(db, proxy_user)
    at = WINDOW_START + timedelta(hours=5)
    previous = WINDOW_START - timedelta(hours=5)
    rows = [
        AIAPIUsage(
            user_id=proxy_user.id,
            source=USAGE_SOURCE_API_KEY,
            credential_id=credential.id,
            model_name="b17-proxy-model",
            call_type="chat_completion",
            input_tokens=10,
            output_tokens=5,
            request_duration_ms=100,
            status="success",
            created_at=at,
        ),
        AIAPIUsage(
            user_id=proxy_user.id,
            source=USAGE_SOURCE_API_KEY,
            credential_id=credential.id,
            model_name="b17-proxy-model",
            call_type="chat_completion",
            input_tokens=1,
            output_tokens=0,
            status="error",
            error_message="boom",
            created_at=at + timedelta(hours=1),
        ),
        AIAPIUsage(
            user_id=template_user.id,
            source=USAGE_SOURCE_PLATFORM,
            model_name="b17-template-model",
            call_type="recommend",
            preset="b17-preset",
            input_tokens=20,
            output_tokens=8,
            request_duration_ms=300,
            status="success",
            created_at=at,
        ),
        # 上一期（比較用）
        AIAPIUsage(
            user_id=proxy_user.id,
            source=USAGE_SOURCE_API_KEY,
            credential_id=credential.id,
            model_name="b17-proxy-model",
            call_type="chat_completion",
            input_tokens=2,
            output_tokens=2,
            request_duration_ms=50,
            status="error",
            created_at=previous,
        ),
    ]
    for row in rows:
        db.add(row)
    db.commit()
    return proxy_user, template_user, credential


def test_monitoring_overview_with_comparison(
    db: Session, usage_cleanup: list[uuid.UUID]
) -> None:
    _seed_monitoring_data(db, usage_cleanup)

    overview = svc.get_monitoring_overview(
        session=db, start_date=WINDOW_START, end_date=WINDOW_END, bucket="hour"
    )

    summary = overview["summary"]
    assert summary["total_calls"] == 3
    assert summary["failed_calls"] == 1
    assert summary["active_users"] == 2
    comparison = overview["comparison"]
    assert comparison["total_calls_delta"] == 2
    assert comparison["total_calls_percent"] == 200.0
    assert comparison["failed_calls_delta"] == 0
    assert comparison["error_rate_delta"] is not None
    assert comparison["avg_latency_ms_delta"] is not None
    assert [point["total_calls"] for point in overview["series"]] == [2, 1]
    first = overview["series"][0]
    assert first["proxy_calls"] == 1 and first["template_calls"] == 1
    assert first["avg_latency_ms"] == 200
    assert overview["series"][1]["avg_latency_ms"] is None
    models = {item["model_name"]: item for item in overview["model_breakdown"]}
    assert models["b17-proxy-model"]["failed_calls"] == 1
    assert models["b17-template-model"]["avg_latency_ms"] == 300


def test_monitoring_overview_without_template_by_day(
    db: Session, usage_cleanup: list[uuid.UUID]
) -> None:
    _seed_monitoring_data(db, usage_cleanup)

    overview = svc.get_monitoring_overview(
        session=db,
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        bucket="day",
        include_template=False,
    )
    assert overview["summary"]["total_calls"] == 2
    assert [point["template_calls"] for point in overview["series"]] == [0]
    assert [item["model_name"] for item in overview["model_breakdown"]] == [
        "b17-proxy-model"
    ]

    stats = svc.get_monitoring_stats(
        session=db,
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        include_template=False,
    )
    assert stats["template_total_calls"] == 0
    assert stats["models_used"] == ["b17-proxy-model"]
    assert stats["success_rate"] == 50


def test_monitoring_overview_skips_unparseable_buckets(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc,
        "_monitoring_bucket_rows",
        lambda **_: [("garbage", 1, 1, 0, 0, 0, 0)],
    )
    overview = svc.get_monitoring_overview(
        session=db,
        start_date=WINDOW_END + timedelta(days=1),
        end_date=WINDOW_END + timedelta(days=2),
    )
    assert overview["series"] == []
    assert overview["summary"]["total_calls"] == 0
    assert overview["comparison"]["error_rate_delta"] is None


def test_list_proxy_and_template_calls_with_filters(
    db: Session, usage_cleanup: list[uuid.UUID]
) -> None:
    proxy_user, template_user, credential = _seed_monitoring_data(db, usage_cleanup)

    proxy = svc.list_proxy_calls(
        session=db,
        user_id=proxy_user.id,
        model_name="b17-proxy",
        call_status="error",
        start_date=WINDOW_START,
        end_date=WINDOW_END,
    )
    assert proxy["count"] == 1
    [record] = proxy["data"]
    assert record["credential_id"] == credential.id
    assert record["user_email"] == proxy_user.email
    assert record["request_type"] == "chat_completion"

    template = svc.list_template_calls(
        session=db,
        user_id=template_user.id,
        call_type="recommend",
        preset="b17-preset",
        call_status="success",
        start_date=WINDOW_START,
        end_date=WINDOW_END,
    )
    assert template["count"] == 1
    assert template["data"][0]["preset"] == "b17-preset"
    assert template["data"][0]["e2e_output_tokens_per_second"] is None


def test_list_users_usage_with_and_without_template(
    db: Session, usage_cleanup: list[uuid.UUID]
) -> None:
    proxy_user, template_user, _ = _seed_monitoring_data(db, usage_cleanup)

    combined = svc.list_users_usage(
        session=db, start_date=WINDOW_START, end_date=WINDOW_END
    )
    assert combined["count"] == 2
    by_user = {row["user_id"]: row for row in combined["data"]}
    assert by_user[proxy_user.id]["failed_calls"] == 1
    assert by_user[proxy_user.id]["error_rate"] == 50.0
    assert by_user[proxy_user.id]["avg_latency_ms"] == 100
    assert by_user[template_user.id]["template_calls"] == 1
    assert by_user[template_user.id]["proxy_calls"] == 0

    proxy_only = svc.list_users_usage(
        session=db,
        start_date=WINDOW_START,
        end_date=WINDOW_END,
        include_template=False,
    )
    assert proxy_only["count"] == 1
    [row] = proxy_only["data"]
    assert row["user_id"] == proxy_user.id
    assert row["template_calls"] == 0
    assert row["proxy_input_tokens"] == 11
    assert row["avg_latency_ms"] == 100


def test_list_users_usage_rows_without_duration(
    db: Session, usage_cleanup: list[uuid.UUID]
) -> None:
    user = _user(db)
    usage_cleanup.append(user.id)
    credential = _credential(db, user)
    db.add(
        AIAPIUsage(
            user_id=user.id,
            source=USAGE_SOURCE_API_KEY,
            credential_id=credential.id,
            model_name="b17-nodur",
            call_type="chat_completion",
            status="success",
            created_at=WINDOW_END + timedelta(days=30),
        )
    )
    db.commit()
    window = (WINDOW_END + timedelta(days=29), WINDOW_END + timedelta(days=31))

    proxy_only = svc.list_users_usage(
        session=db,
        start_date=window[0],
        end_date=window[1],
        include_template=False,
    )
    assert proxy_only["data"][0]["avg_latency_ms"] is None
    combined = svc.list_users_usage(
        session=db, start_date=window[0], end_date=window[1]
    )
    assert combined["data"][0]["avg_latency_ms"] is None


# ---- SQLite 分桶後備 ---------------------------------------------------


@pytest.mark.parametrize("bucket", ["hour", "day"])
def test_monitoring_bucket_rows_sqlite_fallback(bucket: str) -> None:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    at = datetime(2026, 9, 9, 23, 30, tzinfo=UTC)
    try:
        with Session(engine) as session:
            session.add(
                AIAPIUsage(
                    source=USAGE_SOURCE_PLATFORM,
                    user_id=uuid.uuid4(),
                    call_type="chat",
                    model_name="m",
                    input_tokens=4,
                    output_tokens=2,
                    status="success",
                    created_at=at,
                )
            )
            session.commit()
            rows: list[Any] = svc._monitoring_bucket_rows(
                session=session,
                source=USAGE_SOURCE_PLATFORM,
                bucket=bucket,
                start_date=None,
                end_date=None,
            )
    finally:
        engine.dispose()

    expected = "2026-09-09 23:00:00" if bucket == "hour" else "2026-09-09 00:00:00"
    assert [row[0] for row in rows] == [expected]
    assert rows[0][1] == 1
