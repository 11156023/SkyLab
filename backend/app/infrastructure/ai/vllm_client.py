from __future__ import annotations

import weakref
from typing import Any

import httpx

_CLIENTS: weakref.WeakSet[VLLMClient] = weakref.WeakSet()


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
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None


async def close_all_vllm_clients() -> None:
    for client in list(_CLIENTS):
        await client.aclose()
