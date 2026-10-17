"""AI 路由（ai_template_recommendation、ai_proxy、ai_pve_log、ai_api、ai_contextual_help、ai_navigation）的直接呼叫測試。"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.ai.pve_log import ssh_exec as pve_ssh_exec
from app.ai.pve_log.history import PveHistoryValidationError
from app.ai.template_recommendation import options_service
from app.ai.template_recommendation.schemas import ChatMessage, ChatRequest, DeviceNode
from app.api.routes import ai_api as ai_api_route
from app.api.routes import ai_contextual_help as help_route
from app.api.routes import ai_navigation as nav_route
from app.api.routes import ai_proxy as proxy_route
from app.api.routes import ai_pve_log as pve_log_route
from app.api.routes import ai_template_recommendation as template_route

_USER = SimpleNamespace(id=uuid.uuid4(), email="user@example.com")


# ─── ai_template_recommendation ──────────────────────────────────────────────


class _SettingsWithModel:
    """VLLM_MODEL_NAME 是唯讀 property：包一層只覆寫模型名稱。"""

    def __init__(self, inner: Any, model_name: str) -> None:
        self._inner = inner
        self.VLLM_MODEL_NAME = model_name

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _chat_request() -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="我要架一個網站")])


async def test_template_chat_without_model_binding_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        template_route, "settings", _SettingsWithModel(template_route.settings, "")
    )
    with pytest.raises(HTTPException) as exc_info:
        await template_route.chat(
            request=_chat_request(), current_user=_USER, session=object()
        )
    assert exc_info.value.status_code == 503


async def test_template_chat_upstream_http_error_is_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def completion(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("vLLM unreachable")

    record = AsyncMock()
    monkeypatch.setattr(
        template_route,
        "settings",
        _SettingsWithModel(template_route.settings, "test-model"),
    )
    monkeypatch.setattr(
        options_service, "resolve_chat_gpu_options", lambda *_a, **_k: []
    )
    monkeypatch.setattr(template_route.client, "create_chat_completion", completion)
    monkeypatch.setattr(template_route, "_record_template_call", record)

    with pytest.raises(HTTPException) as exc_info:
        await template_route.chat(
            request=_chat_request(), current_user=_USER, session=object()
        )

    assert exc_info.value.status_code == 502
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    assert record.await_args.kwargs["status"] == "error"


def _patch_recommend(
    monkeypatch: pytest.MonkeyPatch, live_nodes: Any
) -> SimpleNamespace:
    resources: dict[str, Any] = {"application_templates": [], "gpu_options": []}
    captured: dict[str, Any] = {}

    async def plan(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], dict]:
        return {"summary": "建議 LXC"}, {"total_tokens": 15}

    def normalize(
        ai_result: dict[str, Any],
        merged_request: Any,
        device_nodes: list[Any],
        *,
        resource_options: dict[str, Any],
        fallback: bool,
    ) -> dict[str, Any]:
        captured["device_nodes"] = list(device_nodes)
        captured["fallback"] = fallback
        return dict(ai_result)

    def resolve_resources(*args: Any, **_kwargs: Any) -> dict[str, Any]:
        captured["resource_args"] = args
        return resources

    monkeypatch.setattr(
        template_route,
        "settings",
        _SettingsWithModel(template_route.settings, "test-model"),
    )
    monkeypatch.setattr(
        options_service, "resolve_recommend_gpu_options", lambda *_a, **_k: []
    )
    monkeypatch.setattr(options_service, "resolve_resource_options", resolve_resources)
    monkeypatch.setattr(options_service, "get_live_device_nodes_safely", live_nodes)
    monkeypatch.setattr(template_route, "generate_ai_plan", plan)
    monkeypatch.setattr(template_route, "normalize_ai_result", normalize)
    monkeypatch.setattr(template_route, "_record_template_call", AsyncMock())

    # 同一個 worker thread 第二次執行的 load_options 不會被 coverage 的 CTracer
    # 記到（離開 event loop 的行為由 test_template_recommendation_offloop 驗證），
    # 這裡改在呼叫端同步執行，確保巢狀函式本身真的跑過。
    async def inline_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(template_route.asyncio, "to_thread", inline_to_thread)
    return SimpleNamespace(captured=captured, resources=resources)


async def test_template_recommend_uses_live_device_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = DeviceNode(node="pve1", maxcpu=32)

    async def live_nodes() -> list[DeviceNode]:
        return [node]

    patched = _patch_recommend(monkeypatch, live_nodes)

    result = await template_route.recommend(
        request=_chat_request(), current_user=_USER, session=object()
    )

    assert result["summary"] == "建議 LXC"
    assert result["live_device_nodes"] == [node.model_dump()]
    assert result["resource_options"] is patched.resources
    assert result["ai_metrics"] == {"total_tokens": 15}
    assert patched.captured["device_nodes"] == [node]
    assert patched.captured["resource_args"][3] is _USER


async def test_template_recommend_falls_back_when_live_nodes_are_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()

    async def slow_live_nodes() -> list[DeviceNode]:
        await never.wait()
        return [DeviceNode(node="late")]

    patched = _patch_recommend(monkeypatch, slow_live_nodes)

    result = await template_route.recommend(
        request=_chat_request(), current_user=_USER, session=object()
    )

    assert result["live_device_nodes"] == []
    assert patched.captured["device_nodes"] == []


def test_template_usage_window_and_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = MagicMock()
    gateway.default_usage_window.return_value = ("start", "end")
    monkeypatch.setattr(template_route, "ai_gateway_service", gateway)
    session = MagicMock()

    result = template_route.get_my_template_usage(
        current_user=_USER, session=session, start_date=None, end_date=None, tz="UTC"
    )

    assert result is gateway.get_user_template_usage_stats.return_value
    gateway.get_user_template_usage_stats.assert_called_once_with(
        session=session,
        user_id=_USER.id,
        start_date="start",
        end_date="end",
        tz="UTC",
    )


# ─── ai_proxy ────────────────────────────────────────────────────────────────


def _http_request(body: bytes, content_type: str = "application/json") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/ai-proxy/chat/completions",
        "headers": [(b"content-type", content_type.encode())],
        "query_string": b"",
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return Request(scope, receive)


def _credential(rate_limit: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        rate_limit=rate_limit,
        request_id=uuid.uuid4(),
        _rate_limit_legacy_ids=(),
    )


async def test_proxy_json_payload_accepts_finite_floats() -> None:
    payload = await proxy_route._json_payload(
        _http_request(b'{"model": "m", "temperature": 0.5}')
    )
    assert payload == {"model": "m", "temperature": 0.5}


async def test_proxy_json_payload_rejects_non_object() -> None:
    response = await proxy_route._json_payload(_http_request(b"[1, 2]"))
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    assert json.loads(response.body)["error"]["code"] == "invalid_request"


async def test_proxy_relay_returns_payload_error_before_relaying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AsyncMock()
    monkeypatch.setattr(proxy_route, "_enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(proxy_route.relay_service, "relay_generation", relay)

    response = await proxy_route._relay_generation(
        endpoint="chat/completions",
        request=_http_request(b"{}", content_type="text/plain"),
        user_and_credential=(_USER, _credential()),
    )

    assert response.status_code == 415
    relay.assert_not_awaited()


async def test_proxy_relay_returns_model_error_before_relaying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AsyncMock()
    monkeypatch.setattr(proxy_route, "_enforce_rate_limit", AsyncMock())
    monkeypatch.setattr(proxy_route.relay_service, "relay_generation", relay)

    response = await proxy_route._relay_generation(
        endpoint="chat/completions",
        request=_http_request(b'{"messages": []}'),
        user_and_credential=(_USER, _credential()),
    )

    assert response.status_code == 400
    relay.assert_not_awaited()


@pytest.mark.parametrize(
    ("handler", "endpoint"),
    [("completions", "completions"), ("responses", "responses")],
)
async def test_proxy_generation_endpoints_forward(
    monkeypatch: pytest.MonkeyPatch, handler: str, endpoint: str
) -> None:
    relay = AsyncMock(return_value="relayed")
    monkeypatch.setattr(proxy_route, "_relay_generation", relay)
    request = _http_request(b"{}")
    pair = (_USER, _credential())

    result = await getattr(proxy_route, handler)(
        request=request, user_and_credential=pair
    )

    assert result == "relayed"
    relay.assert_awaited_once_with(
        endpoint=endpoint, request=request, user_and_credential=pair
    )


async def test_proxy_rate_limit_status_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_route, "get_redis", AsyncMock(return_value=None))

    status = await proxy_route.get_rate_limit_status(
        user_and_credential=(_USER, _credential(rate_limit=7))
    )

    assert status.disabled is True
    assert (status.limit_per_minute, status.remaining) == (7, 7)


async def test_proxy_rate_limit_status_when_peek_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_route, "get_redis", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        proxy_route, "peek_rate_limit_by_key", AsyncMock(return_value=None)
    )

    status = await proxy_route.get_rate_limit_status(
        user_and_credential=(_USER, _credential(rate_limit=9))
    )

    assert status.error == "rate_limit_status_unavailable"
    assert (status.current_usage, status.remaining) == (0, 9)


# ─── ai_pve_log ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(PveHistoryValidationError("bad transcript"), 422), (RuntimeError("x"), 500)],
)
async def test_pve_log_chat_maps_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, status_code: int
) -> None:
    monkeypatch.setattr(pve_log_route, "pve_chat", AsyncMock(side_effect=error))

    with pytest.raises(HTTPException) as exc_info:
        await pve_log_route.chat(
            request=SimpleNamespace(message="hi", messages=[]),
            current_user=_USER,
            session=MagicMock(),
        )

    assert exc_info.value.status_code == status_code
    if status_code == 422:
        assert exc_info.value.detail == "bad transcript"


async def test_pve_log_ssh_confirm_failure_is_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pve_ssh_exec,
        "confirm_exec",
        AsyncMock(side_effect=RuntimeError("ssh refused")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await pve_log_route.post_ssh_confirm(
            request=SimpleNamespace(), current_user=_USER, session=MagicMock()
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "ssh refused"


# ─── ai_api ──────────────────────────────────────────────────────────────────


def test_ai_api_admin_and_owner_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = MagicMock()
    monkeypatch.setattr(ai_api_route, "ai_gateway_service", gateway)
    session = MagicMock()
    request_id = uuid.uuid4()
    credential_id = uuid.uuid4()

    assert (
        ai_api_route.list_all_ai_api_requests(
            session=session, _current_user=_USER, status=None, skip=0, limit=10
        )
        is gateway.list_all_requests.return_value
    )
    assert (
        ai_api_route.get_ai_api_request(
            request_id=request_id, session=session, current_user=_USER
        )
        is gateway.get_request.return_value
    )
    assert (
        ai_api_route.delete_my_ai_api_credential(
            credential_id=credential_id, session=session, current_user=_USER
        )
        is gateway.delete_credential.return_value
    )
    gateway.delete_credential.assert_called_once_with(
        session=session, credential_id=credential_id, current_user=_USER
    )


# ─── ai_contextual_help / ai_navigation ──────────────────────────────────────


async def test_contextual_help_explain_and_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explain = AsyncMock(return_value="explained")
    monkeypatch.setattr(help_route, "explain", explain)
    surfaces = [
        SimpleNamespace(
            id="resources",
            path="/resources",
            title="資源",
            purpose="管理機器",
            when_to_use="開機",
            elements=["field"],
        ),
        SimpleNamespace(
            id="home",
            path="/",
            title="首頁",
            purpose="",
            when_to_use="",
            elements=[],
        ),
    ]
    monkeypatch.setattr(help_route, "get_surfaces_for_user", lambda user: surfaces)
    session = MagicMock()

    assert (
        await help_route.explain_screen(
            request="req", session=session, current_user=_USER
        )
        == "explained"
    )
    explain.assert_awaited_once_with("req", _USER, session=session)

    listed = help_route.list_surfaces(current_user=_USER)
    assert [(item.id, item.has_fields) for item in listed] == [
        ("resources", True),
        ("home", False),
    ]


async def test_navigation_resolve_and_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    resolve = AsyncMock(return_value="resolved")
    monkeypatch.setattr(nav_route, "resolve_navigation", resolve)
    intake = MagicMock(return_value="intake-state")
    monkeypatch.setattr(nav_route, "read_intake", intake)
    session = MagicMock()
    request = SimpleNamespace(
        query="我要開機",
        history=[],
        current_path="/",
        surface_id=None,
        screen_state=None,
        active_flow_id=None,
        pending_flow_ids=[],
    )

    assert (
        await nav_route.resolve_navigation_route(
            request=request, session=session, current_user=_USER
        )
        == "resolved"
    )
    assert resolve.await_args.args == ("我要開機", _USER)

    earlier = nav_route.NavigationMessage(role="assistant", content="需要什麼？")
    result = nav_route.navigation_intake(
        request=SimpleNamespace(
            goal="架網站", history=[earlier], facts={}, pending_key="os"
        ),
        _current_user=_USER,
    )
    assert result == "intake-state"
    history = intake.call_args.args[0]
    assert [(item.role, item.content) for item in history] == [
        ("user", "架網站"),
        ("assistant", "需要什麼？"),
    ]
    assert intake.call_args.kwargs == {"facts": {}, "pending_key": "os"}
