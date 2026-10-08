from __future__ import annotations

import json
import logging
import weakref
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

import httpx

_CLIENTS: weakref.WeakSet[VLLMClient] = weakref.WeakSet()
logger = logging.getLogger(__name__)


class VLLMCapability(str, Enum):
    """Transport-visible capabilities required by a System AI request."""

    THINKING_CONTROL = "thinking_control"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"
    NATIVE_TOOL_CALLS = "native_tool_calls"


class VLLMRequestProfile(str, Enum):
    """Small shared profiles for the direct-vLLM System AI boundary."""

    NAVIGATION_DECISION = "navigation_decision"
    BOUNDED_EXPLANATION = "bounded_explanation"
    CONFIGURED_TEXT = "configured_text"
    STRUCTURED_OBJECT = "structured_object"
    COMPLEX_AGENT = "complex_agent"
    ADHERENCE_CHECK = "adherence_check"


class VLLMProfileError(ValueError):
    """Raised when a request or response violates its declared profile."""


@dataclass(frozen=True)
class _ProfilePolicy:
    thinking_enabled: bool | None
    max_tokens: int | None
    capabilities: frozenset[VLLMCapability]


_PROFILE_POLICIES: Final[dict[VLLMRequestProfile, _ProfilePolicy]] = {
    VLLMRequestProfile.NAVIGATION_DECISION: _ProfilePolicy(
        thinking_enabled=False,
        max_tokens=384,
        capabilities=frozenset(
            {VLLMCapability.THINKING_CONTROL, VLLMCapability.JSON_SCHEMA}
        ),
    ),
    VLLMRequestProfile.BOUNDED_EXPLANATION: _ProfilePolicy(
        thinking_enabled=False,
        max_tokens=384,
        capabilities=frozenset({VLLMCapability.THINKING_CONTROL}),
    ),
    VLLMRequestProfile.CONFIGURED_TEXT: _ProfilePolicy(
        thinking_enabled=None,
        max_tokens=None,
        capabilities=frozenset({VLLMCapability.THINKING_CONTROL}),
    ),
    VLLMRequestProfile.STRUCTURED_OBJECT: _ProfilePolicy(
        thinking_enabled=None,
        max_tokens=None,
        capabilities=frozenset(
            {VLLMCapability.THINKING_CONTROL, VLLMCapability.JSON_OBJECT}
        ),
    ),
    VLLMRequestProfile.COMPLEX_AGENT: _ProfilePolicy(
        thinking_enabled=None,
        max_tokens=None,
        capabilities=frozenset(
            {VLLMCapability.THINKING_CONTROL, VLLMCapability.NATIVE_TOOL_CALLS}
        ),
    ),
    VLLMRequestProfile.ADHERENCE_CHECK: _ProfilePolicy(
        thinking_enabled=False,
        max_tokens=128,
        capabilities=frozenset(
            {VLLMCapability.THINKING_CONTROL, VLLMCapability.JSON_SCHEMA}
        ),
    ),
}


def required_capabilities(
    profile: VLLMRequestProfile,
) -> frozenset[VLLMCapability]:
    """Expose the capability contract for diagnostics and live probes."""
    try:
        return _PROFILE_POLICIES[profile].capabilities
    except KeyError as exc:  # pragma: no cover - enum exhaustiveness guard
        raise VLLMProfileError(f"Unknown vLLM request profile: {profile!r}") from exc


def _profile_policy(profile: VLLMRequestProfile) -> _ProfilePolicy:
    if not isinstance(profile, VLLMRequestProfile):
        raise VLLMProfileError("A valid VLLMRequestProfile is required")
    return _PROFILE_POLICIES[profile]


def _validate_response_format(
    payload: dict[str, Any], profile: VLLMRequestProfile, policy: _ProfilePolicy
) -> None:
    response_format = payload.get("response_format")
    if VLLMCapability.JSON_OBJECT in policy.capabilities:
        if (
            not isinstance(response_format, dict)
            or response_format.get("type") != "json_object"
        ):
            raise VLLMProfileError(
                f"Profile {profile.value} requires response_format=json_object"
            )
        return
    if VLLMCapability.JSON_SCHEMA in policy.capabilities:
        schema_envelope = (
            response_format.get("json_schema")
            if isinstance(response_format, dict)
            and response_format.get("type") == "json_schema"
            else None
        )
        if (
            not isinstance(schema_envelope, dict)
            or not isinstance(schema_envelope.get("name"), str)
            or not schema_envelope["name"].strip()
            or not isinstance(schema_envelope.get("schema"), dict)
        ):
            raise VLLMProfileError(
                f"Profile {profile.value} requires a named JSON Schema"
            )
        return
    if response_format is not None:
        raise VLLMProfileError(
            f"Profile {profile.value} does not allow response_format"
        )


