import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta

import httpx
from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.core.authorizers import require_ai_api_access
from app.core.security import decrypt_value, encrypt_value
from app.exceptions import BadRequestError, NotFoundError
from app.features.ai.config import settings as ai_api_settings
from app.models import (
    AIAPICredential,
    AIAPIRequest,
    AIAPIRequestStatus,
    AIAPIUsage,
    AITemplateCallLog,
    User,
    get_datetime_utc,
)
from app.schemas import (
    AIAPICredentialAdminPublic,
    AIAPICredentialPublic,
    AIAPICredentialsAdminPublic,
    AIAPICredentialsPublic,
    AIAPIRequestCreate,
    AIAPIRequestPublic,
    AIAPIRequestReview,
    AIAPIRequestsPublic,
    Message,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_RATE_LIMIT = 20


def _generate_user_api_key() -> str:
    return f"ccai_{secrets.token_urlsafe(24)}"


def _credential_prefix(api_key: str) -> str:
    api_key = api_key.strip()
    return api_key[: min(8, len(api_key))]


def _get_owned_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> AIAPICredential:
    credential = session.get(AIAPICredential, credential_id)
    if not credential:
        raise NotFoundError("AI API credential not found")
    require_ai_api_access(current_user, credential.user_id)
    return credential


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


def _to_credential_public(credential: AIAPICredential) -> AIAPICredentialPublic:
    try:
        api_key = decrypt_value(credential.api_key_encrypted)
    except Exception:
        api_key = "<無效的或已損壞的金鑰>"

    return AIAPICredentialPublic(
        id=credential.id,
        request_id=credential.request_id,
        base_url=credential.base_url,
        api_key=api_key,
        api_key_prefix=credential.api_key_prefix,
        api_key_name=credential.api_key_name,
        rate_limit=credential.rate_limit,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
        created_at=credential.created_at,
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
    *, credential: AIAPICredential, user: User, now: datetime
) -> AIAPICredentialAdminPublic:
    status, inactive_reason = _resolve_credential_status(credential=credential, now=now)
    return AIAPICredentialAdminPublic(
        id=credential.id,
        user_id=credential.user_id,
        user_email=user.email,
        user_full_name=user.full_name,
        request_id=credential.request_id,
        base_url=credential.base_url,
        api_key_prefix=credential.api_key_prefix,
        api_key_name=credential.api_key_name,
        rate_limit=credential.rate_limit,
        status=status,
        inactive_reason=inactive_reason,
        expires_at=credential.expires_at,
        revoked_at=credential.revoked_at,
        created_at=credential.created_at,
    )


def create_request(
    *, session: Session, request_in: AIAPIRequestCreate, user
) -> AIAPIRequestPublic:
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
    count_query = select(AIAPIRequest.id).where(AIAPIRequest.user_id == user_id)
    data_query = (
        select(AIAPIRequest)
        .where(AIAPIRequest.user_id == user_id)
        .order_by(AIAPIRequest.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return AIAPIRequestsPublic(
        data=[_to_request_public(item) for item in session.exec(data_query).all()],
        count=len(session.exec(count_query).all()),
    )


def list_all_requests(
    *,
    session: Session,
    status: AIAPIRequestStatus | None = None,
    skip: int = 0,
    limit: int = 100,
) -> AIAPIRequestsPublic:
    count_query = select(AIAPIRequest.id)
    data_query = select(AIAPIRequest)
    if status is not None:
        count_query = count_query.where(AIAPIRequest.status == status)
        data_query = data_query.where(AIAPIRequest.status == status)
    data_query = (
        data_query.order_by(AIAPIRequest.created_at.desc()).offset(skip).limit(limit)
    )
    return AIAPIRequestsPublic(
        data=[_to_request_public(item) for item in session.exec(data_query).all()],
        count=len(session.exec(count_query).all()),
    )


def get_request(
    *, session: Session, request_id: uuid.UUID, current_user
) -> AIAPIRequestPublic:
    db_request = session.get(AIAPIRequest, request_id)
    if not db_request:
        raise NotFoundError("AI API request not found")
    require_ai_api_access(current_user, db_request.user_id)
    return _to_request_public(db_request)


def review_request(
    *,
    session: Session,
    request_id: uuid.UUID,
    review_data: AIAPIRequestReview,
    reviewer,
) -> AIAPIRequestPublic:
    db_request = session.get(AIAPIRequest, request_id)
    if not db_request:
        raise NotFoundError("AI API request not found")
    if db_request.status != AIAPIRequestStatus.pending:
        raise BadRequestError("This AI API request has already been reviewed")

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
            raise BadRequestError("AI API connection settings are incomplete")

        expires_at = None
        now = get_datetime_utc()
        duration_str = db_request.duration
        if duration_str == "1h":
            expires_at = now + timedelta(hours=1)
        elif duration_str == "1d":
            expires_at = now + timedelta(days=1)
        elif duration_str == "7d":
            expires_at = now + timedelta(days=7)
        elif duration_str == "30d":
            expires_at = now + timedelta(days=30)

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
    count_query = select(AIAPICredential.id).where(AIAPICredential.user_id == user_id)
    data_query = (
        select(AIAPICredential)
        .where(AIAPICredential.user_id == user_id)
        .order_by(AIAPICredential.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return AIAPICredentialsPublic(
        data=[_to_credential_public(item) for item in session.exec(data_query).all()],
        count=len(session.exec(count_query).all()),
    )


def list_all_credentials(
    *,
    session: Session,
    status: str | None = None,
    user_email: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> AIAPICredentialsAdminPublic:
    now = get_datetime_utc()

    count_query = select(AIAPICredential.id).join(
        User, User.id == AIAPICredential.user_id
    )
    data_query = select(AIAPICredential, User).join(
        User, User.id == AIAPICredential.user_id
    )

    keyword = (user_email or "").strip()
    if keyword:
        like_pattern = f"%{keyword}%"
        count_query = count_query.where(User.email.ilike(like_pattern))
        data_query = data_query.where(User.email.ilike(like_pattern))

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

    if status == "active":
        count_query = count_query.where(active_clause)
        data_query = data_query.where(active_clause)
    elif status == "inactive":
        count_query = count_query.where(inactive_clause)
        data_query = data_query.where(inactive_clause)

    data_query = (
        data_query.order_by(AIAPICredential.created_at.desc()).offset(skip).limit(limit)
    )
    rows = session.exec(data_query).all()

    return AIAPICredentialsAdminPublic(
        data=[
            _to_credential_admin_public(credential=credential, user=user, now=now)
            for credential, user in rows
        ],
        count=len(session.exec(count_query).all()),
    )


def rotate_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> AIAPICredentialPublic:
    credential = _get_owned_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    if credential.revoked_at is not None:
        raise BadRequestError("This AI API credential has already been revoked")

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

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_rotate",
        details=f"Rotated AI API credential {credential_id}",
        commit=False,
    )

    session.commit()
    session.refresh(new_credential)
    return _to_credential_public(new_credential)


def delete_credential(
    *, session: Session, credential_id: uuid.UUID, current_user
) -> Message:
    credential = _get_owned_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    session.delete(credential)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_delete",
        details=f"Deleted AI API credential {credential_id}",
        commit=False,
    )
    session.commit()
    return Message(message="AI API credential deleted successfully")


def update_credential_name(
    *, session: Session, credential_id: uuid.UUID, name: str, current_user
) -> AIAPICredentialPublic:
    credential = _get_owned_credential(
        session=session, credential_id=credential_id, current_user=current_user
    )

    credential.api_key_name = name
    session.add(credential)

    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action="ai_api_credential_update",
        details=f"Renamed AI API credential {credential_id} to '{name}'",
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
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_duration_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
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
        credential_id=credential_id,
        model_name=model_name,
        request_type=request_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_duration_ms=request_duration_ms,
        status=status,
        error_message=error_message,
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
    input_tokens: int = 0,
    output_tokens: int = 0,
    request_duration_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
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
    log = AITemplateCallLog(
        user_id=user_id,
        call_type=call_type,
        model_name=model_name,
        preset=preset,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        request_duration_ms=request_duration_ms,
        status=status,
        error_message=error_message,
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


# ===== 新增：代理到 VLLM 功能 =====


async def proxy_to_vllm_chat_completion(
    *,
    user: User,
    request_data: dict,
) -> dict:
    """
    代理聊天补全請求到 VLLM Gateway（非流式）

    Returns:
        dict: VLLM 回應（附加 duration_ms 耗時資訊）
    """
    url = f"{ai_api_settings.resolved_vllm_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {ai_api_settings.ai_api_api_key}",
        "Content-Type": "application/json",
    }

    start_time = time.time()
    model_name = request_data.get("model", "unknown")

    try:
        async with httpx.AsyncClient(timeout=ai_api_settings.ai_api_timeout) as client:
            response = await client.post(url, json=request_data, headers=headers)
            response.raise_for_status()
            result = response.json()

        duration_ms = int((time.time() - start_time) * 1000)
        result["duration_ms"] = duration_ms  # 附加耗時到回應

        usage = result.get("usage", {})
        logger.info(
            "User %s completed chat request: model=%s, tokens=%d, duration=%dms",
            user.email,
            model_name,
            usage.get("total_tokens", 0),
            duration_ms,
        )

        return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "VLLM request failed for user %s: %s",
            user.email,
            f"VLLM returned {e.response.status_code}: {e.response.text}",
        )
        raise

    except httpx.RequestError as e:
        logger.error("VLLM connection failed for user %s: %s", user.email, str(e))
        raise

    except Exception as e:
        logger.error("Unexpected error for user %s: %s", user.email, str(e))
        raise


async def proxy_to_vllm_chat_completion_stream(
    *,
    user: User,
    request_data: dict,
) -> AsyncGenerator[str, None]:
    """
    代理聊天补全請求到 VLLM Gateway（流式）

    流式回應的 usage 資訊已包含在 vLLM 传輸的各個 chunk 中（若模型支援）。
    """
    request_data["stream"] = True
    request_data.setdefault("stream_options", {})["include_usage"] = True

    url = f"{ai_api_settings.resolved_vllm_base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {ai_api_settings.ai_api_api_key}",
        "Content-Type": "application/json",
    }

    start_time = time.time()
    model_name = request_data.get("model", "unknown")

    try:
        async with httpx.AsyncClient(timeout=ai_api_settings.ai_api_timeout) as client:
            async with client.stream(
                "POST", url, json=request_data, headers=headers
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue

                    if line.startswith("data: "):
                        data_str = line[6:]

                        if data_str == "[DONE]":
                            duration_ms = int((time.time() - start_time) * 1000)
                            logger.info(
                                "User %s completed stream: model=%s, duration=%dms",
                                user.email,
                                model_name,
                                duration_ms,
                            )
                            yield "data: [DONE]\n\n"
                            break

                        try:
                            chunk = json.loads(data_str)
                            yield f"data: {json.dumps(chunk)}\n\n"
                        except json.JSONDecodeError:
                            yield f"data: {data_str}\n\n"

    except Exception as e:
        logger.error("Stream error for user %s: %s", user.email, str(e))
        raise


# ===== 新增：查询使用统计 =====


def get_user_usage_stats(
    *,
    session: Session,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """
    查詢使用者的 Proxy 使用統計
    """
    records = session.exec(
        select(AIAPIUsage)
        .where(AIAPIUsage.user_id == user_id)
        .where(AIAPIUsage.created_at >= start_date)
        .where(AIAPIUsage.created_at <= end_date)
    ).all()

    total_requests = len(records)
    total_input_tokens = sum(r.input_tokens for r in records)
    total_output_tokens = sum(r.output_tokens for r in records)

    by_model: dict[str, dict] = {}
    for record in records:
        model = record.model_name
        if model not in by_model:
            by_model[model] = {
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }

        by_model[model]["requests"] += 1
        by_model[model]["input_tokens"] += record.input_tokens
        by_model[model]["output_tokens"] += record.output_tokens

    return {
        "total_requests": total_requests,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "by_model": by_model,
        "start_date": start_date,
        "end_date": end_date,
    }


def get_user_template_usage_stats(
    *,
    session: Session,
    user_id: uuid.UUID,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """
    查詢使用者的 Template 呼叫統計
    """
    records = session.exec(
        select(AITemplateCallLog)
        .where(AITemplateCallLog.user_id == user_id)
        .where(AITemplateCallLog.created_at >= start_date)
        .where(AITemplateCallLog.created_at <= end_date)
    ).all()

    total_calls = len(records)
    total_input_tokens = sum(r.input_tokens for r in records)
    total_output_tokens = sum(r.output_tokens for r in records)

    by_call_type: dict[str, dict] = {}
    for record in records:
        ct = record.call_type
        if ct not in by_call_type:
            by_call_type[ct] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        by_call_type[ct]["calls"] += 1
        by_call_type[ct]["input_tokens"] += record.input_tokens
        by_call_type[ct]["output_tokens"] += record.output_tokens

    return {
        "total_calls": total_calls,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "by_call_type": by_call_type,
        "start_date": start_date,
        "end_date": end_date,
    }


# ===== Admin 監控功能 =====


def get_monitoring_stats(
    *,
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """全局 AI 監控統計卡片"""
    from sqlalchemy import case, distinct, func

    success_statuses = ("success", "ok", "200")

    proxy_query = select(
        func.count(AIAPIUsage.id),
        func.coalesce(func.sum(AIAPIUsage.input_tokens), 0),
        func.coalesce(func.sum(AIAPIUsage.output_tokens), 0),
        func.coalesce(
            func.sum(case((AIAPIUsage.status.in_(success_statuses), 1), else_=0)),
            0,
        ),
        func.count(AIAPIUsage.request_duration_ms),
        func.coalesce(func.sum(AIAPIUsage.request_duration_ms), 0),
    )
    template_query = select(
        func.count(AITemplateCallLog.id),
        func.coalesce(func.sum(AITemplateCallLog.input_tokens), 0),
        func.coalesce(func.sum(AITemplateCallLog.output_tokens), 0),
        func.coalesce(
            func.sum(
                case((AITemplateCallLog.status.in_(success_statuses), 1), else_=0)
            ),
            0,
        ),
        func.count(AITemplateCallLog.request_duration_ms),
        func.coalesce(func.sum(AITemplateCallLog.request_duration_ms), 0),
    )

    if start_date:
        proxy_query = proxy_query.where(AIAPIUsage.created_at >= start_date)
        template_query = template_query.where(
            AITemplateCallLog.created_at >= start_date
        )
    if end_date:
        proxy_query = proxy_query.where(AIAPIUsage.created_at <= end_date)
        template_query = template_query.where(AITemplateCallLog.created_at <= end_date)

    proxy_row = session.exec(proxy_query).one()
    template_row = session.exec(template_query).one()
    proxy_total_calls = int(proxy_row[0] or 0)
    template_total_calls = int(template_row[0] or 0)
    total_calls = proxy_total_calls + template_total_calls
    successful_calls = int(proxy_row[3] or 0) + int(template_row[3] or 0)
    failed_calls = max(0, total_calls - successful_calls)
    duration_count = int(proxy_row[4] or 0) + int(template_row[4] or 0)
    duration_sum = int(proxy_row[5] or 0) + int(template_row[5] or 0)

    # 活躍使用者（proxy + template 的 distinct user_id 合集）
    proxy_users_q = select(distinct(AIAPIUsage.user_id))
    template_users_q = select(distinct(AITemplateCallLog.user_id))
    if start_date:
        proxy_users_q = proxy_users_q.where(AIAPIUsage.created_at >= start_date)
        template_users_q = template_users_q.where(
            AITemplateCallLog.created_at >= start_date
        )
    if end_date:
        proxy_users_q = proxy_users_q.where(AIAPIUsage.created_at <= end_date)
        template_users_q = template_users_q.where(
            AITemplateCallLog.created_at <= end_date
        )

    proxy_user_ids = set(session.exec(proxy_users_q).all())
    template_user_ids = set(session.exec(template_users_q).all())
    active_users = len(proxy_user_ids | template_user_ids)

    # 使用的模型列表
    proxy_models_q = select(distinct(AIAPIUsage.model_name))
    template_models_q = select(distinct(AITemplateCallLog.model_name))
    if start_date:
        proxy_models_q = proxy_models_q.where(AIAPIUsage.created_at >= start_date)
        template_models_q = template_models_q.where(
            AITemplateCallLog.created_at >= start_date
        )
    if end_date:
        proxy_models_q = proxy_models_q.where(AIAPIUsage.created_at <= end_date)
        template_models_q = template_models_q.where(
            AITemplateCallLog.created_at <= end_date
        )

    models = sorted(
        set(session.exec(proxy_models_q).all())
        | set(session.exec(template_models_q).all())
    )

    return {
        "proxy_total_calls": proxy_total_calls,
        "proxy_total_input_tokens": proxy_row[1],
        "proxy_total_output_tokens": proxy_row[2],
        "template_total_calls": template_total_calls,
        "template_total_input_tokens": template_row[1],
        "template_total_output_tokens": template_row[2],
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
    count_query = select(AIAPIUsage.id)
    data_query = select(AIAPIUsage, User).join(User, User.id == AIAPIUsage.user_id)

    filters = []
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

    total = len(session.exec(count_query).all())
    rows = session.exec(data_query).all()

    records = []
    for usage, user in rows:
        records.append(
            {
                "id": usage.id,
                "user_id": usage.user_id,
                "user_email": user.email,
                "user_full_name": user.full_name,
                "credential_id": usage.credential_id,
                "model_name": usage.model_name,
                "request_type": usage.request_type,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "request_duration_ms": usage.request_duration_ms,
                "status": usage.status,
                "error_message": usage.error_message,
                "created_at": usage.created_at,
            }
        )

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
    count_query = select(AITemplateCallLog.id)
    data_query = select(AITemplateCallLog, User).join(
        User, User.id == AITemplateCallLog.user_id
    )

    filters = []
    if user_id:
        filters.append(AITemplateCallLog.user_id == user_id)
    if call_type:
        filters.append(AITemplateCallLog.call_type == call_type)
    if preset:
        filters.append(AITemplateCallLog.preset == preset)
    if call_status:
        filters.append(AITemplateCallLog.status == call_status)
    if start_date:
        filters.append(AITemplateCallLog.created_at >= start_date)
    if end_date:
        filters.append(AITemplateCallLog.created_at <= end_date)

    for f in filters:
        count_query = count_query.where(f)
        data_query = data_query.where(f)

    data_query = (
        data_query.order_by(AITemplateCallLog.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    total = len(session.exec(count_query).all())
    rows = session.exec(data_query).all()

    records = []
    for log, user in rows:
        records.append(
            {
                "id": log.id,
                "user_id": log.user_id,
                "user_email": user.email,
                "user_full_name": user.full_name,
                "call_type": log.call_type,
                "model_name": log.model_name,
                "preset": log.preset,
                "input_tokens": log.input_tokens,
                "output_tokens": log.output_tokens,
                "request_duration_ms": log.request_duration_ms,
                "status": log.status,
                "error_message": log.error_message,
                "created_at": log.created_at,
            }
        )

    return {"data": records, "count": total}


def list_users_usage(
    *,
    session: Session,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    skip: int = 0,
    limit: int = 50,
) -> dict:
    """Admin: 每個使用者的 AI 用量彙總"""
    from sqlalchemy import func

    # Proxy 用量 per user
    proxy_sub = select(
        AIAPIUsage.user_id,
        func.count(AIAPIUsage.id).label("proxy_calls"),
        func.coalesce(func.sum(AIAPIUsage.input_tokens), 0).label("proxy_input"),
        func.coalesce(func.sum(AIAPIUsage.output_tokens), 0).label("proxy_output"),
    )
    if start_date:
        proxy_sub = proxy_sub.where(AIAPIUsage.created_at >= start_date)
    if end_date:
        proxy_sub = proxy_sub.where(AIAPIUsage.created_at <= end_date)
    proxy_sub = proxy_sub.group_by(AIAPIUsage.user_id).subquery()

    # Template 用量 per user
    tmpl_sub = select(
        AITemplateCallLog.user_id,
        func.count(AITemplateCallLog.id).label("tmpl_calls"),
        func.coalesce(func.sum(AITemplateCallLog.input_tokens), 0).label("tmpl_input"),
        func.coalesce(func.sum(AITemplateCallLog.output_tokens), 0).label(
            "tmpl_output"
        ),
    )
    if start_date:
        tmpl_sub = tmpl_sub.where(AITemplateCallLog.created_at >= start_date)
    if end_date:
        tmpl_sub = tmpl_sub.where(AITemplateCallLog.created_at <= end_date)
    tmpl_sub = tmpl_sub.group_by(AITemplateCallLog.user_id).subquery()

    # 合併：所有有 proxy 或 template 呼叫的使用者
    # 先取得所有相關 user_id
    all_user_ids_q = select(proxy_sub.c.user_id).union(select(tmpl_sub.c.user_id))
    raw_rows = session.exec(all_user_ids_q).all()

    # UNION of single-column selects returns Row objects (Sequence-like); unwrap
    # to scalar UUIDs so downstream `WHERE user_id == uid` binds a UUID, not a Row.
    def _scalar(r):
        if isinstance(r, uuid.UUID):
            return r
        try:
            return r[0]
        except (TypeError, IndexError):
            return r

    all_user_ids = [_scalar(row) for row in raw_rows]
    total_count = len(all_user_ids)

    # 分頁取使用者明細
    paginated_ids = all_user_ids[skip : skip + limit]
    if not paginated_ids:
        return {"data": [], "count": total_count}

    results = []
    for uid in paginated_ids:
        user = session.get(User, uid)
        if not user:
            continue

        # proxy stats
        proxy_row = session.exec(
            select(
                proxy_sub.c.proxy_calls,
                proxy_sub.c.proxy_input,
                proxy_sub.c.proxy_output,
            ).where(proxy_sub.c.user_id == uid)
        ).first()

        # template stats
        tmpl_row = session.exec(
            select(
                tmpl_sub.c.tmpl_calls, tmpl_sub.c.tmpl_input, tmpl_sub.c.tmpl_output
            ).where(tmpl_sub.c.user_id == uid)
        ).first()

        results.append(
            {
                "user_id": uid,
                "user_email": user.email,
                "user_full_name": user.full_name,
                "proxy_calls": proxy_row[0] if proxy_row else 0,
                "proxy_input_tokens": proxy_row[1] if proxy_row else 0,
                "proxy_output_tokens": proxy_row[2] if proxy_row else 0,
                "template_calls": tmpl_row[0] if tmpl_row else 0,
                "template_input_tokens": tmpl_row[1] if tmpl_row else 0,
                "template_output_tokens": tmpl_row[2] if tmpl_row else 0,
            }
        )

    # 按總 token 降序排
    results.sort(
        key=lambda r: (
            r["proxy_input_tokens"]
            + r["proxy_output_tokens"]
            + r["template_input_tokens"]
            + r["template_output_tokens"]
        ),
        reverse=True,
    )

    return {"data": results, "count": total_count}
