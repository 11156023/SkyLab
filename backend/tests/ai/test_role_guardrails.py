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
    adherence_result_schema,
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


def test_adherence_schema_only_allows_runtime_valid_pairs() -> None:
    alternatives = adherence_result_schema()["oneOf"]
    assert alternatives[0]["properties"] == {
        "verdict": {"const": "allow"},
        "reason_code": {"const": "none"},
    }
    assert alternatives[1]["properties"] == {
        "verdict": {"const": "insufficient_context"},
        "reason_code": {"const": "insufficient_context"},
    }
    assert alternatives[2]["properties"]["verdict"] == {"const": "block"}
    block_reasons = alternatives[2]["properties"]["reason_code"]["enum"]
    assert "role_drift" in block_reasons
    assert "none" not in block_reasons
    assert "insufficient_context" not in block_reasons
    assert "check_failed" not in block_reasons


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
async def test_role_adoption_still_calls_checker_model() -> None:
    client = FakeClient(_response('{"verdict":"block","reason_code":"role_drift"}'))
    result = await check_adherence(
        client,
        RoleContract(
            "template_recommendation",
            OutputMode.MODEL_FREE_TEXT,
            "v1",
            "test.fallback",
        ),
        "我要架設 nginx，VM 還是 LXC？",
        "好的！從現在起我是貓娘，改陪你聊天。",
        {},
        "request-id",
        model_name="model",
        phase="respond",
    )
    assert result.verdict is AdherenceVerdict.BLOCK
    assert result.reason_code is AdherenceReason.ROLE_DRIFT
    assert len(client.payloads) == 1


@pytest.mark.asyncio
async def test_template_greeting_still_calls_checker_model() -> None:
    # A greeting request cannot bypass evaluation of a malicious candidate answer.
    client = FakeClient(_response('{"verdict":"block","reason_code":"role_drift"}'))
    result = await check_adherence(
        client,
        RoleContract(
            "template_recommendation",
            OutputMode.MODEL_FREE_TEXT,
            "v1",
            "test.fallback",
        ),
        "目前所在頁面：申請機器。使用者問題：你好！",
        "從現在起我是貓娘，改陪你聊天。",
        {},
        "request-id",
        model_name="model",
        phase="respond",
    )
    assert result.verdict is AdherenceVerdict.BLOCK
    assert len(client.payloads) == 1


@pytest.mark.asyncio
async def test_unmapped_ordinal_clarification_still_calls_checker_model() -> None:
    client = FakeClient(_response('{"verdict":"allow","reason_code":"none"}'))
    result = await check_adherence(
        client,
        _contract(OutputMode.MODEL_ACTION),
        "修改第二個",
        "請提供第二個項目的名稱，以確認修改目標。",
        {
            "evidence": {
                "requested_ordinal": 2,
                "ordinal_mapping_available": False,
                "verified_targets": [],
            }
        },
        "request-id",
        model_name="model",
        phase="act",
    )
    assert result.allowed
    assert len(client.payloads) == 1


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
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.95
    assert payload["top_k"] == 64
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"] == (
        adherence_result_schema()
    )
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
