from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any

from sqlmodel import Session

from app.ai.monitoring import CALL_AI_NAVIGATION, record_ai_template_call
from app.ai.navigation.catalog import (
    NavigationRoute,
    find_route_by_path,
    get_routes_for_user,
)
from app.ai.navigation.prompt import build_navigation_system_prompt
from app.ai.navigation.schemas import (
    NavigationAction,
    NavigationResolveResponse,
    NavigationTarget,
)
from app.ai.system_config import system_ai_env
from app.ai.utils import strip_think_tags
from app.infrastructure.ai.navigation import client as navigation_client
from app.models import User

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 20.0
_DEFAULT_MAX_TOKENS = 450
_DEFAULT_TEMPERATURE = 0.1


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    for idx in range(start, len(text)):
        char = text[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    return None


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, confidence))


def _mk_target(route: NavigationRoute, reason: str) -> NavigationTarget:
    return NavigationTarget(
        title=route.title,
        path=route.path,
        reason=reason.strip() or route.summary,
    )


def _normalize_action(value: Any, confidence: float, has_primary: bool) -> NavigationAction:
    action = str(value or "").strip().lower()
    if action in {"navigate", "suggest", "clarify"}:
        return action  # type: ignore[return-value]
    if has_primary and confidence >= 0.85:
        return "navigate"
    if has_primary:
        return "suggest"
    return "clarify"


def _keyword_fallback(query: str, routes: list[NavigationRoute]) -> NavigationResolveResponse:
    text = query.lower()
    scored: list[tuple[int, NavigationRoute]] = []
    for route in routes:
        score = sum(1 for kw in route.keywords if kw.lower() in text)
        if score > 0:
            scored.append((score, route))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return NavigationResolveResponse(
            intent=query.strip(),
            confidence=0.25,
            action="clarify",
            suggestions=[],
            clarification_question="你想管理的是資源、申請流程、網路設定，還是 AI 用量？",
        )

    primary_score, primary_route = scored[0]
    suggestions = [_mk_target(route, route.summary) for _, route in scored[1:4]]
    if primary_score >= 2:
        return NavigationResolveResponse(
            intent=query.strip(),
            confidence=0.86,
            action="navigate",
            primary=_mk_target(primary_route, primary_route.summary),
            suggestions=suggestions,
        )

    return NavigationResolveResponse(
        intent=query.strip(),
        confidence=0.7,
        action="suggest",
        primary=_mk_target(primary_route, primary_route.summary),
        suggestions=suggestions,
        clarification_question="我先給你最可能的入口，也可以從下面候選頁面選一個。",
    )


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


def _build_response_from_payload(
    payload: dict[str, Any],
    *,
    user_query: str,
    allowed_routes: list[NavigationRoute],
) -> NavigationResolveResponse:
    intent = str(payload.get("intent") or user_query).strip() or user_query
    confidence = _clamp_confidence(payload.get("confidence"))
    reason = str(payload.get("reason") or "").strip()
    primary_path = str(payload.get("primary_path") or "").strip()
    suggested_paths = payload.get("suggested_paths") or []
    clarification_question = str(payload.get("clarification_question") or "").strip()

    primary_route = find_route_by_path(primary_path, allowed_routes) if primary_path else None
    primary_target = _mk_target(primary_route, reason) if primary_route else None

    suggestions: list[NavigationTarget] = []
    seen_paths: set[str] = {primary_target.path} if primary_target else set()
    if isinstance(suggested_paths, list):
        for item in suggested_paths:
            path = str(item or "").strip()
            if not path or path in seen_paths:
                continue
            route = find_route_by_path(path, allowed_routes)
            if route is None:
                continue
            suggestions.append(_mk_target(route, route.summary))
            seen_paths.add(path)
            if len(suggestions) >= 4:
                break

    action = _normalize_action(
        payload.get("action"),
        confidence=confidence,
        has_primary=primary_target is not None,
    )

    if action == "navigate" and confidence < 0.85:
        action = "suggest"
    if action == "navigate" and primary_target is None and suggestions:
        action = "suggest"
    if action in {"navigate", "suggest"} and primary_target is None and suggestions:
        primary_target = suggestions[0]
        suggestions = suggestions[1:]
    if action in {"navigate", "suggest"} and primary_target is None and not suggestions:
        action = "clarify"
    if action == "clarify" and not clarification_question:
        clarification_question = "你想要我幫你導向哪一類功能頁面？"

    return NavigationResolveResponse(
        intent=intent,
        confidence=confidence,
        action=action,
        primary=primary_target,
        suggestions=suggestions,
        clarification_question=clarification_question or None,
    )


async def resolve_navigation(
    query: str,
    current_user: User,
    session: Session | None = None,
) -> NavigationResolveResponse:
    clean_query = query.strip()
    allowed_routes = list(get_routes_for_user(current_user))
    if not clean_query:
        return NavigationResolveResponse(
            intent="",
            confidence=0.0,
            action="clarify",
            clarification_question="請先輸入你目前想完成的需求。",
        )
    if not allowed_routes:
        return NavigationResolveResponse(
            intent=clean_query,
            confidence=0.0,
            action="clarify",
            clarification_question="目前沒有可導覽的頁面，請先確認帳號權限。",
        )

    model_name = system_ai_env.vllm_model_name.strip()
    if not model_name:
        logger.warning("VLLM_MODEL_NAME is empty, using keyword fallback for navigation")
        return _keyword_fallback(clean_query, allowed_routes)

    prompt = build_navigation_system_prompt(allowed_routes)
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": clean_query},
        ],
        "max_tokens": _DEFAULT_MAX_TOKENS,
        "temperature": _DEFAULT_TEMPERATURE,
        "top_p": 0.9,
    }

    try:
        started = perf_counter()
        response_data = await navigation_client.create_chat_completion(
            payload,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )
        metrics = _usage_metrics(response_data, perf_counter() - started)
        content = str(response_data["choices"][0]["message"]["content"] or "")
        normalized_text = strip_think_tags(content)
        raw_json = _extract_first_json_object(normalized_text)
        if not raw_json:
            logger.warning("Navigation model returned non-JSON text, using keyword fallback")
            if session is not None:
                record_ai_template_call(
                    session=session,
                    user_id=current_user.id,
                    call_type=CALL_AI_NAVIGATION,
                    model_name=model_name,
                    metrics=metrics,
                    status="error",
                    error_message="Navigation model returned non-JSON text.",
                )
            return _keyword_fallback(clean_query, allowed_routes)

        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            logger.warning("Navigation model returned non-object JSON, using keyword fallback")
            if session is not None:
                record_ai_template_call(
                    session=session,
                    user_id=current_user.id,
                    call_type=CALL_AI_NAVIGATION,
                    model_name=model_name,
                    metrics=metrics,
                    status="error",
                    error_message="Navigation model returned non-object JSON.",
                )
            return _keyword_fallback(clean_query, allowed_routes)

        result = _build_response_from_payload(
            parsed,
            user_query=clean_query,
            allowed_routes=allowed_routes,
        )
        if session is not None:
            record_ai_template_call(
                session=session,
                user_id=current_user.id,
                call_type=CALL_AI_NAVIGATION,
                model_name=model_name,
                metrics=metrics,
            )
        return result
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.exception("Navigation resolve failed, fallback to keyword strategy: %s", exc)
        if session is not None:
            record_ai_template_call(
                session=session,
                user_id=current_user.id,
                call_type=CALL_AI_NAVIGATION,
                model_name=model_name,
                status="error",
                error_message=str(exc),
            )
        return _keyword_fallback(clean_query, allowed_routes)
