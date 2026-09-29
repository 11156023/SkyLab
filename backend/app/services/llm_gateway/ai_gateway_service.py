import logging
import secrets
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sqlalchemy as sa
from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from app.core.authorizers import require_ai_api_access, require_ai_api_manage
from app.core.i18n import t
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import BadRequestError, NotFoundError
from app.features.ai.config import settings as ai_api_settings
from app.models import (
    API_KEY_PREFIX_LENGTH,
    USAGE_SOURCE_API_KEY,
    USAGE_SOURCE_PLATFORM,
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    User,
    get_datetime_utc,
)
from app.schemas import (
    AIAPICredentialAdminPublic,
    AIAPICredentialPublic,
    AIAPICredentialsAdminPublic,
    AIAPICredentialsPublic,
    AIAPICredentialWithSecret,
    AIAPIRequestCreate,
    AIAPIRequestPublic,
    AIAPIRequestReview,
    AIAPIRequestsPublic,
    Message,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_RATE_LIMIT = 20
#: 申請可選的金鑰效期（與前端 DURATION_OPTIONS 一致）；never 代表不過期
KEY_DURATIONS: dict[str, timedelta | None] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "never": None,
}
_REVIEW_DECISIONS = (AIAPIRequestStatus.approved, AIAPIRequestStatus.rejected)
MONITORING_SUCCESS_STATUSES = ("success", "ok", "200")
#: 使用統計端點沒帶區間時的預設回看天數
DEFAULT_USAGE_WINDOW_DAYS = 30


def default_usage_window(
    start_date: datetime | None, end_date: datetime | None
) -> tuple[datetime, datetime]:
    """補齊使用統計的查詢區間：沒給結束就用現在，沒給開始就往前推 30 天。"""
    end = end_date or datetime.now(timezone.utc)
    start = start_date or end - timedelta(days=DEFAULT_USAGE_WINDOW_DAYS)
    return start, end


def _e2e_output_tokens_per_second(
    *, output_tokens: int, duration_ms: int | None, usage_reported: bool
) -> float | None:
    """Return end-to-end throughput only when the upstream supplied usage."""
    if not usage_reported or duration_ms is None or duration_ms <= 0:
        return None
    return round(output_tokens * 1000 / duration_ms, 2)


def _call_metrics(row: AIAPIUsage) -> dict[str, Any]:
    """逐筆呼叫紀錄共用的欄位（金鑰呼叫與平台功能呼叫同存 AIAPIUsage）。"""
    return {
        "model_name": row.model_name,
        "request_id": row.request_id,
        "upstream_request_id": row.upstream_request_id,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "request_duration_ms": row.request_duration_ms,
        "first_token_ms": row.first_token_ms,
        "stream": row.stream,
        "usage_reported": row.usage_reported,
        "response_model": row.response_model,
        "e2e_output_tokens_per_second": _e2e_output_tokens_per_second(
            output_tokens=row.output_tokens,
            duration_ms=row.request_duration_ms,
            usage_reported=row.usage_reported,
        ),
        "status": row.status,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
    }


def _generate_user_api_key() -> str:
    return f"ccai_{secrets.token_urlsafe(24)}"


def _credential_prefix(api_key: str) -> str:
    api_key = api_key.strip()
    return api_key[: min(API_KEY_PREFIX_LENGTH, len(api_key))]


def _get_manageable_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> AIAPICredential:
    """取出金鑰並檢查「可寫」權限（擁有者，或具 AI_API_MANAGE_ALL 的管理員）。"""
    credential = session.get(AIAPICredential, credential_id)
    if not credential:
        raise NotFoundError(t("ai_gateway.credential_not_found"))
    require_ai_api_manage(
        current_user,
        credential.user_id,
        detail=t("ai_gateway.credential_manage_denied"),
    )
    return credential


def _acting_on_behalf(credential: AIAPICredential, current_user) -> bool:
    """這次操作是不是管理員在動別人的金鑰（決定是否回明文、稽核怎麼寫）。"""
    return getattr(current_user, "id", None) != credential.user_id


def _audit_suffix(credential: AIAPICredential, current_user) -> str:
    if not _acting_on_behalf(credential, current_user):
        return ""
    return f" on behalf of user {credential.user_id}"


def _to_request_public(req: AIAPIRequest) -> AIAPIRequestPublic:
    return AIAPIRequestPublic(
        id=req.id,
        user_id=req.user_id,
        user_email=req.user.email if req.user else None,
        user_full_name=req.user.full_name if req.user else None,
        purpose=req.purpose,
        api_key_name=req.api_key_name,
        duration=req.duration,
        rate_limit=req.rate_limit,
        status=req.status,
        reviewer_id=req.reviewer_id,
        reviewer_email=req.reviewer.email if req.reviewer else None,
        review_comment=req.review_comment,
        reviewed_at=req.reviewed_at,
        created_at=req.created_at,
    )


def _public_base_url(credential: AIAPICredential) -> str:
    """給使用者看的 AI API 位址：一律以「目前」的設定為準。

    credential.base_url 是核發當下的快照，只用於顯示（proxy 轉發不看它）。
    管理員事後修正 ``AI_API_PUBLIC_BASE_URL`` 時，舊金鑰的 Quick Start 也要
    跟著變正確，否則使用者會照著過期的位址（例如 localhost）去接，永遠連不上。
    設定留空才退回快照。
    """
    return ai_api_settings.resolved_public_base_url or credential.base_url


def _to_credential_public(credential: AIAPICredential) -> AIAPICredentialPublic:
    """一般呈現：只帶前綴。明文金鑰不進清單，避免每次載入頁面都再散佈一次。"""
    return AIAPICredentialPublic(
        id=credential.id,
        request_id=credential.request_id,
        base_url=_public_base_url(credential),
        api_key_prefix=credential.api_key_prefix,
        api_key_name=credential.api_key_name,
        rate_limit=credential.rate_limit,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
        created_at=credential.created_at,
    )


def _to_credential_with_secret(
    credential: AIAPICredential, *, api_key: str | None
) -> AIAPICredentialWithSecret:
    """擁有者詳細資料／輪替回應；代操輪替時只回前綴。"""
    return AIAPICredentialWithSecret(
        **_to_credential_public(credential).model_dump(),
        api_key=api_key,
    )


def get_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> AIAPICredentialWithSecret:
    """只有擁有者本人可以讀取完整金鑰，管理權限不授予明文讀取權限。"""
    credential = session.get(AIAPICredential, credential_id)
    if not credential or _acting_on_behalf(credential, current_user):
        raise NotFoundError(t("ai_gateway.credential_not_found"))
    return _to_credential_with_secret(
        credential, api_key=decrypt_value(credential.api_key_encrypted)
    )


