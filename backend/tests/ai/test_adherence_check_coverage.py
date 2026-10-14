"""Coverage tests for app.ai.adherence_check fail-closed parsing."""

import pytest

from app.ai import adherence_check
from app.ai.role_contracts import (
    AdherenceReason,
    AdherenceVerdict,
    OutputMode,
    RoleContract,
)

_CONTRACT = RoleContract(
    role_id="pve_log",
    output_mode=OutputMode.MODEL_FREE_TEXT,
    contract_version="v1",
    fallback_key="k",
)


class _Client:
    def __init__(self, response):
        self.response = response

    async def create_chat_completion(self, payload, *, profile, timeout, request_id):
        del payload, profile, timeout, request_id
        return self.response


def _message(content, **extra):
    return {"choices": [{"message": {"content": content, **extra}}]}


@pytest.mark.parametrize(
    "response",
    [
        {"choices": []},
        {"choices": ["not-a-dict"]},
        {"choices": [{"finish_reason": "length", "message": {}}]},
        {"choices": [{"message": "text"}]},
        _message("{}", tool_calls=[{"id": "x"}]),
        _message('{"verdict": "allow"}'),
        _message("[]"),
    ],
)
async def test_invalid_responses_fail_closed(response) -> None:
    result = await adherence_check.check_adherence(
        _Client(response),
        _CONTRACT,
        "q",
        "candidate",
        {},
        "req-1",
        model_name="m",
        phase="respond",
    )
    assert result.verdict is AdherenceVerdict.INSUFFICIENT_CONTEXT
    assert result.reason_code is AdherenceReason.CHECK_FAILED


@pytest.mark.parametrize(("model", "request_id"), [(" ", "req"), ("m", " ")])
async def test_blank_model_or_request_id_fails_closed(model, request_id) -> None:
    result = await adherence_check.check_adherence(
        _Client({}),
        _CONTRACT,
        "q",
        "c",
        {},
        request_id,
        model_name=model,
        phase="respond",
    )
    assert result.reason_code is AdherenceReason.CHECK_FAILED
    assert result.completion_tokens == adherence_check.ADHERENCE_MAX_TOKENS
