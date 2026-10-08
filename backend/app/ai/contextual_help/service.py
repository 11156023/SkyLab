"""說明助手的主流程：分類 → 取情境 → 能直接答就直接答 → 否則問模型。

整頁導覽（page_guide）與彈出視窗說明（dialog_help）一律直接由畫面定義組成，
見 :mod:`app.ai.contextual_help.guide`。

有一條刻意的捷徑：情境已經足以拼出正確答案時（例如只有一個欄位有驗證錯誤），
就直接組出那句話，不呼叫模型。這種答案更快、更便宜，而且不可能講錯。模型是
用來把複雜情況講得順，不是用來複誦一行錯誤訊息。

同一組確定性答案也是模型不可用時的後備，所以助手在模型離線時仍然可用——只是
講得比較硬。
"""

from __future__ import annotations

import functools
import logging
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlmodel import Session

from app.ai.adherence_check import check_adherence
from app.ai.contextual_help.guide import (
    match_dialog,
    related_targets,
    render_dialog,
    render_dialog_index,
    render_page_guide,
    visible_dialogs,
)
from app.ai.contextual_help.intent import classify
from app.ai.contextual_help.prompt import build_messages
from app.ai.contextual_help.resolver import (
    LEVEL_SURFACE,
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
from app.ai.navigation.catalog import get_routes_for_user, resolve_user_role
from app.ai.role_contracts import OutputMode, RoleContract, TurnContext
from app.ai.system_config import system_ai_env
from app.ai.utils import apply_thinking_control, strip_think_tags
from app.infrastructure.ai import VLLMRequestProfile
from app.infrastructure.ai.contextual_help import client as help_client
from app.models import User

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15.0
_MAX_TOKENS = 220
_TEMPERATURE = 0.2
# 說明就是說明，長了沒人看。超過就截斷，不讓模型把整頁教學倒出來。
# 整頁導覽不經過模型，不受這個上限限制。
_MAX_ANSWER_CHARS = 400
# 整頁導覽引用了哪些定義，依輸出順序
_GUIDE_PARTS = ("purpose", "when_to_use", "features", "dialogs", "related")
CONTEXTUAL_HELP_CONTRACT = RoleContract(
    role_id="contextual_help",
    output_mode=OutputMode.MODEL_FREE_TEXT,
    contract_version="contextual-help-v1",
    fallback_key="contextual_help.static_definition",
)
_ADHERENCE_ALLOWED_BEHAVIOR = (
    "Explain only supplied UI context; refuse unrelated requests. Do not claim "
    "navigation, submission, workflow execution, or unverified screen positions."
)


def build_help_payload(
    intent: HelpIntent,
    context: dict[str, Any],
    question: str,
    *,
    model_name: str,
) -> dict[str, Any]:
    """建立 production 與 live probe 共用的 Contextual Help 請求。"""

    return apply_thinking_control(
        {
            "model": model_name,
            "messages": build_messages(intent, context, question.strip()),
            "max_tokens": _MAX_TOKENS,
            "temperature": _TEMPERATURE,
            "top_p": 0.9,
            "stream": False,
        },
        enable_thinking=False,
    )


def build_help_adherence_facts(
    *,
    surface_id: str,
    intent: HelpIntent,
    context: dict[str, Any],
    grounded_in: list[str],
    context_level: int,
    context_version: int,
    phase: str = "respond",
) -> dict[str, Any]:
    """以後端已驗證的畫面與目標建立單輪 adherence evidence。"""

    candidate_target_ids: list[str] = []
    target = context.get("target")
    if isinstance(target, dict) and isinstance(target.get("id"), str):
        candidate_target_ids.append(target["id"])
    blocked = context.get("blocked")
    if isinstance(blocked, list):
        for item in blocked:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            if item["id"] not in candidate_target_ids:
                candidate_target_ids.append(item["id"])

    turn_context = TurnContext(
        role_id=CONTEXTUAL_HELP_CONTRACT.role_id,
        phase=phase,
        scope_ref=f"surface:{surface_id}",
        selected_target_id=candidate_target_ids[0] if candidate_target_ids else None,
        candidate_target_ids=tuple(candidate_target_ids),
    )
    return {
        "turn_context": turn_context.as_facts(),
        "evidence": {
            "intent": intent,
            "ui_context": context,
            "grounded_in": grounded_in,
            "context_level": context_level,
            "context_version": context_version,
            "allowed_behavior": _ADHERENCE_ALLOWED_BEHAVIOR,
        },
    }


def _parse_model_answer(response_data: dict[str, Any]) -> str:
    choices = response_data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("contextual help response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") == "length":
        raise ValueError("contextual help response was truncated")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("tool_calls"):
        raise ValueError("contextual help response must not contain tool calls")
    answer = strip_think_tags(str(message.get("content") or "")).strip()
    if not answer:
        raise ValueError("empty answer from contextual help model")
    return answer


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

    answer = f"「{surface.title}」：{surface.purpose.split('。', 1)[0]}。"
    if surface.when_to_use:
        answer += f"什麼時候用：{surface.when_to_use.split('。', 1)[0]}。"
    return answer


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
    named_element = False
    if active_target is None:
        # 前端還沒回報 focus，或使用者問的不是游標所在的欄位：
        # 問題裡指名了哪一個元素就用哪一個。
        named = match_element_by_label(surface, request.question)
        if named is not None:
            active_target = named.id
            named_element = True
    dialogs = visible_dialogs(surface, resolve_user_role(current_user))
    dialog = match_dialog(dialogs, request.question)

    intent = classify(
        request.question,
        has_active_target=bool(active_target),
        has_blocked=bool(blocked),
        named_element=named_element,
        named_dialog=dialog is not None,
        has_dialogs=bool(dialogs),
    )
    related = related_targets(surface, get_routes_for_user(current_user))

    # 導覽與視窗說明：定義裡就有完整答案，照排版輸出，不打模型。
    if intent == "page_guide" or (intent == "dialog_help" and dialogs):
        if intent == "page_guide":
            answer = render_page_guide(surface, dialogs, related)
            grounded = [f"{surface.id}.{key}" for key in _GUIDE_PARTS if getattr(surface, key)]
        elif dialog is not None:
            answer = render_dialog(dialog)
            grounded = [f"{surface.id}.dialogs.{dialog.id}"]
        else:
            answer = render_dialog_index(surface, dialogs)
            grounded = [f"{surface.id}.dialogs.{item.id}" for item in dialogs]
        return ExplainResponse(
            intent=intent,
            answer=answer,
            related=related if intent == "page_guide" else [],
            grounded_in=grounded,
            context_level=LEVEL_SURFACE,
            context_version=request.context_version,
            used_model=False,
        )
    if intent == "dialog_help":
        intent = "page_overview"

    context, grounded, level = resolve_context(
        surface, intent, active_target=active_target, state=state
    )
    # resolve_context 在目標未知時會退回頁面概觀，這裡跟著回正。
    if intent == "field_help" and "target" not in context:
        intent = "page_overview"

    # 頁面簡介可以順帶指路：「這頁不是你要的，改去 X」。只給有權限的頁面，
    # 模型也只能從這份清單裡挑（見 prompt）。
    page_related = related if intent == "page_overview" else []
    if page_related:
        context["related"] = [
            {"title": item.title, "when": item.reason} for item in page_related
        ]

    target = active_target or (blocked[0] if blocked else None)

    direct = _deterministic_answer(
        surface, intent, context, active_target=active_target, blocked=blocked
    )
    if direct is not None:
        return ExplainResponse(
            intent=intent,
            answer=direct,
            target=target,
            related=page_related,
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
                surface, intent, active_target=active_target, blocked=blocked
            ),
            target=target,
            related=page_related,
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

    payload = build_help_payload(
        intent,
        context,
        request.question,
        model_name=model_name,
    )

    request_id = new_ai_request_id()
    started = perf_counter()
    started_at = datetime.now(timezone.utc)
    response_data: dict[str, Any] = {}
    try:
        response_data = await help_client.create_chat_completion(
            payload,
            profile=VLLMRequestProfile.BOUNDED_EXPLANATION,
            timeout=_TIMEOUT_SECONDS,
            request_id=request_id,
        )
        metrics = usage_metrics(
            response_data,
            perf_counter() - started,
            request_id=request_id,
            started_at=started_at,
        )
        # adherence 必須檢查實際會顯示的字串，而不是截斷前的另一個版本。
        answer = _parse_model_answer(response_data)[:_MAX_ANSWER_CHARS]
        _log(metrics=metrics)

        guard_request_id = f"{request_id}:adherence"
        guard_result = await check_adherence(
            help_client,
            CONTEXTUAL_HELP_CONTRACT,
            request.question.strip(),
            answer,
            build_help_adherence_facts(
                surface_id=surface.id,
                intent=intent,
                context=context,
                grounded_in=grounded,
                context_level=level,
                context_version=request.context_version,
            ),
            guard_request_id,
            model_name=model_name,
            phase="respond",
        )
        record_ai_template_call(
            session=session,
            user_id=current_user.id,
            call_type="contextual_help_adherence",
            model_name=model_name,
            metrics={
                "request_id": guard_request_id,
                "prompt_tokens": guard_result.prompt_tokens,
                "completion_tokens": guard_result.completion_tokens,
                "total_tokens": guard_result.total_tokens,
                "elapsed_seconds": guard_result.elapsed_seconds,
                "usage_reported": guard_result.usage_reported,
                "response_model": guard_result.response_model,
            },
            status="success" if guard_result.allowed else "error",
            error_message=None if guard_result.allowed else guard_result.reason_code.value,
        )
        if not guard_result.allowed:
            logger.warning(
                "Contextual help answer blocked by adherence check: request_id=%s reason=%s",
                request_id,
                guard_result.reason_code.value,
            )
            answer = _fallback_answer(
                surface, intent, active_target=active_target, blocked=blocked
            )
            used_model = False
        else:
            used_model = True
        return ExplainResponse(
            intent=intent,
            answer=answer[:_MAX_ANSWER_CHARS],
            target=target,
            related=page_related,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=used_model,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Contextual help failed, using deterministic answer: %s", exc)
        _log(
            metrics=usage_metrics(
                response_data,
                perf_counter() - started,
                request_id=request_id,
                started_at=started_at,
            ),
            status="error",
            error_message=str(exc),
        )
        return ExplainResponse(
            intent=intent,
            answer=_fallback_answer(
                surface, intent, active_target=active_target, blocked=blocked
            ),
            target=target,
            related=page_related,
            grounded_in=grounded,
            context_level=level,
            context_version=request.context_version,
            used_model=False,
        )
