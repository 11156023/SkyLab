from __future__ import annotations

import copy
import json

import httpx
import pytest

from app.infrastructure.ai.vllm_client import (
    VLLMCapability,
    VLLMClient,
    VLLMProfileError,
    VLLMRequestProfile,
    close_all_vllm_clients,
    required_capabilities,
)


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


def _text_payload(*, max_tokens: int = 32) -> dict:
    return {
        "model": "test",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }


@pytest.mark.asyncio
async def test_vllm_client_reuses_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient(
        base_url="http://vllm.example/v1",
        api_key="secret",
        default_timeout=10.0,
    )

    await client.create_chat_completion(
        _text_payload(), profile=VLLMRequestProfile.CONFIGURED_TEXT
    )
    await client.create_chat_completion(
        _text_payload(), profile=VLLMRequestProfile.CONFIGURED_TEXT
    )

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
        _text_payload(),
        profile=VLLMRequestProfile.CONFIGURED_TEXT,
        request_id="campus-request-123",
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

    await client.create_chat_completion(
        _text_payload(), profile=VLLMRequestProfile.CONFIGURED_TEXT
    )
    await client.aclose()
    await client.create_chat_completion(
        _text_payload(), profile=VLLMRequestProfile.CONFIGURED_TEXT
    )

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
        "tool_choice": "auto",
        "max_tokens": 4096,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    original = copy.deepcopy(payload)
    result = await client.create_chat_completion(
        payload,
        profile=VLLMRequestProfile.COMPLEX_AGENT,
        request_id="context-1",
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert len(requests) == 3
    retry = json.loads(requests[2].content)
    assert retry == {**original, "max_tokens": 3968}
    assert 92000 + retry["max_tokens"] < 96000
    assert json.loads(requests[1].content) == {
        "model": original["model"],
        "messages": original["messages"],
        "tools": original["tools"],
        "chat_template_kwargs": original["chat_template_kwargs"],
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
        await client.create_chat_completion(
            _text_payload(max_tokens=4096),
            profile=VLLMRequestProfile.CONFIGURED_TEXT,
        )
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
        await client.create_chat_completion(
            _text_payload(max_tokens=4096),
            profile=VLLMRequestProfile.CONFIGURED_TEXT,
        )
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
        await client.create_chat_completion(
            _text_payload(max_tokens=4096),
            profile=VLLMRequestProfile.CONFIGURED_TEXT,
        )
    assert caught.value.response.json() == _context_error()
    assert len(requests) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        {"chat_template_kwargs": {}},
        {"stream": True},
        {"max_tokens": 0},
    ],
)
async def test_profile_rejects_invalid_request_before_transport(
    monkeypatch: pytest.MonkeyPatch, mutation: dict
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient("http://vllm.example/v1", "secret")
    payload = {**_text_payload(), **mutation}

    with pytest.raises(VLLMProfileError):
        await client.create_chat_completion(
            payload, profile=VLLMRequestProfile.CONFIGURED_TEXT
        )

    assert not _FakeAsyncClient.instances


async def test_json_schema_profile_rejects_wrong_request_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient("http://vllm.example/v1", "secret")

    with pytest.raises(VLLMProfileError, match="named JSON Schema"):
        await client.create_chat_completion(
            _text_payload(), profile=VLLMRequestProfile.NAVIGATION_DECISION
        )

    assert not _FakeAsyncClient.instances


async def test_fixed_profile_rejects_excess_output_budget_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    client = VLLMClient("http://vllm.example/v1", "secret")
    payload = {
        **_text_payload(max_tokens=385),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "navigation-v1", "schema": {"type": "object"}},
        },
    }

    with pytest.raises(VLLMProfileError, match="at most 384"):
        await client.create_chat_completion(
            payload, profile=VLLMRequestProfile.NAVIGATION_DECISION
        )

    assert not _FakeAsyncClient.instances


async def test_structured_profile_rejects_non_json_response() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "not-json"}}]},
        )

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    payload = {
        **_text_payload(),
        "response_format": {"type": "json_object"},
    }

    with pytest.raises(VLLMProfileError, match="invalid JSON content"):
        await client.create_chat_completion(
            payload, profile=VLLMRequestProfile.STRUCTURED_OBJECT
        )


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {
            "choices": [
                {"finish_reason": "length", "message": {"content": "partial"}}
            ]
        },
        {"choices": [{"message": {"content": ""}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "get_nodes", "arguments": "[1]"},
                            }
                        ],
                    }
                }
            ]
        },
    ],
)
async def test_profile_rejects_invalid_or_truncated_response(body: dict) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    with pytest.raises(VLLMProfileError):
        await client.create_chat_completion(
            _text_payload(), profile=VLLMRequestProfile.CONFIGURED_TEXT
        )


async def test_complex_agent_accepts_native_tool_call() -> None:
    body = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {"name": "get_nodes", "arguments": "{}"},
                        }
                    ],
                },
            }
        ]
    }

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    client = VLLMClient("http://vllm/v1", "secret")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    payload = {
        **_text_payload(),
        "tools": [{"type": "function", "function": {"name": "get_nodes"}}],
        "tool_choice": "auto",
    }

    result = await client.create_chat_completion(
        payload, profile=VLLMRequestProfile.COMPLEX_AGENT
    )

    assert result == body
    assert required_capabilities(VLLMRequestProfile.COMPLEX_AGENT) == frozenset(
        {VLLMCapability.THINKING_CONTROL, VLLMCapability.NATIVE_TOOL_CALLS}
    )
