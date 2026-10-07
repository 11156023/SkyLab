from __future__ import annotations

import copy
import json

import httpx
import pytest

from app.infrastructure.ai.vllm_client import VLLMClient, close_all_vllm_clients


class _FakeResponse:
    status_code = 200

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
async def test_vllm_client_recreates_after_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def _context_error(input_tokens: int = 91905) -> dict:
    return {
        "error": {
            "message": (
                "This model's maximum context length is 96000 tokens. "
                "However, you requested 4096 output tokens and your prompt contains "
                f"at least {input_tokens} input tokens, for a total of at least "
                f"{input_tokens + 4096} tokens. Please reduce the length of the input "
                "prompt or the number of requested output tokens."
            ),
            "param": "input_tokens",
        }
    }


async def test_context_overflow_retries_same_prompt_with_output_room() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/tokenize":
            assert "max_tokens" not in json.loads(request.content)
            return httpx.Response(200, json={"count": 92000, "max_model_len": 96000})
        if len(requests) == 1:
            return httpx.Response(400, json=_context_error())
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    payload = {
        "model": "test",
        "messages": [{"role": "system", "content": "safety"}],
        "tools": [{"type": "function", "function": {"name": "get_nodes"}}],
        "max_tokens": 4096,
    }
    original = copy.deepcopy(payload)
    result = await client.create_chat_completion(payload, request_id="context-1")

    assert result["choices"][0]["message"]["content"] == "ok"
    assert len(requests) == 3
    retry = json.loads(requests[2].content)
    assert retry == {**original, "max_tokens": 3968}
    assert 92000 + retry["max_tokens"] < 96000
    assert json.loads(requests[1].content) == {
        "model": original["model"],
        "messages": original["messages"],
        "tools": original["tools"],
    }
    assert payload == original
    assert all(request.headers["X-Request-ID"] == "context-1" for request in requests)


@pytest.mark.parametrize(
    "error",
    [
        _context_error(96000),
        _context_error(95900),
        {"error": {"param": "tools", "message": "invalid tools"}},
        {"error": {"param": "input_tokens", "message": "unknown format"}},
        {"error": "invalid error envelope"},
    ],
)
async def test_other_errors_or_no_output_room_do_not_retry(error: dict) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 96000, "max_model_len": 96000})
        return httpx.Response(400, json=error)

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_chat_completion({"max_tokens": 4096})
    assert len([r for r in requests if r.url.path == "/v1/chat/completions"]) == 1
    assert len(requests) <= 2


async def test_context_overflow_retry_is_bounded_to_once() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 92000, "max_model_len": 96000})
        return httpx.Response(400, json=_context_error())

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(httpx.HTTPStatusError):
        await client.create_chat_completion({"max_tokens": 4096})
    assert len(requests) == 3


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"detail": "tokenize unavailable"}),
        (200, {"count": "92000", "max_model_len": 96000}),
        (200, {"count": 92000}),
        (200, []),
    ],
)
async def test_unavailable_or_invalid_token_count_preserves_original_error(
    status: int,
    body: dict | list,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/tokenize":
            return httpx.Response(status, json=body)
        return httpx.Response(400, json=_context_error())

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await client.create_chat_completion({"max_tokens": 4096})
    assert caught.value.response.json() == _context_error()
    assert len(requests) == 2
