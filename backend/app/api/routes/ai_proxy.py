"""
AI Proxy API Routes - 代理到 VLLM 的 API 端点
"""

import json
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import AIAPIUserDep, SessionDep
from app.features.ai.config import settings as ai_api_settings
from app.infrastructure.redis import check_rate_limit_sliding_window, get_redis
from app.schemas.ai_proxy import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelsResponse,
    RateLimitStatusResponse,
    UsageStatsResponse,
)
from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-proxy", tags=["ai_proxy"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="聊天補全",
    description="OpenAI 相容的聊天補全介面，支援串流和非串流回應",
)
async def chat_completions(
    request: ChatCompletionRequest,
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
):
    """
    聊天補全介面

    需要在 Authorization header 中提供 API Key:
    Authorization: Bearer ccai_xxx

    支援串流回應（stream=true）和非串流回應（stream=false）
    """
    user, credential = user_and_credential

    # === 檢查 Redis 速率限制 ===
    rate_limit = (
        credential.rate_limit
        if credential.rate_limit is not None
        else ai_api_settings.ai_api_rate_limit_per_minute
    )

    redis = await get_redis()
    allowed, rate_info = await check_rate_limit_sliding_window(
        redis=redis,
        user_id=str(user.id),
        limit=rate_limit,
        window_seconds=ai_api_settings.ai_api_rate_limit_window_seconds,
    )

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "message": f"Rate limit exceeded. Limit: {rate_info['limit']} requests per {rate_info['window_seconds']} seconds.",
                "limit": rate_info["limit"],
                "current": rate_info["current"],
                "reset_at": rate_info["reset_at"].isoformat(),
            },
        )

    request_data = request.model_dump(exclude_none=True)
    model_name = request_data.get("model", "unknown")

    try:
        if request.stream:
            # 串流模式：包裝 generator 以擷取最後 chunk 的 usage 並記錄
            async def _stream_with_logging():
                input_tokens = 0
                output_tokens = 0
                start_time = time.time()
                _status = "success"
                _error_message = None

                try:
                    async for (
                        chunk_str
                    ) in ai_gateway_service.proxy_to_vllm_chat_completion_stream(
                        user=user,
                        request_data=request_data,
                    ):
                        # 嘗試從 chunk 中擷取 usage（vLLM 在最後一個 data chunk 含 usage）
                        if (
                            chunk_str.startswith("data: ")
                            and chunk_str.strip() != "data: [DONE]"
                        ):
                            try:
                                chunk_data = json.loads(chunk_str[6:])
                                usage = chunk_data.get("usage")
                                if usage:
                                    input_tokens = int(usage.get("prompt_tokens") or 0)
                                    output_tokens = int(usage.get("completion_tokens") or 0)
                            except (json.JSONDecodeError, ValueError):
                                # 略過無法解析的 SSE chunk，不影響串流轉發
                                pass

                        yield chunk_str

                except Exception as stream_err:
                    # 捕捉串流中途發生的錯誤（vLLM 掛掉、連線中斷等）
                    # 注意：外層 try/except (HTTPStatusError/RequestError) 在串流模式下
                    # 無法攔截此處的例外，因為 generator body 由 Starlette 執行。
                    _status = "error"
                    _error_message = f"Stream error: {str(stream_err)[:500]}"
                    raise

                finally:
                    # 使用 finally 確保無論正常結束、例外或客戶端斷線
                    # (GeneratorExit 不被 except Exception 捕捉，但 finally 保證執行)
                    # 都能寫入使用紀錄。
                    #
                    # 關鍵：generator body 由 Starlette 在 response 送出「之後」執行，
                    # 此時 route 依賴注入的 `session` 已被 get_db 的 with 區塊關閉。
                    # 因此串流的 usage 記錄必須開一個獨立 session，不能沿用 `session`，
                    # 否則會落入 except 而漏記（計費統計缺串流呼叫）。
                    duration_ms = int((time.time() - start_time) * 1000)
                    try:
                        from sqlmodel import Session  # noqa: PLC0415

                        from app.core.db import engine  # noqa: PLC0415

                        with Session(engine) as rec_session:
                            ai_gateway_service.record_usage(
                                session=rec_session,
                                user_id=user.id,
                                credential_id=credential.id,
                                model_name=model_name,
                                request_type="chat_completion",
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                request_duration_ms=duration_ms,
                                status=_status,
                                error_message=_error_message,
                            )
                    except Exception as rec_err:
                        logger.error("Failed to record stream usage: %s", rec_err)

            return StreamingResponse(
                _stream_with_logging(),
                media_type="text/event-stream",
            )
        else:
            # 非串流模式
            result = await ai_gateway_service.proxy_to_vllm_chat_completion(
                user=user,
                request_data=request_data,
            )

            # 記錄 usage
            usage = result.get("usage", {})
            try:
                ai_gateway_service.record_usage(
                    session=session,
                    user_id=user.id,
                    credential_id=credential.id,
                    model_name=model_name,
                    request_type="chat_completion",
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    request_duration_ms=result.get("duration_ms"),
                    status="success",
                )
            except Exception as rec_err:
                logger.error("Failed to record usage: %s", rec_err)

            return result

    except httpx.HTTPStatusError as e:
        logger.error(
            "VLLM error for user %s: status=%d, body=%s",
            user.email,
            e.response.status_code,
            e.response.text,
        )
        # 記錄失敗
        try:
            ai_gateway_service.record_usage(
                session=session,
                user_id=user.id,
                credential_id=credential.id,
                model_name=model_name,
                request_type="chat_completion",
                status="error",
                error_message=f"HTTP {e.response.status_code}: {e.response.text[:500]}",
            )
        except Exception:
            # Ignore failures when recording usage to avoid masking the main error
            pass
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Upstream model service error: {e.response.text}",
        )

    except httpx.RequestError as e:
        logger.error("VLLM connection error for user %s: %s", user.email, str(e))
        try:
            ai_gateway_service.record_usage(
                session=session,
                user_id=user.id,
                credential_id=credential.id,
                model_name=model_name,
                request_type="chat_completion",
                status="error",
                error_message=f"Connection error: {str(e)[:500]}",
            )
        except Exception:
            # Ignore failures when recording usage to avoid masking the main error
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model service is temporarily unavailable. Please try again later.",
        )

    except Exception as e:
        logger.exception("Unexpected error for user %s: %s", user.email, str(e))
        try:
            ai_gateway_service.record_usage(
                session=session,
                user_id=user.id,
                credential_id=credential.id,
                model_name=model_name,
                request_type="chat_completion",
                status="error",
                error_message=f"Unexpected: {str(e)[:500]}",
            )
        except Exception:
            # Ignore failures when recording usage to avoid masking the main error
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please contact support.",
        )