def _validate_request(payload: dict[str, Any], profile: VLLMRequestProfile) -> None:
    policy = _profile_policy(profile)
    if "extra_body" in payload:
        raise VLLMProfileError(
            "extra_body is an SDK wrapper and must not be sent to direct vLLM"
        )
    if not isinstance(payload.get("model"), str) or not payload["model"].strip():
        raise VLLMProfileError(f"Profile {profile.value} requires a model")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise VLLMProfileError(f"Profile {profile.value} requires messages")
    max_tokens = payload.get("max_tokens")
    if type(max_tokens) is not int or max_tokens <= 0:
        raise VLLMProfileError(
            f"Profile {profile.value} requires a positive integer max_tokens"
        )
    if policy.max_tokens is not None and max_tokens > policy.max_tokens:
        raise VLLMProfileError(
            f"Profile {profile.value} allows at most {policy.max_tokens} output tokens"
        )
    if payload.get("stream", False) is not False:
        raise VLLMProfileError("System AI VLLMClient only supports non-stream responses")

    if VLLMCapability.THINKING_CONTROL in policy.capabilities:
        template_kwargs = payload.get("chat_template_kwargs")
        enable_thinking = (
            template_kwargs.get("enable_thinking")
            if isinstance(template_kwargs, dict)
            else None
        )
        if type(enable_thinking) is not bool:
            raise VLLMProfileError(
                f"Profile {profile.value} requires explicit thinking control"
            )
        if (
            policy.thinking_enabled is not None
            and enable_thinking is not policy.thinking_enabled
        ):
            raise VLLMProfileError(
                f"Profile {profile.value} requires enable_thinking="
                f"{str(policy.thinking_enabled).lower()}"
            )

    tools = payload.get("tools")
    if VLLMCapability.NATIVE_TOOL_CALLS not in policy.capabilities:
        if tools or payload.get("tool_choice") is not None:
            raise VLLMProfileError(f"Profile {profile.value} does not allow tools")
    elif tools is not None:
        if not isinstance(tools, list) or not tools:
            raise VLLMProfileError(
                f"Profile {profile.value} requires a non-empty tools list"
            )
        for tool in tools:
            function = tool.get("function") if isinstance(tool, dict) else None
            if (
                not isinstance(tool, dict)
                or tool.get("type") != "function"
                or not isinstance(function, dict)
                or not isinstance(function.get("name"), str)
                or not function["name"].strip()
            ):
                raise VLLMProfileError(
                    f"Profile {profile.value} received an invalid tool definition"
                )
        tool_choice = payload.get("tool_choice")
        if tool_choice is None:
            raise VLLMProfileError(
                f"Profile {profile.value} requires tool_choice when tools are present"
            )
        if isinstance(tool_choice, str):
            valid_tool_choice = tool_choice in {"auto", "none", "required"}
        else:
            selected = (
                tool_choice.get("function")
                if isinstance(tool_choice, dict)
                and tool_choice.get("type") == "function"
                else None
            )
            valid_tool_choice = (
                isinstance(selected, dict)
                and isinstance(selected.get("name"), str)
                and bool(selected["name"].strip())
            )
        if not valid_tool_choice:
            raise VLLMProfileError(
                f"Profile {profile.value} received an invalid tool_choice"
            )
        if payload.get("response_format") is not None:
            raise VLLMProfileError(
                f"Profile {profile.value} cannot combine tools with response_format"
            )
    _validate_response_format(payload, profile, policy)


def validate_request_profile(
    payload: dict[str, Any], profile: VLLMRequestProfile
) -> None:
    """Validate a direct-vLLM payload without sending model traffic."""
    _validate_request(payload, profile)


def _validate_tool_calls(tool_calls: Any, profile: VLLMRequestProfile) -> bool:
    if not tool_calls:
        return False
    if not isinstance(tool_calls, list):
        raise VLLMProfileError(
            f"Profile {profile.value} received an invalid tool_calls envelope"
        )
    for tool_call in tool_calls:
        function = tool_call.get("function") if isinstance(tool_call, dict) else None
        if (
            not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not function["name"].strip()
        ):
            raise VLLMProfileError(
                f"Profile {profile.value} received an invalid native tool call"
            )
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                raise VLLMProfileError(
                    f"Profile {profile.value} received invalid tool arguments"
                ) from exc
        if not isinstance(arguments, dict):
            raise VLLMProfileError(
                f"Profile {profile.value} requires object tool arguments"
            )
    return True


