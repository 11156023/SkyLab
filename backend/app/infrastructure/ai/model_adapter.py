"""Small, model-wide request contracts for the direct-vLLM System AI client."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_FILE = Path(__file__).resolve().parents[3] / "config" / "llm-model-profiles.yaml"
logger = logging.getLogger(__name__)

ResponseFormat = Literal["json_object", "json_schema"]
ToolChoiceMode = Literal["auto", "none", "required", "named"]
ModelName = Annotated[str, Field(min_length=1)]
_LEGACY_THINKING = frozenset({"none", "off", "low", "medium", "high"})


class ModelAdapterError(ValueError):
    """Invalid configuration or an unsupported model request contract."""


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    thinking: Literal["none", "low"]
    thinking_parameter: Literal["reasoning_effort", "enable_thinking"]
    response_formats: tuple[ResponseFormat, ...]
    native_tool_calls: bool
    tool_choice_modes: tuple[ToolChoiceMode, ...]

    @field_validator("response_formats", "tool_choice_modes", mode="before")
    @classmethod
    def freeze_lists(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_contract(self) -> ModelProfile:
        if self.thinking_parameter == "reasoning_effort" and self.thinking != "low":
            raise ValueError("reasoning_effort requires thinking='low'")
        if not self.native_tool_calls and self.tool_choice_modes:
            raise ValueError("tool_choice_modes require native_tool_calls")
        if len(set(self.response_formats)) != len(self.response_formats):
            raise ValueError("duplicate response_formats")
        if len(set(self.tool_choice_modes)) != len(self.tool_choice_modes):
            raise ValueError("duplicate tool_choice_modes")
        return self


class ModelProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: int
    models: dict[ModelName, ModelProfile] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("unsupported schema_version")
        return value

    @field_validator("models")
    @classmethod
    def validate_names(cls, value: dict[str, ModelProfile]) -> dict[str, ModelProfile]:
        if any(name != name.strip() for name in value):
            raise ValueError("model keys must not have surrounding whitespace")
        return value


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc]
    """SafeLoader scoped to this file; never silently replace a duplicate key."""


def _unique_mapping(loader: Any, node: Any) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            if key in result:
                raise ModelAdapterError(f"Duplicate YAML key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=True)
        except TypeError as exc:
            raise ModelAdapterError("YAML mapping keys must be scalar values") from exc
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


@lru_cache(maxsize=1)
def load_model_profiles() -> ModelProfiles:
    """Read the fixed backend config once, independent of the working directory."""
    try:
        raw = yaml.load(
            CONFIG_FILE.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
        )
        return ModelProfiles.model_validate(raw)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ModelAdapterError(f"Invalid {CONFIG_FILE}: {exc}") from exc


def adapt_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy and adapt only model-owned fields; leave budgets and business data alone."""
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ModelAdapterError("A non-empty model is required")
    profile = load_model_profiles().models.get(model)
    if profile is None:
        raise ModelAdapterError(
            f"No model contract for {model!r} in {CONFIG_FILE.name}"
        )

    template = payload.get("chat_template_kwargs")
    if template is not None and not isinstance(template, dict):
        raise ModelAdapterError("chat_template_kwargs must be an object")
    template_kwargs = dict(template or {})
    if (
        "enable_thinking" in template_kwargs
        and type(template_kwargs["enable_thinking"]) is not bool
    ):
        raise ModelAdapterError("Legacy enable_thinking must be a boolean")
    for source in (payload, template_kwargs):
        if "reasoning_effort" in source:
            effort = source["reasoning_effort"]
            if not isinstance(effort, str) or effort not in _LEGACY_THINKING:
                raise ModelAdapterError("Invalid legacy reasoning_effort")

    response_format = payload.get("response_format")
    if response_format is not None:
        if (
            not isinstance(response_format, dict)
            or response_format.get("type") not in profile.response_formats
        ):
            raise ModelAdapterError(
                f"Model {model!r} does not support this response_format"
            )
    if payload.get("tools"):
        if not profile.native_tool_calls:
            raise ModelAdapterError(
                f"Model {model!r} does not support native tool calls"
            )
        choice = payload.get("tool_choice")
        mode = "named" if isinstance(choice, dict) else choice
        if mode not in profile.tool_choice_modes:
            raise ModelAdapterError(
                f"Model {model!r} does not support this tool_choice"
            )

    wire = dict(payload)
    wire.pop("reasoning_effort", None)
    template_kwargs.pop("reasoning_effort", None)
    template_kwargs.pop("enable_thinking", None)
    if profile.thinking_parameter == "reasoning_effort":
        wire["reasoning_effort"] = profile.thinking
        if template_kwargs:
            wire["chat_template_kwargs"] = template_kwargs
        else:
            wire.pop("chat_template_kwargs", None)
    else:
        enabled = profile.thinking == "low"
        wire["chat_template_kwargs"] = {**template_kwargs, "enable_thinking": enabled}
    logger.debug(
        "System AI model contract: model=%s thinking=%s parameter=%s",
        model,
        profile.thinking,
        profile.thinking_parameter,
    )
    return wire