@router.get(
    "/models",
    response_model=ModelsResponse,
    summary="列出可用模型",
    description="获取所有可用的 AI 模型列表",
)
async def list_models(
    user_and_credential: AIAPIUserDep,
):
    """
    列出可用模型

    从 VLLM Gateway 获取可用模型列表
    """
    user, credential = user_and_credential

    try:
        # 从 VLLM Gateway 获取模型列表
        url = f"{ai_api_settings.resolved_vllm_base_url}/v1/models"
        headers = {
            "Authorization": f"Bearer {ai_api_settings.ai_api_api_key}",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            result = response.json()

        # vLLM may not return `created` field; backfill with current timestamp
        now_ts = int(time.time())
        for model in result.get("data", []):
            if "created" not in model or model["created"] is None:
                model["created"] = now_ts

        logger.info("User %s queried model list", user.email)
        return result

    except httpx.RequestError as e:
        logger.error("Failed to fetch models: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model service is temporarily unavailable",
        )


@router.get(
    "/usage/my",
    response_model=UsageStatsResponse,
    summary="查看我的使用统计",
    description="查看当前用户的 AI API 使用统计",
)
async def get_my_usage_stats(
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    """
    查看我的使用统计

    Query 参数:
    - start_date: 开始日期（默认 30 天前）
    - end_date: 结束日期（默认今天）
    """
    user, credential = user_and_credential

    # 默认查询最近 30 天
    if not end_date:
        end_date = datetime.now(timezone.utc)
    if not start_date:
        start_date = end_date - timedelta(days=30)

    stats = ai_gateway_service.get_user_usage_stats(
        session=session, user_id=user.id, start_date=start_date, end_date=end_date
    )

    logger.info("User %s queried usage stats", user.email)
    return stats


@router.get(
    "/rate-limit/status",
    response_model=RateLimitStatusResponse,
    summary="查看速率限制状态",
    description="查看当前用户的速率限制状态",
)
async def get_rate_limit_status(
    user_and_credential: AIAPIUserDep,
    session: SessionDep,
):
    """
    查看速率限制状态

    返回当前时间窗口的请求配额使用情况
    """
    user, credential = user_and_credential

    # 使用 credential 的 rate_limit，如果為 None 則使用預設值
    limit = (
        credential.rate_limit
        if credential.rate_limit is not None
        else ai_api_settings.ai_api_rate_limit_per_minute
    )

    # 從 Redis 獲取當前速率限制狀態
    redis = await get_redis()

    # 如果 Redis 未啟用，返回禁用狀態
    if redis is None:
        return RateLimitStatusResponse(
            limit_per_minute=limit,
            current_usage=0,
            remaining=limit,
            reset_at=datetime.now(tz=timezone.utc),
            disabled=True,  # 標記速率限制已禁用
        )

    # 獲取當前窗口的請求數（不實際消耗配額）
    key = f"rate_limit:user:{user.id}"
    now_ms = int(time.time() * 1000)
    window_seconds = ai_api_settings.ai_api_rate_limit_window_seconds
    window_start_ms = now_ms - (window_seconds * 1000)

    try:
        # 移除過期請求並計數
        await redis.zremrangebyscore(key, "-inf", window_start_ms)
        current_usage = await redis.zcard(key)

        reset_at = datetime.fromtimestamp(
            (now_ms + window_seconds * 1000) / 1000, tz=timezone.utc
        )

        return RateLimitStatusResponse(
            limit_per_minute=limit,
            current_usage=current_usage,
            remaining=max(0, limit - current_usage),
            reset_at=reset_at,
        )

    except Exception as e:
        # Redis 錯誤時返回預設值
        logger.error("Failed to get rate limit status: %s", str(e))
        return RateLimitStatusResponse(
            limit_per_minute=limit,
            current_usage=0,
            remaining=limit,
            reset_at=datetime.now(tz=timezone.utc),
            error=str(e),
        )
