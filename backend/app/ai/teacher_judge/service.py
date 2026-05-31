"""AI analysis and chat service for Teacher Judge rubric workflows."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any, Literal, cast

import httpx
from fastapi import HTTPException

from app.ai.teacher_judge._types import VLLMMetrics
from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.prompt import (
    ANALYZE_SYSTEM_PROMPT,
    CHAT_SYSTEM_TEMPLATE,
    SITUATION_NORMAL,
    SITUATION_REFINE,
    TEMPLATE_COMMAND_CONTEXT_TEMPLATE,
)
from app.ai.teacher_judge.schemas import (
    ChatMessage,
    RubricAnalysis,
    RubricCheckStep,
    RubricItem,
)
from app.ai.teacher_judge.template_command_service import (
    format_template_commands_for_prompt,
    validate_check_steps,
)
from app.ai.utils import apply_thinking_control, strip_think_tags
from app.infrastructure.ai.teacher_judge import client as teacher_judge_client
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand

logger = logging.getLogger(__name__)


async def close_http_client() -> None:
    """Close Teacher Judge AI client; kept for older callers/tests."""
    await teacher_judge_client.aclose()


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "checked", "done"}:
            return True
        if text in {"false", "0", "no", "n", "off", "unchecked", "todo"}:
            return False
    return default


def _normalize_check_steps(
    raw_steps: Any,
    template_key: str | None = None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> list[RubricCheckStep]:
    if not isinstance(raw_steps, list):
        return []

    if template_commands is not None:
        validated_items = validate_check_steps(
            template_key or "",
            [{"check_steps": raw_steps}],
            template_commands,
        )
        return [
            RubricCheckStep(**step)
            for step in validated_items[0].get("check_steps", [])
        ]

    normalized: list[RubricCheckStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue

        command_key = str(raw_step.get("command_key") or "").strip()
        step_template_key = str(raw_step.get("template_key") or template_key or "").strip()
        if not command_key or not step_template_key:
            continue

        command_label = raw_step.get("command_label")

        normalized.append(
            RubricCheckStep(
                template_key=step_template_key,
                command_key=command_key,
                command_label=str(command_label) if command_label else None,
            )
        )

    return normalized


def _normalize_rubric_items(
    raw_items: Any,
    template_key: str | None = None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
    force_checked_false: bool = False,
) -> list[RubricItem]:
    """Best-effort normalization for AI-returned item payloads."""
    if not isinstance(raw_items, list):
        return []

    normalized: list[RubricItem] = []
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue

        item_id = str(raw.get("id") or f"item-{i + 1}")
        title = str(raw.get("title") or raw.get("name") or "").strip() or "未命名項目"
        description = str(raw.get("description") or raw.get("desc") or "")
        checked = (
            False
            if force_checked_false
            else _to_bool(raw.get("checked", raw.get("is_checked")), default=False)
        )

        detectable_raw = str(raw.get("detectable") or "manual").strip().lower()
        if detectable_raw not in {"auto", "partial", "manual"}:
            detectable_raw = "manual"
        detectable: Literal["auto", "partial", "manual"] = cast(
            "Literal['auto', 'partial', 'manual']", detectable_raw
        )

        detection_method = raw.get("detection_method") or raw.get("detection")
        fallback = raw.get("fallback") or raw.get("suggestion")
        check_steps = _normalize_check_steps(
            raw.get("check_steps"),
            template_key=template_key,
            template_commands=template_commands,
        )
        if template_commands is not None and detectable == "auto" and not check_steps:
            detectable = "partial"
            detection_method = (
                str(detection_method).strip()
                if detection_method is not None
                else "目前沒有可引用的有效 command_key，需人工或後續檢查輔助判斷"
            )

        normalized.append(
            RubricItem(
                id=item_id,
                title=title,
                description=description,
                checked=checked,
                detectable=detectable,
                detection_method=str(detection_method)
                if detection_method is not None
                else None,
                fallback=str(fallback) if fallback is not None else None,
                check_steps=check_steps,
            )
        )

    return normalized


def normalize_items_for_export(raw_items: Any) -> list[RubricItem]:
    """Public helper for robust export parsing."""
    return _normalize_rubric_items(raw_items)


def _extract_context_item_count(rubric_context: str) -> int:
    try:
        parsed = json.loads(rubric_context or "{}")
    except json.JSONDecodeError:
        return 0
    items = parsed.get("items")
    return len(items) if isinstance(items, list) else 0


async def _call_vllm(
    payload: dict[str, Any], timeout: float = 60.0
) -> tuple[str, VLLMMetrics]:
    """Call vLLM chat/completions and return (content, usage_metrics)."""
    url = f"{settings.VLLM_BASE_URL}/chat/completions"
    started = perf_counter()

    logger.debug(f"Calling vLLM API: {url}")

    try:
        data = await teacher_judge_client.create_chat_completion(
            payload,
            timeout=timeout,
        )

        elapsed = max(perf_counter() - started, 0.0)
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(
            usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        )
        tps = (completion_tokens / elapsed) if elapsed > 0 else 0.0

        logger.info(
            f"vLLM call successful: {total_tokens} tokens in {elapsed:.2f}s ({tps:.1f} t/s)"
        )

        content = data["choices"][0]["message"]["content"] or ""
        content = strip_think_tags(content)
        metrics = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(elapsed, 3),
            "tokens_per_second": round(tps, 2),
        }
        return content, cast("VLLMMetrics", metrics)
    except httpx.TimeoutException as exc:
        logger.error(f"vLLM API timeout after {timeout}s")
        raise HTTPException(
            status_code=504, detail="AI 服務回應超時，請稍後再試。"
        ) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.error(f"vLLM API returned status {status}")
        raise HTTPException(
            status_code=502, detail=f"AI 服務異常（狀態碼 {status}）"
        ) from exc
    except Exception as exc:
        logger.error(f"vLLM API call failed: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"AI 呼叫失敗：{exc}") from exc


async def analyze_rubric(
    raw_text: str,
    template_key: str = "linux",
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> tuple[RubricAnalysis, VLLMMetrics]:
    """Send raw document text to AI, return structured RubricAnalysis."""
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

    logger.info(f"Starting rubric analysis, text length: {len(raw_text)} characters")

    user_content = f"# 評分表原文\n\n{raw_text}"
    template_command_context = TEMPLATE_COMMAND_CONTEXT_TEMPLATE.format(
        template_key=template_key,
        template_commands=format_template_commands_for_prompt(template_commands or []),
    )
    analyze_system_prompt = ANALYZE_SYSTEM_PROMPT.replace(
        "{template_command_context}",
        template_command_context,
    )

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": [
                {"role": "system", "content": analyze_system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": settings.VLLM_MAX_TOKENS,
            "temperature": 0.2,
            "top_p": settings.VLLM_TOP_P,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )

    content, metrics = await _call_vllm(
        payload, timeout=float(settings.VLLM_TIMEOUT)
    )

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse AI response as JSON: {exc}")
        raise HTTPException(
            status_code=502, detail=f"AI 回傳 JSON 解析失敗：{exc}"
        ) from exc

    items_raw = data.get("items") or []
    items = _normalize_rubric_items(
        items_raw,
        template_key=template_key,
        template_commands=template_commands,
        force_checked_false=True,
    )

    total_items = len(items)
    checked_count = sum(1 for item in items if item.checked)
    auto_count = sum(1 for item in items if item.detectable == "auto")
    partial_count = sum(1 for item in items if item.detectable == "partial")
    manual_count = sum(1 for item in items if item.detectable == "manual")

    logger.info(
        f"Analysis complete: {total_items} items, {checked_count} checked (auto: {auto_count}, partial: {partial_count}, manual: {manual_count})"
    )

    analysis = RubricAnalysis(
        items=items,
        total_items=total_items,
        checked_count=checked_count,
        auto_count=auto_count,
        partial_count=partial_count,
        manual_count=manual_count,
        summary=str(data.get("summary") or ""),
        raw_text=raw_text,
    )
    return analysis, metrics


async def chat_with_rubric(
    messages: list[ChatMessage],
    rubric_context: str,
    is_refine: bool = False,
    template_key: str = "linux",
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> tuple[str, list[dict[str, Any]] | None, VLLMMetrics]:
    """
    Multi-turn chat with rubric context injected into system prompt.
    Returns (reply_text, updated_items_or_None, metrics).
    - is_refine: True 表示老師手動修改完表單後觸發的「全表潤飾」模式。
    - updated_items: complete list of RubricItem dicts when AI modified the rubric;
      None when AI only answered a question without changes.
    """
    if not settings.VLLM_MODEL_NAME:
        raise HTTPException(status_code=503, detail="VLLM_MODEL_NAME 未設定。")

    context_item_count = _extract_context_item_count(rubric_context)
    situation = SITUATION_REFINE if is_refine else SITUATION_NORMAL
    system_prompt = (
        CHAT_SYSTEM_TEMPLATE.replace(
            "{rubric_context}", rubric_context or "（尚未上傳評分表）"
        )
        .replace("{rubric_item_count}", str(context_item_count))
        .replace("{situation_instruction}", situation)
        .replace(
            "{template_command_context}",
            TEMPLATE_COMMAND_CONTEXT_TEMPLATE.format(
                template_key=template_key,
                template_commands=format_template_commands_for_prompt(
                    template_commands or []
                ),
            ),
        )
    )

    formatted = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        formatted.append({"role": msg.role, "content": msg.content})

    payload = apply_thinking_control(
        {
            "model": settings.VLLM_MODEL_NAME,
            "messages": formatted,
            "max_tokens": settings.VLLM_CHAT_MAX_TOKENS,
            "temperature": settings.VLLM_CHAT_TEMPERATURE,
            "top_p": settings.VLLM_TOP_P,
            "top_k": settings.VLLM_TOP_K,
            "repetition_penalty": settings.VLLM_REPETITION_PENALTY,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )

    content, metrics = await _call_vllm(
        payload, timeout=float(settings.VLLM_TIMEOUT)
    )

    reply_text = content
    updated_items: list[dict[str, Any]] | None = None
    try:
        parsed = json.loads(content)
        reply_text = str(parsed.get("reply") or content)
        raw_updated = parsed.get("updated_items")
        normalized_updated = _normalize_rubric_items(
            raw_updated,
            template_key=template_key,
            template_commands=template_commands,
        )
        if normalized_updated:
            if context_item_count > 0:
                updated_count = len(normalized_updated)
                if updated_count < context_item_count - 1:
                    logger.warning(
                        f"⚠️ AI 返回的項目數異常：期望至少 {context_item_count - 1} 個，"
                        f"實際返回 {updated_count} 個。可能導致資料遺失。"
                    )
                    reply_text = (
                        f"⚠️ 系統偵測到異常：我只返回了 {updated_count} 個項目，"
                        f"但原本有 {context_item_count} 個。這可能是我理解錯誤了。\n\n"
                        f"為了安全起見，請確認這是否是你想要的結果。如果不是，請重新說明你的需求。\n\n"
                        f"原始回覆：{reply_text}"
                    )
            updated_items = [item.model_dump() for item in normalized_updated]
    except (json.JSONDecodeError, TypeError):
        # Ignore malformed AI response for rubric updates
        pass

    return reply_text, updated_items, metrics
