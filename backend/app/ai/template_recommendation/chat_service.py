"""Shared production chat requests and adherence evidence for template planning."""

from __future__ import annotations

import json
from typing import Any

from app.ai.role_contracts import TurnContext
from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.prompt import (
    build_chat_runtime_context,
    build_chat_system_prompt,
    build_intake_focus_block,
)
from app.ai.template_recommendation.schemas import ChatRequest
from app.ai.utils import (
    apply_thinking_control,
    ensure_conversation_within_limits,
    ensure_form_context_within_limits,
)

_CLIENT_OPTION_FIELDS = {
    "gpu_options",
    "lxc_os_options",
    "vm_os_options",
    "resource_options_from_client",
}


def chat_form_snapshot(request: ChatRequest) -> dict[str, Any] | None:
    """Client form values are context; option inventories come from the backend."""
    form = request.form_context
    snapshot = (
        form.model_dump(mode="json", exclude=_CLIENT_OPTION_FIELDS) if form else None
    )
    ensure_form_context_within_limits(json.dumps(snapshot or {}, ensure_ascii=False))
    return snapshot


def latest_user_request(request: ChatRequest) -> str:
    return next(
        (
            message.content.strip()
            for message in reversed(request.messages)
            if message.role == "user"
        ),
        "",
    )


def build_chat_payload(
    request: ChatRequest,
    *,
    gpu_options: list[dict[str, Any]],
    model_name: str,
) -> dict[str, Any]:
    ensure_conversation_within_limits(request.messages)
    snapshot = chat_form_snapshot(request)
    runtime = (
        build_chat_runtime_context(
            resource_type=snapshot.get("resource_type") if snapshot else None,
            gpu_options=gpu_options,
            form_context=snapshot,
        )
        if gpu_options or snapshot is not None
        else ""
    )
    prompt = build_chat_system_prompt(
        is_first_turn=len(request.messages) <= 1, runtime_context=runtime
    )
    if request.focus_hint:
        prompt += f"\n\n{build_intake_focus_block(request.focus_hint.strip())}"
    return apply_thinking_control(
        {
            "model": model_name,
            "messages": [
                {"role": "system", "content": prompt},
                *[message.model_dump() for message in request.messages],
            ],
            "max_tokens": settings.VLLM_CHAT_MAX_TOKENS,
            "temperature": settings.VLLM_CHAT_TEMPERATURE,
            "top_p": settings.VLLM_TOP_P,
            "top_k": settings.VLLM_TOP_K,
            "min_p": settings.VLLM_MIN_P,
            "repetition_penalty": settings.VLLM_REPETITION_PENALTY,
        },
        settings.VLLM_ENABLE_THINKING,
    )


def template_adherence_facts(
    *,
    phase: str,
    scope_ref: str,
    evidence: dict[str, Any],
    allowed_actions: tuple[str, ...] = (),
) -> dict[str, Any]:
    context = TurnContext(
        role_id="template_recommendation",
        phase=phase,
        scope_ref=scope_ref,
        allowed_actions=allowed_actions,
    )
    return {"turn_context": context.as_facts(), "evidence": evidence}


def build_chat_adherence_facts(
    request: ChatRequest, *, gpu_options: list[dict[str, Any]]
) -> dict[str, Any]:
    ensure_conversation_within_limits(request.messages)
    return template_adherence_facts(
        phase="respond",
        scope_ref="template_recommendation:chat",
        evidence={
            # Same bounded conversation and form values seen by the answering model.
            "conversation_history": [
                message.model_dump() for message in request.messages
            ],
            "form_context": chat_form_snapshot(request),
            "focus_hint": request.focus_hint,
            "gpu_options": [
                {
                    key: option.get(key)
                    for key in (
                        "mapping_id",
                        "description",
                        "model",
                        "vram",
                        "node",
                        "available_count",
                        "capacity_count",
                        "device_count",
                    )
                }
                for option in gpu_options[:10]
            ],
            "form_values_source": "client_snapshot_not_backend_capability_inventory",
        },
    )
