"""說明助手的主流程：分類 → 取情境 → 能直接答就直接答 → 否則問模型。

有一條刻意的捷徑：情境已經足以拼出正確答案時（例如只有一個欄位有驗證錯誤），
就直接組出那句話，不呼叫模型。這種答案更快、更便宜，而且不可能講錯。模型是
用來把複雜情況講得順，不是用來複誦一行錯誤訊息。

同一組確定性答案也是模型不可用時的後備，所以助手在模型離線時仍然可用——只是
講得比較硬。
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlmodel import Session

from app.ai.contextual_help.intent import classify
from app.ai.contextual_help.prompt import build_candidate_messages
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
from app.ai.monitoring import (
    CALL_AI_CONTEXTUAL_HELP,
    new_ai_request_id,
    record_ai_template_call,
    usage_metrics,
)
from app.ai.role_contracts import (
    CandidateDecision,
    OutputMode,
    RoleContract,
    candidate_decision_schema,
    parse_candidate_decision,
    validate_candidate_ids,
)
from app.ai.system_config import system_ai_env
from app.ai.utils import apply_thinking_control, strip_think_tags
from app.infrastructure.ai.contextual_help import client as help_client
from app.models import User

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 256
_TEMPERATURE = 0.2
# 說明就是說明，長了沒人看。超過就截斷，不讓模型把整頁教學倒出來。
_MAX_ANSWER_CHARS = 400

CONTEXTUAL_HELP_CONTRACT = RoleContract(
    role_id="contextual_help",
    output_mode=OutputMode.SERVER_RENDERED,
    contract_version="contextual-help-candidates-v1",
    fallback_key="contextual_help.scope_clarification",
)
CONTEXTUAL_HELP_FALLBACK = (
    "我可以說明目前 SkyLab 畫面的欄位、限制與錯誤；請指出要了解的項目。"
)


def _answer_candidates(
    surface: SurfaceSpec,
    context: dict[str, Any],
    *,
    active_target: str | None,
    blocked: list[str],
) -> tuple[list[dict[str, Any]], dict[str, tuple[HelpIntent, str | None, str]]]:
    candidates: list[dict[str, Any]] = []
    candidate_map: dict[str, tuple[HelpIntent, str | None, str]] = {}

    page_id = "answer:page_overview"
    page_answer = _fallback_answer(
        surface, "page_overview", active_target=None, blocked=[]
    )
    candidate_map[page_id] = ("page_overview", None, page_answer)
    candidates.append(
        {
            "candidate_id": page_id,
            "kind": "page_overview",
            "title": surface.title,
            "facts": {
                "purpose": surface.purpose,
                "sections": list(surface.sections),
            },
        }
    )

    if active_target:
        element = find_element(surface, active_target)
        if element:
            candidate_id = f"answer:field:{element.id}"
            answer = _fallback_answer(
                surface,
                "field_help",
                active_target=element.id,
                blocked=blocked,
            )
            candidate_map[candidate_id] = ("field_help", element.id, answer)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "kind": "field_help",
                    "label": element.label,
                    "facts": context.get("target") or {},
                }
            )

    if blocked:
        candidate_id = "answer:validation"
        answer = _fallback_answer(
            surface,
            "validation_help",
            active_target=active_target,
            blocked=blocked,
        )
        candidate_map[candidate_id] = (
            "validation_help",
            blocked[0],
            answer,
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "kind": "validation_help",
                "facts": context.get("blocked") or [],
            }
        )
    return candidates, candidate_map


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
    *,
    active_target: str | None,
    blocked: list[str],
) -> str:
    """模型不可用時的答案。講得硬，但不會錯。

    只在 ``_deterministic_answer`` 已經答不出來之後才會被呼叫（見 :func:`explain`），
    所以這裡不再重試確定性答案。
    """
    if intent == "field_help" and active_target:
        element = find_element(surface, active_target)
        if element:
            parts = [f"「{element.label}」"]
            if element.help:
                parts.append(element.help.split("。", 1)[0] + "。")
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

    return f"「{surface.title}」：{surface.purpose.split('。', 1)[0]}。"


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
            answer=CONTEXTUAL_HELP_FALLBACK,
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )

    _log = functools.partial(
        record_ai_template_call,
        session=session,
        user_id=current_user.id,
        call_type=CALL_AI_CONTEXTUAL_HELP,
        model_name=model_name,
    )

    candidate_data, candidate_map = _answer_candidates(
        surface,
        context,
        active_target=active_target,
        blocked=blocked,
    )
    payload = {
        "model": model_name,
        "messages": build_candidate_messages(
            candidate_data, request.question.strip()
        ),
        "max_tokens": _MAX_TOKENS,
        "temperature": _TEMPERATURE,
        "top_p": 0.95,
        "top_k": 64,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": CONTEXTUAL_HELP_CONTRACT.contract_version,
                "schema": candidate_decision_schema(
                    list(candidate_map), max_items=1
                ),
            },
        },
    }
    apply_thinking_control(payload, enable_thinking=False)

    request_id = new_ai_request_id()
    started = perf_counter()
    started_at = datetime.now(timezone.utc)
    try:
        response_data = await help_client.create_chat_completion(
            payload, timeout=_TIMEOUT_SECONDS, request_id=request_id
        )
        metrics = usage_metrics(
            response_data,
            perf_counter() - started,
            request_id=request_id,
            started_at=started_at,
        )
        choice = response_data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("Contextual-help decision was truncated")
        message = choice["message"]
        if message.get("tool_calls"):
            raise ValueError("Contextual-help decision must not contain tool calls")
        content = strip_think_tags(str(message.get("content") or ""))
        parsed = json.loads(content)
        decision = CandidateDecision.model_validate(
            parse_candidate_decision(parsed).model_dump()
        )
        candidate_ids = validate_candidate_ids(
            decision, frozenset(candidate_map), max_items=1
        )
        if candidate_ids:
            intent, target, answer = candidate_map[candidate_ids[0]]
            selected_target = target if intent == "field_help" else None
            context, grounded, level = resolve_context(
                surface,
                intent,
                active_target=selected_target,
                state=state,
            )
        else:
            target = None
            answer = CONTEXTUAL_HELP_FALLBACK
        _log(metrics=metrics)
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
        _log(
            metrics=usage_metrics(
                {},
                perf_counter() - started,
                request_id=request_id,
                started_at=started_at,
            ),
            status="error",
            error_message=str(exc),
        )
        return ExplainResponse(
            intent=intent,
            answer=CONTEXTUAL_HELP_FALLBACK,
            target=target,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )
