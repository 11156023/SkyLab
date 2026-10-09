from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.infrastructure.ai import model_adapter as adapter

GPT = "openai/gpt-oss-120b"
GEMMA = "gemma4-26b-a4b-it"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    adapter.load_model_profiles.cache_clear()
    yield
    adapter.load_model_profiles.cache_clear()


def _payload(model: str = GPT) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 128,
        "temperature": 0.2,
        "top_p": 0.95,
        "top_k": 64,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"enable_thinking": False, "custom": "keep"},
    }


def _config_file(monkeypatch, tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "llm-model-profiles.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(adapter, "CONFIG_FILE", path)
    adapter.load_model_profiles.cache_clear()
    return path


def _config() -> dict:
    return adapter.load_model_profiles().model_dump()


@pytest.mark.parametrize("model", [GPT, GEMMA])
@pytest.mark.parametrize("legacy_effort", ["none", "off", "low", "medium", "high"])
@pytest.mark.parametrize("legacy_bool", [False, True])
def test_model_contract_owns_thinking_and_preserves_other_fields(
    model: str, legacy_effort: str, legacy_bool: bool
) -> None:
    payload = _payload(model)
    payload["reasoning_effort"] = legacy_effort
    payload["chat_template_kwargs"].update(
        enable_thinking=legacy_bool, reasoning_effort=legacy_effort
    )
    original = copy.deepcopy(payload)
    wire = adapter.adapt_chat_request(payload)
    if model == GPT:
        assert wire["reasoning_effort"] == "low"
        assert wire["chat_template_kwargs"] == {"custom": "keep"}
    else:
        assert "reasoning_effort" not in wire
        assert wire["chat_template_kwargs"] == {
            "custom": "keep",
            "enable_thinking": False,
        }
    for key in payload.keys() - {"reasoning_effort", "chat_template_kwargs"}:
        assert wire[key] == payload[key]
    assert payload == original
    assert adapter.adapt_chat_request(wire) == wire


def test_yaml_supplies_thinking_without_any_legacy_field() -> None:
    for model in (GPT, GEMMA):
        payload = _payload(model)
        payload.pop("chat_template_kwargs")
        wire = adapter.adapt_chat_request(payload)
        if model == GPT:
            assert wire["reasoning_effort"] == "low"
            assert "chat_template_kwargs" not in wire
        else:
            assert wire["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize(
    "mutation",
    [
        {"model": "unknown-model"},
        {"model": " openai/gpt-oss-120b"},
        {"model": None},
        {"chat_template_kwargs": []},
        {"chat_template_kwargs": {"enable_thinking": "false"}},
        {"reasoning_effort": {}},
        {"reasoning_effort": "xhigh"},
        {"chat_template_kwargs": {"reasoning_effort": None}},
    ],
)
def test_bad_requests_fail_locally(mutation: dict) -> None:
    with pytest.raises(adapter.ModelAdapterError):
        adapter.adapt_chat_request({**_payload(), **mutation})


def test_config_is_cached_and_independent_of_cwd(monkeypatch, tmp_path) -> None:
    original = adapter.load_model_profiles()
    monkeypatch.chdir(tmp_path)
    assert adapter.load_model_profiles() is original
    adapter.load_model_profiles.cache_clear()
    assert adapter.load_model_profiles() == original
    path = _config_file(monkeypatch, tmp_path, original.model_dump())
    cached = adapter.load_model_profiles()
    path.write_text("invalid: true", encoding="utf-8")
    assert adapter.load_model_profiles() is cached
    adapter.load_model_profiles.cache_clear()
    with pytest.raises(adapter.ModelAdapterError):
        adapter.load_model_profiles()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thinking", None),
        ("thinking", False),
        ("thinking", "high"),
        ("thinking", "none"),
        ("thinking_parameter", "arbitrary.field"),
        ("native_tool_calls", "true"),
        ("native_tool_calls", False),
        ("response_formats", ["text"]),
        ("tool_choice_modes", ["guess"]),
        ("tool_choice_modes", ["auto", "auto"]),
        ("max_tokens", 1024),
    ],
)
def test_invalid_profile_configuration_is_rejected(monkeypatch, tmp_path, field, value):
    config = _config()
    config["models"][GPT][field] = value
    _config_file(monkeypatch, tmp_path, config)
    with pytest.raises(adapter.ModelAdapterError):
        adapter.load_model_profiles()


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"schema_version": True, "models": {}},
        {"schema_version": 2, "models": {}},
        {"schema_version": 1, "models": {}},
        {"schema_version": 1, "models": {" ": {}}},
    ],
)
def test_invalid_config_root_is_rejected(monkeypatch, tmp_path, config):
    _config_file(monkeypatch, tmp_path, config)
    with pytest.raises(adapter.ModelAdapterError):
        adapter.load_model_profiles()


