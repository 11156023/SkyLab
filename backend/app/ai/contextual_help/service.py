"""說明助手的主流程：分類 → 取情境 → 能直接答就直接答 → 否則問模型。

有一條刻意的捷徑：情境已經足以拼出正確答案時（例如只有一個欄位有驗證錯誤），
就直接組出那句話，不呼叫模型。這種答案更快、更便宜，而且不可能講錯。模型是
用來把複雜情況講得順，不是用來複誦一行錯誤訊息。

同一組確定性答案也是模型不可用時的後備，所以助手在模型離線時仍然可用——只是
講得比較硬。
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from sqlmodel import Session

from app.ai.contextual_help.intent import classify
from app.ai.contextual_help.prompt import build_messages
from app.ai.contextual_help.resolver import (
    blocked_elements,
    resolve_context,
    sanitize_state,
)
from app.ai.contextual_help.schemas import (
    ExplainRequest,
    ExplainResponse,
    HelpIntent,
    SurfaceSpec,
)
from app.ai.contextual_help.surfaces import (
    find_element,
    find_surface,
    get_surfaces_for_user,
    match_element_by_label,
)
from app.ai.monitoring import CALL_AI_CONTEXTUAL_HELP, record_ai_template_call
from app.ai.system_config import system_ai_env
from app.ai.utils import strip_think_tags
from app.infrastructure.ai.contextual_help import client as help_client
from app.models import User

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 220
_TEMPERATURE = 0.2
# 說明就是說明，長了沒人看。超過就截斷，不讓模型把整頁教學倒出來。
_MAX_ANSWER_CHARS = 400


def _usage_metrics(response_data: dict[str, Any], elapsed: float) -> dict[str, Any]:
    usage = response_data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int(
            usage.get("total_tokens") or prompt_tokens + completion_tokens
        ),
        "elapsed_seconds": round(max(elapsed, 0.0), 3),
    }


# ------------------------------------------------------------ 確定性答案


def _deterministic_answer(
    surface: SurfaceSpec,
    intent: HelpIntent,
    context: dict[str, Any],
    *,
    active_target: str | None,
    blocked: list[str],
) -> str | None:
    """情境足以直接拼出正確答案時回傳那句話，否則回 None 交給模型。"""
    if intent == "validation_help":
        if not blocked:
            return "目前這個畫面沒有任何驗證錯誤或被停用的操作。"
        if len(blocked) == 1:
            element = find_element(surface, blocked[0])
            state = context.get("blocked", [{}])[0]
            reason = state.get("error") or state.get("disabled_reason")
            if element and reason:
                return f"「{element.label}」目前沒有通過驗證：{reason}"
        return None

    if intent == "field_help":
        target = context.get("target") or {}
        # 有 help 就交給模型講得順一點；只有 label 和 constraints 時直接列出來
        # 反而比讓模型改寫更準。
        if active_target and not target.get("help") and target.get("constraints"):
            element = find_element(surface, active_target)
            if element:
                rules = "、".join(element.constraints)
                return f"「{element.label}」的填寫限制：{rules}。"
        return None

    return None


def _fallback_answer(
    surface: SurfaceSpec,
    intent: HelpIntent,
    context: dict[str, Any],
    *,
    active_target: str | None,
    blocked: list[str],
) -> str:
    """模型不可用時的答案。講得硬，但不會錯。"""
    direct = _deterministic_answer(
        surface, intent, context, active_target=active_target, blocked=blocked
    )
    if direct:
        return direct

    if intent == "field_help" and active_target:
        element = find_element(surface, active_target)
        if element:
            parts = [f"「{element.label}」"]
            if element.help:
                parts.append(element.help)
            if element.constraints:
                parts.append("限制：" + "、".join(element.constraints) + "。")
            return " ".join(parts)

    if intent == "validation_help" and blocked:
        labels = []
        for element_id in blocked:
            element = find_element(surface, element_id)
            if element:
                labels.append(element.label)
        if labels:
            return "以下欄位還沒有通過驗證：" + "、".join(labels) + "。"

    summary = f"這是「{surface.title}」。{surface.purpose}"
    if surface.sections:
        summary += "主要分成：" + "、".join(surface.sections) + "。"
    return summary


# ------------------------------------------------------------ 主流程


async def explain(
    request: ExplainRequest,
    current_user: User,
    session: Session | None = None,
) -> ExplainResponse:
    allowed = get_surfaces_for_user(current_user)
    surface = find_surface(request.surface_id, allowed)
    if surface is None:
        # 也可能是使用者沒有這個畫面的權限；兩種情況都不該透露它存在。
        return ExplainResponse(
            intent="page_overview",
            answer="我沒有這個畫面的資料，沒辦法說明。",
            context_version=request.context_version,
        )

    state = sanitize_state(surface, request.state)
    blocked = blocked_elements(surface, state)
    active_target = request.active_target
    if active_target and find_element(surface, active_target) is None:
        active_target = None
    if active_target is None:
        # 前端還沒回報 focus，或使用者問的不是游標所在的欄位：
        # 問題裡指名了哪一個元素就用哪一個。
        named = match_element_by_label(surface, request.question)
        if named is not None:
            active_target = named.id

    intent = classify(
        request.question,
        has_active_target=bool(active_target),
        has_blocked=bool(blocked),
    )
    context, grounded, level = resolve_context(
        surface, intent, active_target=active_target, state=state
    )
    # resolve_context 在目標未知時會退回頁面概觀，這裡跟著回正。
    if intent == "field_help" and "target" not in context:
        intent = "page_overview"

    target = active_target or (blocked[0] if blocked else None)

    direct = _deterministic_answer(
        surface, intent, context, active_target=active_target, blocked=blocked
    )
    if direct is not None:
        return ExplainResponse(
            intent=intent,
            answer=direct,
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )

    model_name = system_ai_env.vllm_model_name.strip()
    if not model_name:
        return ExplainResponse(
            intent=intent,
            answer=_fallback_answer(
                surface, intent, context, active_target=active_target, blocked=blocked
            ),
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )

    def _log(
        metrics: dict[str, Any] | None = None,
        *,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        if session is None:
            return
        record_ai_template_call(
            session=session,
            user_id=current_user.id,
            call_type=CALL_AI_CONTEXTUAL_HELP,
            model_name=model_name,
            metrics=metrics,
            status=status,
            error_message=error_message,
        )

    payload = {
        "model": model_name,
        "messages": build_messages(intent, context, request.question.strip()),
        "max_tokens": _MAX_TOKENS,
        "temperature": _TEMPERATURE,
        "top_p": 0.9,
    }

    try:
        started = perf_counter()
        response_data = await help_client.create_chat_completion(
            payload, timeout=_TIMEOUT_SECONDS
        )
        metrics = _usage_metrics(response_data, perf_counter() - started)
        content = str(response_data["choices"][0]["message"]["content"] or "")
        answer = strip_think_tags(content).strip()
        if not answer:
            _log(metrics, status="error", error_message="Empty answer from model.")
            answer = _fallback_answer(
                surface, intent, context, active_target=active_target, blocked=blocked
            )
            used_model = False
        else:
            _log(metrics)
            used_model = True
        return ExplainResponse(
            intent=intent,
            answer=answer[:_MAX_ANSWER_CHARS],
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=used_model,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Contextual help failed, using deterministic answer: %s", exc)
        _log(status="error", error_message=str(exc))
        return ExplainResponse(
            intent=intent,
            answer=_fallback_answer(
                surface, intent, context, active_target=active_target, blocked=blocked
            ),
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )
