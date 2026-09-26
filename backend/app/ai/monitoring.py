"""Shared helpers for recording built-in AI feature usage."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session

from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

CALL_AI_NAVIGATION = "ai_nav"
CALL_AI_CONTEXTUAL_HELP = "ai_help"


def new_ai_request_id() -> str:
    return str(uuid.uuid4())


def usage_metrics(
    response_data: dict[str, Any],
    elapsed: float,
    *,
    request_id: str | None = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """從 OpenAI 相容回應的 ``usage`` 區塊整理 token 數與耗時（缺值一律視為 0）。"""
    raw_usage = response_data.get("usage")
    usage_reported = isinstance(raw_usage, dict)
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    completed_at = datetime.now(timezone.utc)
    effective_elapsed = max(elapsed, 0.0)
    return {
        "request_id": request_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        ),
        "elapsed_seconds": round(effective_elapsed, 3),
        "usage_reported": usage_reported,
        "response_model": str(response_data.get("model") or "")[:255] or None,
        "started_at": started_at or completed_at - timedelta(seconds=effective_elapsed),
        "completed_at": completed_at,
    }


def _token_count(metrics: Mapping[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(metrics: Mapping[str, Any] | None) -> int | None:
    if not metrics:
        return None
    elapsed = metrics.get("elapsed_seconds")
    if elapsed is None:
        return None
    try:
        return int(round(float(elapsed) * 1000))
    except (TypeError, ValueError):
        return None


def _truncate_error(error_message: str | None) -> str | None:
    if not error_message:
        return None
    return error_message[:1000]


def record_ai_template_call(
    *,
    session: Session | None,
    user_id: uuid.UUID | None,
    call_type: str,
    model_name: str | None,
    preset: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Best-effort usage logging for platform-owned LLM calls."""
    if session is None or user_id is None:
        return
    try:
        ai_gateway_service.record_template_call(
            session=session,
            user_id=user_id,
            call_type=call_type,
            model_name=(model_name or "unknown")[:255],
            preset=preset,
            request_id=str((metrics or {}).get("request_id") or new_ai_request_id())[
                :255
            ],
            input_tokens=_token_count(metrics or {}, "prompt_tokens"),
            output_tokens=_token_count(metrics or {}, "completion_tokens"),
            request_duration_ms=_duration_ms(metrics),
            stream=bool((metrics or {}).get("stream", False)),
            usage_reported=bool((metrics or {}).get("usage_reported", False)),
            response_model=str((metrics or {}).get("response_model") or "")[:255]
            or None,
            status=status,
            error_message=_truncate_error(error_message),
            started_at=(metrics or {}).get("started_at"),
            completed_at=(metrics or {}).get("completed_at"),
        )
    except Exception:
        logger.warning(
            "Failed to record AI template call usage: call_type=%s user_id=%s",
            call_type,
            user_id,
            exc_info=True,
        )
