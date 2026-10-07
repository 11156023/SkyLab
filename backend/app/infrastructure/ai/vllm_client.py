from __future__ import annotations

import logging
import weakref
from typing import Any, cast

import httpx

_CLIENTS: weakref.WeakSet[VLLMClient] = weakref.WeakSet()
logger = logging.getLogger(__name__)


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
        self._api_key = api_key
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
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
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
        return cast(dict[str, Any], response.json())

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None


async def close_all_vllm_clients() -> None:
    for client in list(_CLIENTS):
        await client.aclose()