def _resolve_credential_status(
    *, credential: AIAPICredential, now: datetime
) -> tuple[str, str | None]:
    if credential.revoked_at is not None:
        return "inactive", "revoked"
    if credential.expires_at is not None and credential.expires_at <= now:
        return "inactive", "expired"
    return "active", None


def _to_credential_admin_public(
    *,
    credential: AIAPICredential,
    user: User,
    request: AIAPIRequest | None,
    reviewer: User | None,
    last_used_at: datetime | None,
    now: datetime,
) -> AIAPICredentialAdminPublic:
    status, inactive_reason = _resolve_credential_status(credential=credential, now=now)
    return AIAPICredentialAdminPublic(
        id=credential.id,
        user_id=credential.user_id,
        user_email=user.email,
        user_full_name=user.full_name,
        user_role=user.role.value if user.role else None,
        request_id=credential.request_id,
        base_url=_public_base_url(credential),
        api_key_prefix=credential.api_key_prefix,
        api_key_name=credential.api_key_name,
        rate_limit=credential.rate_limit,
        status=status,
        inactive_reason=inactive_reason,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
        created_at=credential.created_at,
        request_purpose=request.purpose if request else None,
        reviewer_email=reviewer.email if reviewer else None,
        reviewer_full_name=reviewer.full_name if reviewer else None,
        reviewed_at=request.reviewed_at if request else None,
        last_used_at=last_used_at,
    )


def create_request(
    *, session: Session, request_in: AIAPIRequestCreate, user
) -> AIAPIRequestPublic:
    # 審核時只認得這幾種效期；其他字串核准後會變成永不過期的金鑰
    if request_in.duration not in KEY_DURATIONS:
        raise BadRequestError(t("ai_gateway.invalid_duration"))
    db_request = AIAPIRequest(
        user_id=user.id,
        purpose=request_in.purpose.strip(),
        api_key_name=request_in.api_key_name.strip(),
        duration=request_in.duration,
        rate_limit=DEFAULT_REQUEST_RATE_LIMIT,
    )
    session.add(db_request)
    audit_service.log_action(
        session=session,
        user_id=user.id,
        action="ai_api_request_submit",
        details=f"Submitted AI API request. Purpose: {db_request.purpose}",
        commit=False,
    )
    session.commit()
    session.refresh(db_request)
    logger.info("User %s submitted AI API request %s", user.email, db_request.id)
    return _to_request_public(db_request)


