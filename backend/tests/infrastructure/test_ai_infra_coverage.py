"""Coverage for app.infrastructure.ai vllm_client validators, model_adapter and litellm_runtime."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.infrastructure.ai import litellm_runtime
from app.infrastructure.ai.model_adapter import ModelProfile, ModelProfiles
from app.infrastructure.ai.vllm_client import (
    VLLMProfileError,
    VLLMRequestProfile,
    _validate_request,
    _validate_response,
)

_TEXT = VLLMRequestProfile.CONFIGURED_TEXT
_AGENT = VLLMRequestProfile.COMPLEX_AGENT
_OBJECT = VLLMRequestProfile.STRUCTURED_OBJECT

_TOOL = {"type": "function", "function": {"name": "lookup", "parameters": {}}}


def _payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16,
    }
    data.update(overrides)
    return data


def _agent_payload(**overrides: Any) -> dict[str, Any]:
    return _payload(tools=[_TOOL], tool_choice="auto", **overrides)


# ---------------------------------------------------------------- request


@pytest.mark.parametrize(
    ("payload", "profile", "match"),
    [
        (_payload(), "configured_text", "valid VLLMRequestProfile"),
        (_payload(model="  "), _TEXT, "requires a model"),
        (_payload(messages=[]), _TEXT, "requires messages"),
        (_payload(tools=[_TOOL]), _TEXT, "does not allow tools"),
        (_payload(tool_choice="auto"), _TEXT, "does not allow tools"),
        (_payload(tools=[]), _AGENT, "non-empty tools list"),
        (_payload(tools="x"), _AGENT, "non-empty tools list"),
        (_payload(tools=["bad"]), _AGENT, "invalid tool definition"),
        (
            _payload(tools=[{"type": "function", "function": {"name": " "}}]),
            _AGENT,
            "invalid tool definition",
        ),
        (_payload(tools=[_TOOL]), _AGENT, "requires tool_choice"),
        (
            _payload(tools=[_TOOL], tool_choice="sometimes"),
            _AGENT,
            "invalid tool_choice",
        ),
        (
            _payload(tools=[_TOOL], tool_choice={"type": "other"}),
            _AGENT,
            "invalid tool_choice",
        ),
        (
            _payload(
                tools=[_TOOL],
                tool_choice={"type": "function", "function": {"name": ""}},
            ),
            _AGENT,
            "invalid tool_choice",
        ),
        (
            _agent_payload(response_format={"type": "json_object"}),
            _AGENT,
            "cannot combine tools",
        ),
        (_payload(), _OBJECT, "requires response_format=json_object"),
        (
            _payload(response_format={"type": "json_schema"}),
            _OBJECT,
            "requires response_format=json_object",
        ),
        (
            _payload(response_format={"type": "json_object"}),
            _TEXT,
            "does not allow response_format",
        ),
    ],
)
def test_validate_request_rejects(
    payload: dict[str, Any], profile: Any, match: str
) -> None:
    with pytest.raises(VLLMProfileError, match=match):
        _validate_request(payload, profile)


# ---------------------------------------------------------------- response


def _response(message: Any, **choice: Any) -> dict[str, Any]:
    return {"choices": [{"message": message, **choice}]}


@pytest.mark.parametrize(
    ("response", "profile", "match"),
    [
        ("not-a-dict", _TEXT, "non-object response"),
        ({"choices": []}, _TEXT, "exactly one response choice"),
        ({"choices": ["x"]}, _TEXT, "invalid response choice"),
        (_response("x"), _TEXT, "invalid assistant message"),
        (_response({"tool_calls": "abc"}), _AGENT, "invalid tool_calls envelope"),
        (_response({"tool_calls": ["x"]}), _AGENT, "invalid native tool call"),
        (
            _response({"tool_calls": [{"function": {"name": " "}}]}),
            _AGENT,
            "invalid native tool call",
        ),
        (
            _response(
                {"tool_calls": [{"function": {"name": "f", "arguments": "{bad"}}]}
            ),
            _AGENT,
            "invalid tool arguments",
        ),
        (
            _response(
                {"tool_calls": [{"function": {"name": "f", "arguments": "[1]"}}]}
            ),
            _AGENT,
            "object tool arguments",
        ),
        (
            _response({"tool_calls": [{"function": {"name": "f", "arguments": "{}"}}]}),
            _TEXT,
            "forbidden tool calls",
        ),
        (_response({"content": "  "}), _TEXT, "non-empty assistant content"),
        (_response({"content": "not json"}), _OBJECT, "invalid JSON content"),
        (_response({"content": "[1, 2]"}), _OBJECT, "JSON object response"),
    ],
)
def test_validate_response_rejects(response: Any, profile: Any, match: str) -> None:
    with pytest.raises(VLLMProfileError, match=match):
        _validate_response(response, profile)


# ---------------------------------------------------------------- model_adapter


def _profile_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "thinking": "none",
        "thinking_parameter": "enable_thinking",
        "response_formats": ["json_object"],
        "native_tool_calls": True,
        "tool_choice_modes": ["auto"],
    }
    data.update(overrides)
    return data


def test_model_profile_rejects_duplicate_response_formats() -> None:
    with pytest.raises(ValidationError, match="duplicate response_formats"):
        ModelProfile.model_validate(
            _profile_data(response_formats=["json_object", "json_object"])
        )


def test_model_profiles_rejects_whitespace_model_keys() -> None:
    with pytest.raises(ValidationError, match="surrounding whitespace"):
        ModelProfiles.model_validate(
            {"schema_version": 1, "models": {" m ": _profile_data()}}
        )


# ---------------------------------------------------------------- litellm_runtime


def test_short_error_timeout() -> None:
    assert litellm_runtime._short_error(TimeoutError("x")) == "timeout"
    assert litellm_runtime._short_error(ValueError("boom")) == "ValueError: boom"