@pytest.mark.parametrize(
    "contents",
    [
        "schema_version: 1\nschema_version: 1\nmodels: {}",
        "schema_version: 1\nmodels:\n  off: {}",
        "models: [",
        "!!python/object:os.system {}",
        "? [a, b]\n: value",
    ],
)
def test_bad_yaml_is_rejected(monkeypatch, tmp_path, contents):
    path = _config_file(monkeypatch, tmp_path, {})
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(adapter.ModelAdapterError):
        adapter.load_model_profiles()


def test_missing_file_fails_with_the_config_path(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "CONFIG_FILE", tmp_path / "missing.yaml")
    with pytest.raises(adapter.ModelAdapterError, match="missing.yaml"):
        adapter.load_model_profiles()


def test_template_low_is_owned_by_yaml(monkeypatch, tmp_path):
    config = _config()
    config["models"][GEMMA]["thinking"] = "low"
    _config_file(monkeypatch, tmp_path, config)
    assert (
        adapter.adapt_chat_request(_payload(GEMMA))["chat_template_kwargs"][
            "enable_thinking"
        ]
        is True
    )
    assert adapter.adapt_chat_request(_payload(GPT))["reasoning_effort"] == "low"


@pytest.mark.parametrize("mode", ["auto", "none", "required", "named"])
def test_tool_modes_preserve_native_objects(mode: str) -> None:
    payload = _payload()
    payload.pop("response_format")
    payload["tools"] = [{"type": "function", "function": {"name": "get_nodes"}}]
    payload["tool_choice"] = (
        {"type": "function", "function": {"name": "get_nodes"}}
        if mode == "named"
        else mode
    )
    wire = adapter.adapt_chat_request(payload)
    assert wire["tools"] == payload["tools"]
    assert wire["tool_choice"] == payload["tool_choice"]


def test_declared_capabilities_reject_without_downgrading(monkeypatch, tmp_path):
    config = _config()
    config["models"][GPT].update(
        response_formats=[], native_tool_calls=False, tool_choice_modes=[]
    )
    _config_file(monkeypatch, tmp_path, config)
    payload = _payload()
    with pytest.raises(adapter.ModelAdapterError, match="response_format"):
        adapter.adapt_chat_request(payload)
    payload.pop("response_format")
    assert "tools" not in adapter.adapt_chat_request(payload)
    payload["tools"] = [{"type": "function", "function": {"name": "get_nodes"}}]
    payload["tool_choice"] = "auto"
    with pytest.raises(adapter.ModelAdapterError, match="native tool calls"):
        adapter.adapt_chat_request(payload)


def test_disallowed_tool_mode_is_rejected(monkeypatch, tmp_path):
    config = _config()
    config["models"][GPT]["tool_choice_modes"] = ["auto"]
    _config_file(monkeypatch, tmp_path, config)
    payload = _payload()
    payload.pop("response_format")
    payload.update(
        tools=[{"type": "function", "function": {"name": "get_nodes"}}],
        tool_choice="required",
    )
    with pytest.raises(adapter.ModelAdapterError, match="tool_choice"):
        adapter.adapt_chat_request(payload)
