from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.ai.adherence_check import ADHERENCE_MAX_TOKENS, check_adherence
from app.ai.role_contracts import (
    AdherenceReason,
    AdherenceVerdict,
    CandidateDecision,
    OutputMode,
    RoleContract,
    candidate_decision_schema,
    parse_candidate_decision,
    validate_candidate_ids,
)
from app.ai.system_config import SystemAIVLLMConfig


class FakeClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.payloads: list[dict[str, Any]] = []

    async def create_chat_completion(self, payload: dict[str, Any], **_kwargs: Any):
        self.payloads.append(payload)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _contract(mode: OutputMode) -> RoleContract:
    return RoleContract("test_role", mode, "v1", "test.fallback")


def _response(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ]
    }


def test_candidate_decision_rejects_extra_fields_and_unknown_ids() -> None:
    with pytest.raises(ValueError):
        parse_candidate_decision({"candidate_ids": ["a"], "answer": "任意文字"})
    decision = CandidateDecision(candidate_ids=["a"])
    with pytest.raises(ValueError, match="unknown"):
        validate_candidate_ids(decision, {"b"}, 1)


def test_candidate_decision_rejects_duplicates_and_preserves_order() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_candidate_ids(CandidateDecision(candidate_ids=["a", "a"]), {"a"}, 2)
    assert validate_candidate_ids(
        CandidateDecision(candidate_ids=["b", "a"]), {"a", "b"}, 2
    ) == ("b", "a")


def test_candidate_schema_is_closed_and_bounded() -> None:
    schema = candidate_decision_schema(["a", "b"], 2)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["candidate_ids"]["uniqueItems"] is True
    assert schema["properties"]["candidate_ids"]["items"]["enum"] == ["a", "b"]


def test_system_ai_config_rejects_unsafe_generation_limits() -> None:
    assert SystemAIVLLMConfig(max_tokens=8192, chat_max_tool_rounds=6)
    with pytest.raises(ValidationError):
        SystemAIVLLMConfig(max_tokens=8193)
    with pytest.raises(ValidationError):
        SystemAIVLLMConfig(chat_max_tool_rounds=7)


@pytest.mark.asyncio
async def test_server_rendered_never_calls_checker_model() -> None:
    client = FakeClient(RuntimeError("must not be called"))
    result = await check_adherence(
        client,
        _contract(OutputMode.SERVER_RENDERED),
        "question",
        "answer",
        {},
        "request-id",
        model_name="model",
        phase="respond",
    )
    assert result.allowed is True
    assert client.payloads == []


@pytest.mark.asyncio
async def test_free_text_allow_uses_closed_schema_without_tools() -> None:
    response = _response('{"verdict":"allow","reason_code":"none"}')
    response["model"] = "guard-model"
    response["usage"] = {
        "prompt_tokens": 20,
        "completion_tokens": 6,
        "total_tokens": 26,
    }
    client = FakeClient(response)
    result = await check_adherence(
        client,
        _contract(OutputMode.MODEL_FREE_TEXT),
        "請說明欄位",
        "這是欄位說明",
        {"label": "名稱"},
        "request-id",
        model_name="model",
        phase="respond",
    )
    assert result.verdict is AdherenceVerdict.ALLOW
    assert result.prompt_tokens == 20
    assert result.completion_tokens == 6
    assert result.total_tokens == 26
    assert result.usage_reported is True
    assert result.response_model == "guard-model"
    payload = client.payloads[0]
    assert "tools" not in payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_missing_checker_usage_is_charged_conservatively() -> None:
    result = await check_adherence(
        FakeClient(_response('{"verdict":"allow","reason_code":"none"}')),
        _contract(OutputMode.MODEL_FREE_TEXT),
        "請說明欄位",
        "這是欄位說明",
        {},
        "request-id",
        model_name="model",
        phase="respond",
    )

    assert result.completion_tokens == ADHERENCE_MAX_TOKENS
    assert result.usage_reported is False


@pytest.mark.asyncio
async def test_malformed_checker_usage_is_charged_conservatively() -> None:
    response = _response('{"verdict":"allow","reason_code":"none"}')
    response["usage"] = {
        "prompt_tokens": "invalid",
        "completion_tokens": {"invalid": True},
        "total_tokens": "invalid",
    }

    result = await check_adherence(
        FakeClient(response),
        _contract(OutputMode.MODEL_FREE_TEXT),
        "請說明欄位",
        "這是欄位說明",
        {},
        "request-id",
        model_name="model",
        phase="respond",
    )

    assert result.allowed is True
    assert result.completion_tokens == ADHERENCE_MAX_TOKENS
    assert result.total_tokens == ADHERENCE_MAX_TOKENS
    assert result.usage_reported is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("down"),
        _response("not-json"),
        _response('{"verdict":"allow","reason_code":"none"}', finish_reason="length"),
        _response('{"verdict":"allow","reason_code":"role_drift"}'),
    ],
)
async def test_checker_failures_are_insufficient_context(response: Any) -> None:
    result = await check_adherence(
        FakeClient(response),
        _contract(OutputMode.MODEL_ACTION),
        "查詢狀態",
        {"tool": "delete"},
        {},
        "request-id",
        model_name="model",
        phase="act",
    )
    assert result.verdict is AdherenceVerdict.INSUFFICIENT_CONTEXT
    assert result.reason_code is AdherenceReason.CHECK_FAILED