def list_requests_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> AIAPIRequestsPublic:
    count_query = (
        select(func.count())
        .select_from(AIAPIRequest)
        .where(AIAPIRequest.user_id == user_id)
    )
    data_query = (
        select(AIAPIRequest)
        .where(AIAPIRequest.user_id == user_id)
        .order_by(AIAPIRequest.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return AIAPIRequestsPublic(
        data=[_to_request_public(item) for item in session.exec(data_query).all()],
        count=int(session.exec(count_query).one()),
    )


def list_all_requests(
    *,
    session: Session,
    status: AIAPIRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> AIAPIRequestsPublic:
    count_query = select(func.count()).select_from(AIAPIRequest)
    data_query = select(AIAPIRequest)
    if status is not None:
        count_query = count_query.where(AIAPIRequest.status == status)
        data_query = data_query.where(AIAPIRequest.status == status)
    data_query = (
        data_query.order_by(AIAPIRequest.created_at.desc()).offset(skip).limit(limit)
    )
    return AIAPIRequestsPublic(
        data=[_to_request_public(item) for item in session.exec(data_query).all()],
        count=int(session.exec(count_query).one()),
    )


def get_request(
    *, session: Session, request_id: uuid.UUID, current_user
) -> AIAPIRequestPublic:
    db_request = session.get(AIAPIRequest, request_id)
    if not db_request:
        raise NotFoundError(t("ai_gateway.request_not_found"))
    require_ai_api_access(current_user, db_request.user_id)
    return _to_request_public(db_request)


def review_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    review_data: AIAPIRequestReview,
    reviewer,
) -> AIAPIRequestPublic:
    if review_data.status not in _REVIEW_DECISIONS:
        raise BadRequestError(t("ai_gateway.invalid_review_status"))
    # 交易層級的列鎖（PgBouncer transaction pooling 下安全）：兩位審核者同時
    # 核准時，後到的會等前者 commit，再看到已非 pending 而被擋下，不會發兩把金鑰
    db_request = session.exec(
        select(AIAPIRequest)
        .where(AIAPIRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if not db_request:
        raise NotFoundError(t("ai_gateway.request_not_found"))
    if db_request.status != AIAPIRequestStatus.pending:
        raise BadRequestError(t("ai_gateway.request_already_reviewed"))
    if (
        review_data.status == AIAPIRequestStatus.approved
        and db_request.duration not in KEY_DURATIONS
    ):
        # 舊資料的效期字串無法換算；不能默默發出永不過期的金鑰
        raise BadRequestError(t("ai_gateway.invalid_duration"))

    db_request.status = review_data.status
    db_request.reviewer_id = reviewer.id
    db_request.review_comment = (
        review_data.review_comment.strip() if review_data.review_comment else None
    )
    db_request.reviewed_at = get_datetime_utc()
    session.add(db_request)

    if review_data.status == AIAPIRequestStatus.approved:
        base_url = ai_api_settings.resolved_public_base_url
        api_key = _generate_user_api_key()
        if not base_url:
            raise BadRequestError(t("ai_gateway.connection_incomplete"))

        lifetime = KEY_DURATIONS[db_request.duration]
        expires_at = get_datetime_utc() + lifetime if lifetime is not None else None

        session.add(
            AIAPICredential(
                user_id=db_request.user_id,
                request_id=db_request.id,
                base_url=base_url,
                api_key_encrypted=encrypt_value(api_key),
                api_key_prefix=_credential_prefix(api_key),
                api_key_name=db_request.api_key_name,
                rate_limit=db_request.rate_limit,  # 繼承申請的 rate_limit
                expires_at=expires_at,
            )
        )

    action = (
        "approved" if review_data.status == AIAPIRequestStatus.approved else "rejected"
    )
    details = f"Reviewed AI API request {request_id}: {action}"
    if db_request.review_comment:
        details += f". Comment: {db_request.review_comment}"
    audit_service.log_action(
        session=session,
        user_id=reviewer.id,
        action="ai_api_request_review",
        details=details,
        commit=False,
    )

    session.commit()
    session.refresh(db_request)
    logger.info("Admin %s %s AI API request %s", reviewer.email, action, request_id)
    return _to_request_public(db_request)


def list_credentials_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> AIAPICredentialsPublic:
    count_query = (
        select(func.count())
        .select_from(AIAPICredential)
        .where(AIAPICredential.user_id == user_id)
    )
    data_query = (
        select(AIAPICredential)
        .where(AIAPICredential.user_id == user_id)
        .order_by(AIAPICredential.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return AIAPICredentialsPublic(
        data=[_to_credential_public(item) for item in session.exec(data_query).all()],
        count=int(session.exec(count_query).one()),
    )


def list_all_credentials(
    *,
    session: Session,
    status: str | None = None,
    user_email: str | None = None,
    query: str | None = None,
    user_roles: list[str] | None = None,
    created_after: datetime | None = None,
    skip: int = 0,
    limit: int = 100,
) -> AIAPICredentialsAdminPublic:
    now = get_datetime_utc()

    reviewer = aliased(User)
    usage_subquery = (
        select(
            AIAPIUsage.credential_id,
            func.max(AIAPIUsage.created_at).label("last_used_at"),
        )
        .where(AIAPIUsage.source == USAGE_SOURCE_API_KEY)
        .group_by(AIAPIUsage.credential_id)
        .subquery("credential_last_usage")
    )
    base_from = (
        select(AIAPICredential.id)
        .select_from(AIAPICredential)
        .join(User, User.id == AIAPICredential.user_id)
    )
    data_query = (
        select(
            AIAPICredential,
            User,
            AIAPIRequest,
            reviewer,
            usage_subquery.c.last_used_at,
        )
        .join(User, User.id == AIAPICredential.user_id)
        .join(AIAPIRequest, AIAPIRequest.id == AIAPICredential.request_id)
        .outerjoin(reviewer, reviewer.id == AIAPIRequest.reviewer_id)
        .outerjoin(
            usage_subquery,
            usage_subquery.c.credential_id == AIAPICredential.id,
        )
    )

    keyword = query.strip() if query and query.strip() else (user_email or "").strip()
    filters = []
    if keyword:
        like_pattern = f"%{keyword}%"
        filters.append(
            or_(
                User.email.ilike(like_pattern),
                User.full_name.ilike(like_pattern),
                AIAPICredential.api_key_name.ilike(like_pattern),
                AIAPICredential.api_key_prefix.ilike(like_pattern),
            )
        )
    if user_roles:
        filters.append(User.role.in_(user_roles))
    if created_after is not None:
        filters.append(AIAPICredential.created_at >= created_after)

    active_clause = and_(
        AIAPICredential.revoked_at.is_(None),
        or_(AIAPICredential.expires_at.is_(None), AIAPICredential.expires_at > now),
    )
    inactive_clause = or_(
        AIAPICredential.revoked_at.is_not(None),
        and_(
            AIAPICredential.expires_at.is_not(None), AIAPICredential.expires_at <= now
        ),
    )

    base_filters = list(filters)
    if status == "active":
        filters.append(active_clause)
    elif status == "inactive":
        filters.append(inactive_clause)

    count_query = base_from.where(*filters)

    def count_rows(query_to_count: Any) -> int:
        count_statement = select(func.count()).select_from(query_to_count.subquery())
        return int(session.exec(count_statement).one())

    summary_query = base_from.where(*base_filters)
    total_count_value = count_rows(summary_query)
    active_count = count_rows(summary_query.where(active_clause))
    inactive_count = count_rows(summary_query.where(inactive_clause))

    data_query = (
        data_query.where(*filters)
        .order_by(AIAPICredential.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = session.exec(data_query).all()

    return AIAPICredentialsAdminPublic(
        data=[
            _to_credential_admin_public(
                credential=credential,
                user=user,
                request=request,
                reviewer=reviewer_user,
                last_used_at=last_used_at,
                now=now,
            )
            for credential, user, request, reviewer_user, last_used_at in rows
        ],
        count=count_rows(count_query),
        total_count=total_count_value,
        active_count=active_count,
        inactive_count=inactive_count,
    )


def rotate_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> AIAPICredentialWithSecret:
    credential = _get_manageable_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    if credential.revoked_at is not None:
        raise BadRequestError(t("ai_gateway.credential_already_revoked"))

    credential.revoked_at = get_datetime_utc()
    session.add(credential)

    new_api_key = _generate_user_api_key()

    new_credential = AIAPICredential(
        user_id=credential.user_id,
        request_id=credential.request_id,
        base_url=credential.base_url,
        api_key_encrypted=encrypt_value(new_api_key),
        api_key_prefix=_credential_prefix(new_api_key),
        api_key_name=credential.api_key_name,
        rate_limit=credential.rate_limit,
        expires_at=credential.expires_at,
    )
    session.add(new_credential)

    on_behalf = _acting_on_behalf(credential, current_user)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_rotate",
        details=(
            f"Rotated AI API credential {credential_id}"
            f"{_audit_suffix(credential, current_user)}"
        ),
        commit=False,
    )

    session.commit()
    session.refresh(new_credential)
    # 代操時不回明文：管理員的目的是撤換別人的金鑰，不是取得它
    return _to_credential_with_secret(
        new_credential, api_key=None if on_behalf else new_api_key
    )


def delete_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> Message:
    credential = _get_manageable_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    # A credential referenced by AIAPIUsage is part of the audit trail and
    # cannot be hard-deleted without violating its foreign key. Revoking it
    # preserves accounting while immediately making the ccai_* key unusable.
    usage_exists = session.exec(
        select(AIAPIUsage.id).where(AIAPIUsage.credential_id == credential.id).limit(1)
    ).first()
    if usage_exists:
        if credential.revoked_at is None:
            credential.revoked_at = get_datetime_utc()
            session.add(credential)
        operation = "revoked"
    else:
        session.delete(credential)
        operation = "deleted"
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_delete",
        details=(
            f"{operation.title()} AI API credential {credential_id}"
            f"{_audit_suffix(credential, current_user)}"
        ),
        commit=False,
    )
    session.commit()
    return Message(message=f"AI API credential {operation} successfully")


def update_credential_name(
    *, session: Session, credential_id: uuid.UUID, name: str, current_user
) -> AIAPICredentialPublic:
    credential = _get_manageable_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    credential.api_key_name = name
    session.add(credential)

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_update",
        details=(
            f"Renamed AI API credential {credential_id} to '{name}'"
            f"{_audit_suffix(credential, current_user)}"
        ),
        commit=False,
    )

    session.commit()
    session.refresh(credential)
    return _to_credential_public(credential)


# ===== 新增：使用量记录功能 =====


def record_usage(
    *,
    session: Session,
    user_id: uuid.UUID,
    credential_id: uuid.UUID,
    model_name: str,
    request_type: str,
    request_id: str | None = None,
    upstream_request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_duration_ms: int | None = None,
    first_token_ms: int | None = None,
    stream: bool = False,
    usage_reported: bool = False,
    response_model: str | None = None,
    status: str = "success",
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """
    記錄 AI API Proxy 使用量

    Args:
        session: 資料庫會話
        user_id: 使用者 ID
        credential_id: 憑證 ID
        model_name: 模型名稱
        request_type: 請求類型（chat_completion 等）
        input_tokens: 輸入 tokens
        output_tokens: 輸出 tokens
        request_duration_ms: 請求耗時（毫秒）
        status: 狀態（success, error）
        error_message: 錯誤訊息
    """
    usage = AIAPIUsage(
        user_id=user_id,
        source=USAGE_SOURCE_API_KEY,
        credential_id=credential_id,
        model_name=model_name,
        call_type=request_type,
        request_id=request_id,
        upstream_request_id=upstream_request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_duration_ms=request_duration_ms,
        first_token_ms=first_token_ms,
        stream=stream,
        usage_reported=usage_reported,
        response_model=response_model,
        status=status,
        error_message=error_message,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(usage)
    session.commit()
    logger.info(
        "Recorded proxy usage: user=%s, model=%s, in=%d, out=%d",
        user_id,
        model_name,
        input_tokens,
        output_tokens,
    )


def record_template_call(
    *,
    session: Session,
    user_id: uuid.UUID,
    call_type: str,
    model_name: str,
    preset: str | None = None,
    request_id: str | None = None,
    upstream_request_id: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_duration_ms: int | None = None,
    first_token_ms: int | None = None,
    stream: bool = False,
    usage_reported: bool = False,
    response_model: str | None = None,
    status: str = "success",
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """
    記錄 AI Template 呼叫（chat / recommend）

    Args:
        session: 資料庫會話
        user_id: 使用者 ID
        call_type: 呼叫類型（"chat" | "recommend"）
        model_name: 模型名稱
        preset: 推薦 preset（recommend 才有）
        input_tokens: 輸入 tokens
        output_tokens: 輸出 tokens
        request_duration_ms: 請求耗時（毫秒）
        status: 狀態（success, error）
        error_message: 錯誤訊息
    """
    log = AIAPIUsage(
        user_id=user_id,
        source=USAGE_SOURCE_PLATFORM,
        credential_id=None,
        call_type=call_type,
        model_name=model_name,
        preset=preset,
        request_id=request_id,
        upstream_request_id=upstream_request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_duration_ms=request_duration_ms,
        first_token_ms=first_token_ms,
        stream=stream,
        usage_reported=usage_reported,
        response_model=response_model,
        status=status,
        error_message=error_message,
        started_at=started_at,
        completed_at=completed_at,
    )
    session.add(log)
    session.commit()
    logger.info(
        "Recorded template call: user=%s, type=%s, model=%s, in=%d, out=%d",
        user_id,
        call_type,
        model_name,
        input_tokens,
        output_tokens,
    )


# ===== 新增：查询使用统计 =====


def _usage_timezone(tz: str | None) -> tzinfo:
    """使用者瀏覽器的 IANA 時區；缺漏或無效時退回 UTC（舊版行為）。"""
    if not tz:
        return timezone.utc
    try:
        return ZoneInfo(tz)
    # 某些平台對 "Asia" 這類目錄名稱丟 PermissionError／IsADirectoryError 等
    # OSError；一律當成無效時區
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return timezone.utc


def _local_date(value: datetime, zone: tzinfo) -> date:
    """查詢參數沒帶時區時視為 UTC（不能讓 astimezone 套用伺服器本機時區）。"""
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(zone).date()


def _usage_day_expr(
    session: Session, column: Any, *, zone: tzinfo, reference: datetime
) -> Any:
    """把 timestamptz 欄位轉成「使用者當地的日期」的 SQL 運算式。

    PostgreSQL 用 ``timezone(name, ts)`` 換算（含夏令時間）。SQLite 沒有 IANA
    時區，只能以區間結束時的 UTC 偏移近似，僅供測試環境使用。時區名稱以
    literal 內嵌，SELECT 與 GROUP BY 才會是同一個運算式。
    """
    if session.get_bind().dialect.name == "sqlite":
        aware = (
            reference if reference.tzinfo else reference.replace(tzinfo=timezone.utc)
        )
        offset = aware.astimezone(zone).utcoffset() or timedelta(0)
        minutes = int(offset.total_seconds() // 60)
        return func.strftime(
            "%Y-%m-%d",
            column,
            sa.literal(f"{minutes:+d} minutes", literal_execute=True),
        )
    name = zone.key if isinstance(zone, ZoneInfo) else "UTC"
    return sa.cast(
        func.timezone(sa.literal(name, literal_execute=True), column), sa.Date
    )


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _daily_usage_buckets(
    rows: Iterable[Any],
    *,
    start_date: datetime,
    end_date: datetime,
    zone: tzinfo = timezone.utc,
) -> list[dict[str, Any]]:
    """把已按「當地日期」聚合的用量列補成連續日序列（我的用量折線圖用，#7）。

    ``rows`` 為 ``(day, requests, input_tokens, output_tokens)``。區間內沒有
    呼叫的日子補零，X 軸才連續；區間異常大（>400 天）時不補零，只回傳實際
    有紀錄的日子，避免產生上千個空桶。
    """
    start_day = _local_date(start_date, zone)
    end_day = _local_date(end_date, zone)
    span = (end_day - start_day).days
    buckets: dict[date, dict[str, Any]] = {}
    if 0 <= span <= 400:
        day = start_day
        while day <= end_day:
            buckets[day] = {
                "date": day,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
            day += timedelta(days=1)
    for raw_day, requests, input_tokens, output_tokens in rows:
        day = _as_date(raw_day)
        bucket = buckets.get(day)
        if bucket is None:
            bucket = {"date": day, "requests": 0, "input_tokens": 0, "output_tokens": 0}
            buckets[day] = bucket
        bucket["requests"] += int(requests or 0)
        bucket["input_tokens"] += int(input_tokens or 0)
        bucket["output_tokens"] += int(output_tokens or 0)
    return [buckets[key] for key in sorted(buckets)]


def _aggregate_user_usage(
    *,
    session: Session,
    source: str,
    group_column: Any,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
    zone: tzinfo,
) -> tuple[list[Any], list[Any]]:
    """在資料庫端依群組欄位與當地日期聚合，不把逐筆紀錄載入記憶體。"""
    filters = (
        AIAPIUsage.user_id == user_id,
        AIAPIUsage.source == source,
        AIAPIUsage.created_at >= start_date,
        AIAPIUsage.created_at <= end_date,
    )
    count = func.count(AIAPIUsage.id)
    input_sum = func.coalesce(func.sum(AIAPIUsage.input_tokens), 0)
    output_sum = func.coalesce(func.sum(AIAPIUsage.output_tokens), 0)
    grouped = session.exec(
        select(group_column, count, input_sum, output_sum)
        .where(*filters)
        .group_by(group_column)
    ).all()
    day_expr = _usage_day_expr(
        session, AIAPIUsage.created_at, zone=zone, reference=end_date
    )
    daily = session.exec(
        select(day_expr, count, input_sum, output_sum)
        .where(*filters)
        .group_by(day_expr)
    ).all()
    return list(grouped), list(daily)


def get_user_usage_stats(
    *,
    session: Session,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
    tz: str | None = None,
) -> dict:
    """
    查詢使用者的 Proxy 使用統計

    ``tz`` 為瀏覽器的 IANA 時區，決定每日分桶以哪一地的日期切日。
    """
    zone = _usage_timezone(tz)
    grouped, daily = _aggregate_user_usage(
        session=session,
        source=USAGE_SOURCE_API_KEY,
        group_column=AIAPIUsage.model_name,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        zone=zone,
    )
    by_model: dict[str, dict[str, int]] = {
        model_name: {
            "requests": int(requests),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
        for model_name, requests, input_tokens, output_tokens in grouped
    }

    return {
        "total_requests": sum(item["requests"] for item in by_model.values()),
        "total_input_tokens": sum(item["input_tokens"] for item in by_model.values()),
        "total_output_tokens": sum(item["output_tokens"] for item in by_model.values()),
        "by_model": by_model,
        "daily": _daily_usage_buckets(
            daily, start_date=start_date, end_date=end_date, zone=zone
        ),
        "start_date": start_date,
        "end_date": end_date,
    }


def get_user_template_usage_stats(
    *,
    session: Session,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
    tz: str | None = None,
) -> dict:
    """
    查詢使用者的 Template 呼叫統計
    """
    zone = _usage_timezone(tz)
    grouped, daily = _aggregate_user_usage(
        session=session,
        source=USAGE_SOURCE_PLATFORM,
        group_column=AIAPIUsage.call_type,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        zone=zone,
    )
    by_call_type: dict[str, dict[str, int]] = {
        call_type: {
            "calls": int(calls),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
        }
        for call_type, calls, input_tokens, output_tokens in grouped
    }

    return {
        "total_calls": sum(item["calls"] for item in by_call_type.values()),
        "total_input_tokens": sum(
            item["input_tokens"] for item in by_call_type.values()
        ),
        "total_output_tokens": sum(
            item["output_tokens"] for item in by_call_type.values()
        ),
        "by_call_type": by_call_type,
        "daily": _daily_usage_buckets(
            daily, start_date=start_date, end_date=end_date, zone=zone
        ),
        "start_date": start_date,
        "end_date": end_date,
    }


# ===== 統一用量（整合 Proxy / Template 兩種計算路由） =====

ROUTE_MODEL = "model"


def list_user_usage_records(
    *,
    session: Session,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
    skip: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    """查詢使用者透過申請金鑰發出的逐筆 API 呼叫紀錄。"""
    filters = (
        AIAPIUsage.user_id == user_id,
        AIAPIUsage.source == USAGE_SOURCE_API_KEY,
        AIAPIUsage.created_at >= start_date,
        AIAPIUsage.created_at <= end_date,
    )
    count = int(
        session.exec(select(func.count()).select_from(AIAPIUsage).where(*filters)).one()
        or 0
    )
    rows = session.exec(
        select(AIAPIUsage, AIAPICredential)
        .join(AIAPICredential, AIAPICredential.id == AIAPIUsage.credential_id)
        .where(*filters)
        .order_by(AIAPIUsage.created_at.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    return {
        "data": [
            {
                **_call_metrics(usage),
                "id": usage.id,
                "route": ROUTE_MODEL,
                "credential_id": credential.id,
                "api_key_name": credential.api_key_name,
                "api_key_prefix": credential.api_key_prefix,
                "call_type": usage.call_type,
                "preset": None,
                "total_tokens": usage.input_tokens + usage.output_tokens,
            }
            for usage, credential in rows
        ],
        "count": count,
    }


# ===== Admin 監控功能 =====


def _monitoring_filters(
    source: str, start_date: datetime | None, end_date: datetime | None
):
    filters = [AIAPIUsage.source == source]
    if start_date:
        filters.append(AIAPIUsage.created_at >= start_date)
    if end_date:
        filters.append(AIAPIUsage.created_at <= end_date)
    return filters


#: _usage_aggregate_columns 產生的欄位名稱（不含前綴），順序與欄位一致
USAGE_AGGREGATE_NAMES = (
    "total_calls",
    "successful_calls",
    "input_tokens",
    "output_tokens",
    "duration_count",
    "duration_sum",
)


def _usage_aggregate_columns(prefix: str = "") -> list[Any]:
    """AIAPIUsage 兩種來源（金鑰／平台功能）共用的六個用量聚合欄位。

    欄位依 ``USAGE_AGGREGATE_NAMES`` 命名並加上 ``prefix``（兩個來源的子查詢
    要 join 在一起時用來區分）。來源由呼叫端以 ``_monitoring_filters`` 過濾。
    成功與否一律以 MONITORING_SUCCESS_STATUSES 判斷。
    """
    success = case((AIAPIUsage.status.in_(MONITORING_SUCCESS_STATUSES), 1), else_=0)
    columns = (
        func.count(AIAPIUsage.id),
        func.coalesce(func.sum(success), 0),
        func.coalesce(func.sum(AIAPIUsage.input_tokens), 0),
        func.coalesce(func.sum(AIAPIUsage.output_tokens), 0),
        func.count(AIAPIUsage.request_duration_ms),
        func.coalesce(func.sum(AIAPIUsage.request_duration_ms), 0),
    )
    return [
        column.label(f"{prefix}{name}")
        for column, name in zip(columns, USAGE_AGGREGATE_NAMES, strict=True)
    ]


def _monitoring_bucket_rows(
    *,
    session: Session,
    source: str,
    bucket: str,
    start_date: datetime | None,
    end_date: datetime | None,
):
    """依時間 bucket 聚合單一 AI 使用表，避免前端用有限明細反推趨勢。"""
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "sqlite":
        sqlite_format = "%Y-%m-%d %H:00:00" if bucket == "hour" else "%Y-%m-%d 00:00:00"
        bucket_expr = func.strftime(sqlite_format, AIAPIUsage.created_at).label(
            "bucket_start"
        )
    else:
        bucket_expr = func.date_trunc(bucket, AIAPIUsage.created_at).label(
            "bucket_start"
        )
    statement = select(bucket_expr, *_usage_aggregate_columns()).where(
        *_monitoring_filters(source, start_date, end_date)
    )
    statement = statement.group_by(bucket_expr).order_by(bucket_expr)
    return session.exec(statement).all()


def _monitoring_bucket_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _monitoring_model_rows(
    *,
    session: Session,
    source: str,
    start_date: datetime | None,
    end_date: datetime | None,
):
    statement = select(AIAPIUsage.model_name, *_usage_aggregate_columns()).where(
        *_monitoring_filters(source, start_date, end_date)
    )
    statement = statement.group_by(AIAPIUsage.model_name)
    return session.exec(statement).all()


def _monitoring_summary(stats: dict) -> dict:
    total_calls = int(stats.get("proxy_total_calls", 0) or 0) + int(
        stats.get("template_total_calls", 0) or 0
    )
    successful_calls = int(stats.get("successful_calls", 0) or 0)
    failed_calls = max(0, total_calls - successful_calls)
    total_tokens = sum(
        int(stats.get(field, 0) or 0)
        for field in (
            "proxy_total_input_tokens",
            "proxy_total_output_tokens",
            "template_total_input_tokens",
            "template_total_output_tokens",
        )
    )
    return {
        "total_calls": total_calls,
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "error_rate": None
        if total_calls == 0
        else round(failed_calls / total_calls * 100, 2),
        "total_tokens": total_tokens,
        "avg_latency_ms": (
            None
            if total_calls == 0 or stats.get("avg_latency_ms") is None
            else int(stats["avg_latency_ms"])
        ),
        "active_users": int(stats.get("active_users", 0) or 0),
    }


def _monitoring_percent_delta(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def get_monitoring_overview(
    *,
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    bucket: str = "hour",
    compare: bool = True,
    include_template: bool = True,
) -> dict:
    """回傳 AI 監控首頁使用的趨勢、比較與模型聚合資料。"""
    if bucket not in {"hour", "day"}:
        raise ValueError("bucket must be hour or day")

    current_stats = get_monitoring_stats(
        session=session,
        start_date=start_date,
        end_date=end_date,
        include_template=include_template,
    )
    current_summary = _monitoring_summary(current_stats)

    comparison = {
        "total_calls_delta": 0,
        "total_calls_percent": None,
        "failed_calls_delta": 0,
        "failed_calls_percent": None,
        "error_rate_delta": None,
        "avg_latency_ms_delta": None,
    }
    if compare and start_date and end_date and end_date > start_date:
        period = end_date - start_date
        previous_end = start_date - timedelta(microseconds=1)
        previous_stats = get_monitoring_stats(
            session=session,
            start_date=previous_end - period,
            end_date=previous_end,
            include_template=include_template,
        )
        previous_summary = _monitoring_summary(previous_stats)
        comparison = {
            "total_calls_delta": current_summary["total_calls"]
            - previous_summary["total_calls"],
            "total_calls_percent": _monitoring_percent_delta(
                current_summary["total_calls"], previous_summary["total_calls"]
            ),
            "failed_calls_delta": current_summary["failed_calls"]
            - previous_summary["failed_calls"],
            "failed_calls_percent": _monitoring_percent_delta(
                current_summary["failed_calls"], previous_summary["failed_calls"]
            ),
            "error_rate_delta": (
                None
                if current_summary["error_rate"] is None
                or previous_summary["error_rate"] is None
                else round(
                    current_summary["error_rate"] - previous_summary["error_rate"], 2
                )
            ),
            "avg_latency_ms_delta": (
                None
                if current_summary["avg_latency_ms"] is None
                or previous_summary["avg_latency_ms"] is None
                else current_summary["avg_latency_ms"]
                - previous_summary["avg_latency_ms"]
            ),
        }

    series_by_bucket = {}
    source_models = [(USAGE_SOURCE_API_KEY, "proxy_calls")]
    if include_template:
        source_models.append((USAGE_SOURCE_PLATFORM, "template_calls"))
    for source, source_key in source_models:
        for row in _monitoring_bucket_rows(
            session=session,
            source=source,
            bucket=bucket,
            start_date=start_date,
            end_date=end_date,
        ):
            bucket_start = _monitoring_bucket_datetime(row[0])
            if bucket_start is None:
                continue
            item = series_by_bucket.setdefault(
                bucket_start,
                {
                    "bucket_start": bucket_start,
                    "total_calls": 0,
                    "successful_calls": 0,
                    "total_tokens": 0,
                    "avg_latency_sum": 0.0,
                    "avg_latency_count": 0,
                    "proxy_calls": 0,
                    "template_calls": 0,
                },
            )
            total_calls = int(row[1] or 0)
            successful_calls = int(row[2] or 0)
            item["total_calls"] += total_calls
            item["successful_calls"] += successful_calls
            item["total_tokens"] += int(row[3] or 0) + int(row[4] or 0)
            item[source_key] += total_calls
            duration_count = int(row[5] or 0)
            if duration_count:
                item["avg_latency_sum"] += float(row[6] or 0)
                item["avg_latency_count"] += duration_count

    series = []
    for item in sorted(
        series_by_bucket.values(), key=lambda value: value["bucket_start"]
    ):
        total_calls = item["total_calls"]
        failed_calls = total_calls - item["successful_calls"]
        series.append(
            {
                "bucket_start": item["bucket_start"],
                "total_calls": total_calls,
                "successful_calls": item["successful_calls"],
                "failed_calls": failed_calls,
                "total_tokens": item["total_tokens"],
                "error_rate": (
                    None
                    if total_calls == 0
                    else round(failed_calls / total_calls * 100, 2)
                ),
                "avg_latency_ms": (
                    round(item["avg_latency_sum"] / item["avg_latency_count"])
                    if item["avg_latency_count"]
                    else None
                ),
                "proxy_calls": item["proxy_calls"],
                "template_calls": item["template_calls"],
            }
        )

    model_totals = {}
    sources = [USAGE_SOURCE_API_KEY]
    if include_template:
        sources.append(USAGE_SOURCE_PLATFORM)
    for source in sources:
        for row in _monitoring_model_rows(
            session=session,
            source=source,
            start_date=start_date,
            end_date=end_date,
        ):
            model_name = str(row[0] or "unknown")
            item = model_totals.setdefault(
                model_name,
                {
                    "total_calls": 0,
                    "successful_calls": 0,
                    "total_tokens": 0,
                    "avg_latency_sum": 0.0,
                    "avg_latency_count": 0,
                },
            )
            total_calls = int(row[1] or 0)
            item["total_calls"] += total_calls
            item["successful_calls"] += int(row[2] or 0)
            item["total_tokens"] += int(row[3] or 0) + int(row[4] or 0)
            duration_count = int(row[5] or 0)
            if duration_count:
                item["avg_latency_sum"] += float(row[6] or 0)
                item["avg_latency_count"] += duration_count

    model_breakdown = []
    for model_name, item in sorted(
        model_totals.items(), key=lambda entry: entry[1]["total_calls"], reverse=True
    ):
        total_calls = item["total_calls"]
        failed_calls = total_calls - item["successful_calls"]
        model_breakdown.append(
            {
                "model_name": model_name,
                "total_calls": total_calls,
                "total_tokens": item["total_tokens"],
                "failed_calls": failed_calls,
                "error_rate": (
                    None
                    if total_calls == 0
                    else round(failed_calls / total_calls * 100, 2)
                ),
                "avg_latency_ms": (
                    round(item["avg_latency_sum"] / item["avg_latency_count"])
                    if item["avg_latency_count"]
                    else None
                ),
            }
        )

    return {
        "start_date": start_date,
        "end_date": end_date,
        "bucket": bucket,
        "summary": current_summary,
        "comparison": comparison,
        "series": series,
        "model_breakdown": model_breakdown,
    }


def get_monitoring_stats(
    *,
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    include_template: bool = True,
) -> dict:
    """全局 AI 監控統計卡片"""
    proxy_filters = _monitoring_filters(USAGE_SOURCE_API_KEY, start_date, end_date)
    template_filters = _monitoring_filters(USAGE_SOURCE_PLATFORM, start_date, end_date)

    def _totals(filters) -> dict[str, int]:
        row = session.exec(select(*_usage_aggregate_columns()).where(*filters)).one()
        return {name: int(row._mapping[name] or 0) for name in USAGE_AGGREGATE_NAMES}

    proxy_totals = _totals(proxy_filters)
    template_totals = (
        _totals(template_filters)
        if include_template
        else dict.fromkeys(USAGE_AGGREGATE_NAMES, 0)
    )
    proxy_total_calls = proxy_totals["total_calls"]
    template_total_calls = template_totals["total_calls"]
    total_calls = proxy_total_calls + template_total_calls
    successful_calls = (
        proxy_totals["successful_calls"] + template_totals["successful_calls"]
    )
    failed_calls = max(0, total_calls - successful_calls)
    duration_count = proxy_totals["duration_count"] + template_totals["duration_count"]
    duration_sum = proxy_totals["duration_sum"] + template_totals["duration_sum"]

    # 活躍使用者（proxy + template 的 distinct user_id 合集）
    proxy_user_ids = set(
        session.exec(select(distinct(AIAPIUsage.user_id)).where(*proxy_filters)).all()
    )
    template_user_ids = (
        set(
            session.exec(
                select(distinct(AIAPIUsage.user_id)).where(*template_filters)
            ).all()
        )
        if include_template
        else set()
    )
    active_users = len(proxy_user_ids | template_user_ids)

    # 使用的模型列表
    template_models = (
        set(
            session.exec(
                select(distinct(AIAPIUsage.model_name)).where(*template_filters)
            ).all()
        )
        if include_template
        else set()
    )
    proxy_models = set(
        session.exec(
            select(distinct(AIAPIUsage.model_name)).where(*proxy_filters)
        ).all()
    )
    models = sorted(proxy_models | template_models)

    return {
        "proxy_total_calls": proxy_total_calls,
        "proxy_total_input_tokens": proxy_totals["input_tokens"],
        "proxy_total_output_tokens": proxy_totals["output_tokens"],
        "template_total_calls": template_total_calls,
        "template_total_input_tokens": template_totals["input_tokens"],
        "template_total_output_tokens": template_totals["output_tokens"],
        "successful_calls": successful_calls,
        "failed_calls": failed_calls,
        "success_rate": 100
        if total_calls == 0
        else round((successful_calls / total_calls) * 100),
        "avg_latency_ms": 0
        if duration_count == 0
        else round(duration_sum / duration_count),
        "active_users": active_users,
        "models_used": models,
    }


def list_proxy_calls(
    *,
    session: Session,
    user_id: uuid.UUID | None = None,
    model_name: str | None = None,
    call_status: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> dict:
    """Admin: 列出 Proxy 呼叫紀錄"""
    count_query = select(func.count()).select_from(AIAPIUsage)
    data_query = select(AIAPIUsage, User).join(User, User.id == AIAPIUsage.user_id)

    filters = [AIAPIUsage.source == USAGE_SOURCE_API_KEY]
    if user_id:
        filters.append(AIAPIUsage.user_id == user_id)
    if model_name:
        filters.append(AIAPIUsage.model_name.ilike(f"%{model_name}%"))
    if call_status:
        filters.append(AIAPIUsage.status == call_status)
    if start_date:
        filters.append(AIAPIUsage.created_at >= start_date)
    if end_date:
        filters.append(AIAPIUsage.created_at <= end_date)

    for f in filters:
        count_query = count_query.where(f)
        data_query = data_query.where(f)

    data_query = (
        data_query.order_by(AIAPIUsage.created_at.desc()).offset(skip).limit(limit)
    )

    total = int(session.exec(count_query).one() or 0)
    rows = session.exec(data_query).all()

    records = [
        {
            **_call_metrics(usage),
            "id": usage.id,
            "user_id": usage.user_id,
            "user_email": user.email,
            "user_full_name": user.full_name,
            "credential_id": usage.credential_id,
            "request_type": usage.call_type,
        }
        for usage, user in rows
    ]

    return {"data": records, "count": total}


def list_template_calls(
    *,
    session: Session,
    user_id: uuid.UUID | None = None,
    call_type: str | None = None,
    preset: str | None = None,
    call_status: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> dict:
    """Admin: 列出 Template 呼叫紀錄"""
    count_query = select(func.count()).select_from(AIAPIUsage)
    data_query = select(AIAPIUsage, User).join(User, User.id == AIAPIUsage.user_id)

    filters = [AIAPIUsage.source == USAGE_SOURCE_PLATFORM]
    if user_id:
        filters.append(AIAPIUsage.user_id == user_id)
    if call_type:
        filters.append(AIAPIUsage.call_type == call_type)
    if preset:
        filters.append(AIAPIUsage.preset == preset)
    if call_status:
        filters.append(AIAPIUsage.status == call_status)
    if start_date:
        filters.append(AIAPIUsage.created_at >= start_date)
    if end_date:
        filters.append(AIAPIUsage.created_at <= end_date)

    for f in filters:
        count_query = count_query.where(f)
        data_query = data_query.where(f)

    data_query = (
        data_query.order_by(AIAPIUsage.created_at.desc()).offset(skip).limit(limit)
    )

    total = int(session.exec(count_query).one() or 0)
    rows = session.exec(data_query).all()

    records = [
        {
            **_call_metrics(log),
            "id": log.id,
            "user_id": log.user_id,
            "user_email": user.email,
            "user_full_name": user.full_name,
            "call_type": log.call_type,
            "preset": log.preset,
        }
        for log, user in rows
    ]

    return {"data": records, "count": total}


def list_users_usage(
    *,
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
    include_template: bool = True,
) -> dict:
    """Admin: 每個使用者的 AI 用量彙總"""
    # 先在資料庫完成兩個來源的 per-user 聚合，再一次 join 使用者資料。
    # 舊實作在分頁後對每位使用者執行 session.get + 2 次聚合查詢，
    # 100 位使用者就會產生 300+ 次 round-trip。
    proxy_sub = (
        select(AIAPIUsage.user_id, *_usage_aggregate_columns("proxy_"))
        .where(*_monitoring_filters(USAGE_SOURCE_API_KEY, start_date, end_date))
        .group_by(AIAPIUsage.user_id)
        .subquery("proxy_usage")
    )

    if not include_template:
        usage_query = (
            select(
                User.id,
                User.email,
                User.full_name,
                proxy_sub.c.proxy_total_calls,
                proxy_sub.c.proxy_input_tokens,
                proxy_sub.c.proxy_output_tokens,
                proxy_sub.c.proxy_successful_calls,
                proxy_sub.c.proxy_duration_count,
                proxy_sub.c.proxy_duration_sum,
            )
            .select_from(User)
            .join(proxy_sub, proxy_sub.c.user_id == User.id)
        )
        total_count = int(
            session.exec(select(func.count()).select_from(usage_query.subquery())).one()
            or 0
        )
        total_tokens = proxy_sub.c.proxy_input_tokens + proxy_sub.c.proxy_output_tokens
        rows = session.exec(
            usage_query.order_by(total_tokens.desc(), User.id).offset(skip).limit(limit)
        ).all()
        results = []
        for row in rows:
            total_calls = int(row.proxy_total_calls or 0)
            successful_calls = int(row.proxy_successful_calls or 0)
            failed_calls = max(0, total_calls - successful_calls)
            duration_count = int(row.proxy_duration_count or 0)
            duration_sum = int(row.proxy_duration_sum or 0)
            results.append(
                {
                    "user_id": row.id,
                    "user_email": row.email,
                    "user_full_name": row.full_name,
                    "proxy_calls": total_calls,
                    "proxy_input_tokens": int(row.proxy_input_tokens or 0),
                    "proxy_output_tokens": int(row.proxy_output_tokens or 0),
                    "template_calls": 0,
                    "template_input_tokens": 0,
                    "template_output_tokens": 0,
                    "failed_calls": failed_calls,
                    "error_rate": (
                        None
                        if total_calls == 0
                        else round(failed_calls / total_calls * 100, 2)
                    ),
                    "avg_latency_ms": (
                        round(duration_sum / duration_count) if duration_count else None
                    ),
                }
            )
        return {"data": results, "count": total_count}

    # Template 用量 per user
    tmpl_sub = (
        select(AIAPIUsage.user_id, *_usage_aggregate_columns("tmpl_"))
        .where(*_monitoring_filters(USAGE_SOURCE_PLATFORM, start_date, end_date))
        .group_by(AIAPIUsage.user_id)
        .subquery("template_usage")
    )

    proxy_calls = func.coalesce(proxy_sub.c.proxy_total_calls, 0)
    proxy_input = func.coalesce(proxy_sub.c.proxy_input_tokens, 0)
    proxy_output = func.coalesce(proxy_sub.c.proxy_output_tokens, 0)
    proxy_success = func.coalesce(proxy_sub.c.proxy_successful_calls, 0)
    proxy_duration_count = func.coalesce(proxy_sub.c.proxy_duration_count, 0)
    proxy_duration_sum = func.coalesce(proxy_sub.c.proxy_duration_sum, 0)
    template_calls = func.coalesce(tmpl_sub.c.tmpl_total_calls, 0)
    template_input = func.coalesce(tmpl_sub.c.tmpl_input_tokens, 0)
    template_output = func.coalesce(tmpl_sub.c.tmpl_output_tokens, 0)
    template_success = func.coalesce(tmpl_sub.c.tmpl_successful_calls, 0)
    template_duration_count = func.coalesce(tmpl_sub.c.tmpl_duration_count, 0)
    template_duration_sum = func.coalesce(tmpl_sub.c.tmpl_duration_sum, 0)
    total_tokens = proxy_input + proxy_output + template_input + template_output

    # UNION of both aggregates gives exactly one row per user with activity.
    active_users = (
        select(proxy_sub.c.user_id.label("user_id"))
        .union(select(tmpl_sub.c.user_id.label("user_id")))
        .subquery("active_ai_users")
    )
    usage_query = (
        select(
            User.id,
            User.email,
            User.full_name,
            proxy_calls.label("proxy_calls"),
            proxy_input.label("proxy_input"),
            proxy_output.label("proxy_output"),
            proxy_success.label("proxy_success"),
            proxy_duration_count.label("proxy_duration_count"),
            proxy_duration_sum.label("proxy_duration_sum"),
            template_calls.label("template_calls"),
            template_input.label("template_input"),
            template_output.label("template_output"),
            template_success.label("template_success"),
            template_duration_count.label("template_duration_count"),
            template_duration_sum.label("template_duration_sum"),
        )
        .select_from(User)
        .join(active_users, active_users.c.user_id == User.id)
        .outerjoin(proxy_sub, proxy_sub.c.user_id == User.id)
        .outerjoin(tmpl_sub, tmpl_sub.c.user_id == User.id)
    )

    total_count = int(
        session.exec(select(func.count()).select_from(usage_query.subquery())).one()
        or 0
    )
    rows = session.exec(
        usage_query.order_by(total_tokens.desc(), User.id).offset(skip).limit(limit)
    ).all()

    results = []
    for row in rows:
        proxy_calls_value = int(row.proxy_calls or 0)
        template_calls_value = int(row.template_calls or 0)
        successful_calls = int(row.proxy_success or 0) + int(row.template_success or 0)
        total_calls = proxy_calls_value + template_calls_value
        duration_count = int(row.proxy_duration_count or 0) + int(
            row.template_duration_count or 0
        )
        duration_sum = int(row.proxy_duration_sum or 0) + int(
            row.template_duration_sum or 0
        )
        failed_calls = max(0, total_calls - successful_calls)
        results.append(
            {
                "user_id": row.id,
                "user_email": row.email,
                "user_full_name": row.full_name,
                "proxy_calls": proxy_calls_value,
                "proxy_input_tokens": int(row.proxy_input or 0),
                "proxy_output_tokens": int(row.proxy_output or 0),
                "template_calls": template_calls_value,
                "template_input_tokens": int(row.template_input or 0),
                "template_output_tokens": int(row.template_output or 0),
                "failed_calls": failed_calls,
                "error_rate": (
                    None
                    if total_calls == 0
                    else round(failed_calls / total_calls * 100, 2)
                ),
                "avg_latency_ms": (
                    round(duration_sum / duration_count) if duration_count else None
                ),
            }
        )

    return {"data": results, "count": total_count}
