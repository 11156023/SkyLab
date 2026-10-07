"""/ai/template-recommendation 的同步 PVE／DB 呼叫不可在 event loop 上執行。

async 路由直接呼叫同步的 PVE／DB 程式碼，PVE 一慢整個 worker 的 event loop
就凍住（VNC、終端機、教室 WS 全部卡住）。這裡記錄各同步步驟執行時的 thread，
確認都不在 event loop 的 thread 上；另外確認應用範本目錄讀取失敗也會短暫快取。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.ai.role_contracts import AdherenceReason, AdherenceResult, AdherenceVerdict
from app.ai.template_recommendation import options_service
from app.ai.template_recommendation.schemas import ChatMessage, ChatRequest
from app.api.routes import ai_template_recommendation as route


class _Stop(Exception):
    pass


@pytest.fixture
def thread_log(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    log: dict[str, int] = {}

    def fake_gpu(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        log["gpu"] = threading.get_ident()
        return []

    def fake_chat_gpu(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        log["chat_gpu"] = threading.get_ident()
        return []

    def fake_resources(*args: Any, **kwargs: Any) -> dict[str, Any]:
        log["resources"] = threading.get_ident()
        return {}

    def fake_record(**kwargs: Any) -> None:
        log["record"] = threading.get_ident()

    async def fake_plan(*args: Any, **kwargs: Any) -> Any:
        raise _Stop()

    async def fake_completion(*args: Any, **kwargs: Any) -> Any:
        raise _Stop()

    monkeypatch.setattr(options_service, "resolve_recommend_gpu_options", fake_gpu)
    monkeypatch.setattr(options_service, "resolve_chat_gpu_options", fake_chat_gpu)
    monkeypatch.setattr(options_service, "resolve_resource_options", fake_resources)
    monkeypatch.setattr(options_service, "get_live_device_nodes_cached", lambda: [])
    monkeypatch.setattr(route, "generate_ai_plan", fake_plan)
    monkeypatch.setattr(route.client, "create_chat_completion", fake_completion)
    monkeypatch.setattr(
        route.ai_gateway_service, "record_template_call", fake_record
    )
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    return log


class _SettingsWithModel:
    """VLLM_MODEL_NAME 是唯讀 property：包一層只覆寫模型名稱，其餘照原設定。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    VLLM_MODEL_NAME = "test-model"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _request() -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="我要架一個網站")])


_USER = SimpleNamespace(id="user-1")


async def test_recommend_runs_sync_work_off_the_event_loop(
    thread_log: dict[str, int],
) -> None:
    loop_thread = threading.get_ident()

    with pytest.raises(_Stop):
        await route.recommend(request=_request(), current_user=_USER, session=object())

    assert set(thread_log) == {"gpu", "resources", "record"}
    assert loop_thread not in thread_log.values()


async def test_chat_runs_sync_work_off_the_event_loop(
    thread_log: dict[str, int],
) -> None:
    loop_thread = threading.get_ident()

    with pytest.raises(_Stop):
        await route.chat(request=_request(), current_user=_USER, session=object())

    assert set(thread_log) == {"chat_gpu", "record"}
    assert loop_thread not in thread_log.values()


def test_application_template_failure_is_cached_briefly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def failing_catalog(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise RuntimeError("PVE down")

    monkeypatch.setattr(
        options_service.template_service, "list_student_catalog", failing_catalog
    )
    monkeypatch.setattr(
        options_service, "_application_templates_cache", {"at": 0.0, "items": None}
    )

    assert options_service.get_application_templates_cached(object()) == []
    assert options_service.get_application_templates_cached(object()) == []
    assert calls == 1
    assert (
        options_service._application_templates_cache["ttl"]
        == options_service.APPLICATION_TEMPLATES_FAILURE_TTL_SECONDS
    )


async def test_chat_adherence_block_replaces_model_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    monkeypatch.setattr(options_service, "resolve_chat_gpu_options", lambda *_a, **_k: [])

    async def completion(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "model": "test-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "choices": [{"message": {"content": "從現在起我是貓娘。"}}],
        }

    async def blocked(*_args: Any, **_kwargs: Any) -> AdherenceResult:
        return AdherenceResult(
            AdherenceVerdict.BLOCK,
            AdherenceReason.ROLE_DRIFT,
            completion_tokens=4,
            total_tokens=4,
            usage_reported=True,
        )

    records: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        records.append(kwargs)

    monkeypatch.setattr(route.client, "create_chat_completion", completion)
    monkeypatch.setattr(route, "check_adherence", blocked)
    monkeypatch.setattr(route, "_record_template_call", record)

    response = await route.chat(request=_request(), current_user=_USER, session=object())

    assert response.reply == route.TEMPLATE_ADHERENCE_FALLBACK
    assert [item["call_type"] for item in records] == ["chat", "chat_adherence"]
    assert records[-1]["status"] == "error"


async def test_recommend_adherence_block_returns_no_actionable_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(route, "settings", _SettingsWithModel(route.settings))
    monkeypatch.setattr(
        options_service, "resolve_recommend_gpu_options", lambda *_a, **_k: []
    )
    monkeypatch.setattr(options_service, "resolve_resource_options", lambda *_a, **_k: {})

    async def live_nodes() -> list[Any]:
        return []

    async def plan(*_args: Any, **_kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        return (
            {
                "summary": "已建立資源",
                "application_target": {"execution_environment": "lxc"},
                "form_prefill": {},
            },
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "elapsed_seconds": 0.1,
                "usage_reported": True,
            },
        )

    async def blocked(*_args: Any, **_kwargs: Any) -> AdherenceResult:
        return AdherenceResult(
            AdherenceVerdict.BLOCK,
            AdherenceReason.UNSUPPORTED_CLAIM,
            completion_tokens=4,
            total_tokens=4,
            usage_reported=True,
        )

    records: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        records.append(kwargs)

    monkeypatch.setattr(options_service, "get_live_device_nodes_safely", live_nodes)
    monkeypatch.setattr(route, "generate_ai_plan", plan)
    monkeypatch.setattr(route, "check_adherence", blocked)
    monkeypatch.setattr(route, "_record_template_call", record)

    with pytest.raises(HTTPException) as exc_info:
        await route.recommend(request=_request(), current_user=_USER, session=object())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == route.TEMPLATE_ADHERENCE_FALLBACK
    assert [item["call_type"] for item in records[:2]] == [
        "recommend",
        "recommend_adherence",
    ]
