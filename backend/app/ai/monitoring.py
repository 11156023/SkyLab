"""Shared helpers for recording built-in AI feature usage."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from sqlmodel import Session

from app.services.llm_gateway import ai_gateway_service

logger = logging.getLogger(__name__)

CALL_AI_NAVIGATION = "ai_nav"
CALL_TJ_RUBRIC = "tj_rubric"
CALL_TJ_CHAT = "tj_chat"
CALL_TJ_SCRIPT_GENERATION = "tj_script_gen"
CALL_TJ_SCRIPT_REVIEW = "tj_script_review"
CALL_TJ_RESULT_ANALYSIS = "tj_result_ai"


def _token_count(metrics: Mapping[str, Any], key: str) -> int:
    try:
        return int(metrics.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(metrics: Mapping[str, Any] | None) -> int | None:
    if not metrics:
        return None
    elapsed = metrics.get("elapsed_seconds")
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
    session: Session,
    user_id: uuid.UUID | None,
    call_type: str,
    model_name: str | None,
    preset: str | None = None,
    metrics: Mapping[str, Any] | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Best-effort usage logging for platform-owned LLM calls."""
    if user_id is None:
        return
    try:
        ai_gateway_service.record_template_call(
            session=session,
            user_id=user_id,
            call_type=call_type,
            model_name=(model_name or "unknown")[:255],
            preset=preset,
            input_tokens=_token_count(metrics or {}, "prompt_tokens"),
            output_tokens=_token_count(metrics or {}, "completion_tokens"),
            request_duration_ms=_duration_ms(metrics),
            status=status,
            error_message=_truncate_error(error_message),
        )
    except Exception:
        logger.warning(
            "Failed to record AI template call usage: call_type=%s user_id=%s",
            call_type,
            user_id,
            exc_info=True,
        )
