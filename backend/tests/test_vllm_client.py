from __future__ import annotations

import httpx
import pytest

from app.infrastructure.ai.vllm_client import VLLMClient, close_all_vllm_clients


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "ok"}}]}


class _FakeAsyncClient:
    instances: list[_FakeAsyncClient] = []

    def __init__(self, *args, **kwargs) -> None:
        self.is_closed = False
        self.posts: list[dict] = []
        self.__class__.instances.append(self)

    async def post(self, url: str, **kwargs) -> _FakeResponse:
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse()

    async def aclose(self) -> None:
        self.is_closed = True


@pytest.fixture(autouse=True)
async def _close_clients_after_test():
    yield
    await close_all_vllm_clients()
    _FakeAsyncClient.instances.clear()


@pytest.mark.asyncio
async def test_vllm_client_reuses_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient(
        base_url="http://vllm.example/v1",
        api_key="secret",
        default_timeout=10.0,
    )

    await client.create_chat_completion({"model": "test"})
    await client.create_chat_completion({"model": "test"})

    assert len(_FakeAsyncClient.instances) == 1
    assert len(_FakeAsyncClient.instances[0].posts) == 2


@pytest.mark.asyncio
async def test_vllm_client_forwards_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient(
        base_url="http://vllm.example/v1",
        api_key="secret",
        default_timeout=10.0,
    )

    await client.create_chat_completion(
        {"model": "test"}, request_id="campus-request-123"
    )

    request = _FakeAsyncClient.instances[0].posts[0]
    assert request["headers"]["X-Request-ID"] == "campus-request-123"


@pytest.mark.asyncio
async def test_vllm_client_recreates_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient(
        base_url="http://vllm.example/v1",
        api_key="secret",
        default_timeout=10.0,
    )

    await client.create_chat_completion({"model": "test"})
    await client.aclose()
    await client.create_chat_completion({"model": "test"})

    assert len(_FakeAsyncClient.instances) == 2
    assert _FakeAsyncClient.instances[0].is_closed is True