def _validate_response(
    response: Any, profile: VLLMRequestProfile
) -> dict[str, Any]:
    policy = _profile_policy(profile)
    if not isinstance(response, dict):
        raise VLLMProfileError(
            f"Profile {profile.value} received a non-object response"
        )
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise VLLMProfileError(
            f"Profile {profile.value} requires exactly one response choice"
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise VLLMProfileError(
            f"Profile {profile.value} received an invalid response choice"
        )
    if choice.get("finish_reason") == "length":
        raise VLLMProfileError(f"Profile {profile.value} response was truncated")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise VLLMProfileError(
            f"Profile {profile.value} received an invalid assistant message"
        )
    has_tool_calls = _validate_tool_calls(message.get("tool_calls"), profile)
    if (
        has_tool_calls
        and VLLMCapability.NATIVE_TOOL_CALLS not in policy.capabilities
    ):
        raise VLLMProfileError(
            f"Profile {profile.value} received forbidden tool calls"
        )
    content = message.get("content")
    if has_tool_calls:
        return response
    if not isinstance(content, str) or not content.strip():
        raise VLLMProfileError(
            f"Profile {profile.value} requires non-empty assistant content"
        )
    if policy.capabilities.intersection(
        {VLLMCapability.JSON_OBJECT, VLLMCapability.JSON_SCHEMA}
    ):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise VLLMProfileError(
                f"Profile {profile.value} received invalid JSON content"
            ) from exc
        if not isinstance(parsed, dict):
            raise VLLMProfileError(
                f"Profile {profile.value} requires a JSON object response"
            )
    return response


def _is_context_overflow(response: httpx.Response, requested: Any) -> bool:
    """vLLM's 'at least' input count is a lower bound, not a usable budget."""
    if response.status_code != 400 or type(requested) is not int:
        return False
    try:
        error = response.json().get("error", {})
        return error.get("param") == "input_tokens" and (
            "maximum context length" in str(error.get("message") or "")
        )
    except (AttributeError, TypeError, ValueError):
        return False


class VLLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_timeout: float = 30.0,
        limits: httpx.Limits | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._default_timeout = default_timeout
        self._limits = limits or httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
        )
        self._http_client: httpx.AsyncClient | None = None
        _CLIENTS.add(self)

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._default_timeout),
                limits=self._limits,
            )
        return self._http_client

    async def create_chat_completion(
        self,
        payload: dict[str, Any],
        *,
        profile: VLLMRequestProfile,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        validate_request_profile(payload, profile)
        headers = {"Content-Type": "application/json"}
        # 沒設 --api-key 的 vLLM 不需要認證；VLLM_API_KEY 留空時送 "Bearer " 會被
        # h11 判為非法標頭，請求根本發不出去
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if request_id:
            headers["X-Request-ID"] = request_id[:255]
        effective_timeout = timeout if timeout is not None else self._default_timeout
        http_client = await self._get_http_client()
        response = await http_client.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=effective_timeout,
        )
        output_limit = None
        if _is_context_overflow(response, payload.get("max_tokens")):
            # Count the full prompt with the serving model's chat template/tools.
            # The early-abort count in a 400 changes with max_tokens and cannot
            # establish how much room the unchanged prompt actually leaves.
            tokenize_payload = {
                key: payload[key]
                for key in (
                    "model",
                    "messages",
                    "tools",
                    "chat_template",
                    "chat_template_kwargs",
                    "add_generation_prompt",
                    "continue_final_message",
                    "add_special_tokens",
                )
                if key in payload
            }
            try:
                token_response = await http_client.post(
                    f"{self._base_url.removesuffix('/v1')}/tokenize",
                    json=tokenize_payload,
                    headers=headers,
                    timeout=effective_timeout,
                )
                token_response.raise_for_status()
                token_data = token_response.json()
                prompt_tokens = token_data.get("count")
                context_tokens = token_data.get("max_model_len")
                if type(prompt_tokens) is int and type(context_tokens) is int:
                    available = context_tokens - prompt_tokens - 32
                    if prompt_tokens >= 0 and 128 <= available < payload["max_tokens"]:
                        output_limit = available
            except (httpx.HTTPError, AttributeError, TypeError, ValueError) as exc:
                logger.warning(
                    "vLLM context token count failed: request_id=%s error=%s",
                    request_id,
                    exc,
                )
        if output_limit is not None:
            logger.warning(
                "vLLM context overflow: request_id=%s max_tokens=%s adjusted_max_tokens=%s",
                request_id,
                payload["max_tokens"],
                output_limit,
            )
            # A rejected generation executed no tools. Retry the exact same prompt
            # once with a smaller output reservation; never trim safety/history.
            response = await http_client.post(
                f"{self._base_url}/chat/completions",
                json={**payload, "max_tokens": output_limit},
                headers=headers,
                timeout=effective_timeout,
            )
        response.raise_for_status()
        return _validate_response(response.json(), profile)

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None


async def close_all_vllm_clients() -> None:
    for client in list(_CLIENTS):
        await client.aclose()
