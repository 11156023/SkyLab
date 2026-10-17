"""app/api/routes/ai_monitoring.py 路由層補充測試（直接呼叫路由函式，service／httpx 全部替換）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.api.routes import ai_monitoring

SESSION = object()
USER = SimpleNamespace(id=uuid.uuid4())


# ─── 薄轉發端點 ───────────────────────────────────────────────────────────────


def _recorder(monkeypatch: pytest.MonkeyPatch, name: str) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fn(**kwargs: Any) -> str:
        captured.update(kwargs)
        return name

    monkeypatch.setattr(ai_monitoring.ai_gateway_service, name, _fn)
    return captured


@pytest.mark.parametrize(("source", "include"), [("all", True), ("api_key", False)])
def test_get_overview_forwards_source(
    monkeypatch: pytest.MonkeyPatch, source: str, include: bool
) -> None:
    captured = _recorder(monkeypatch, "get_monitoring_overview")
    assert (
        ai_monitoring.get_overview(
            session=SESSION,
            _current_user=USER,
            start_date=None,
            end_date=None,
            bucket="day",
            compare=False,
            source=source,
        )
        == "get_monitoring_overview"
    )
    assert captured["include_template"] is include
    assert captured["bucket"] == "day"
    assert captured["compare"] is False


def test_get_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _recorder(monkeypatch, "get_monitoring_stats")
    start = datetime(2030, 1, 1, tzinfo=UTC)
    assert (
        ai_monitoring.get_stats(
            session=SESSION, _current_user=USER, start_date=start, end_date=None
        )
        == "get_monitoring_stats"
    )
    assert captured == {"session": SESSION, "start_date": start, "end_date": None}


def test_list_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _recorder(monkeypatch, "list_proxy_calls")
    user_id = uuid.uuid4()
    assert (
        ai_monitoring.list_api_calls(
            session=SESSION,
            _current_user=USER,
            user_id=user_id,
            model_name="m",
            status="error",
            start_date=None,
            end_date=None,
            skip=5,
            limit=10,
        )
        == "list_proxy_calls"
    )
    assert captured["call_status"] == "error"
    assert captured["user_id"] == user_id
    assert (captured["skip"], captured["limit"]) == (5, 10)


def test_list_template_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _recorder(monkeypatch, "list_template_calls")
    assert (
        ai_monitoring.list_template_calls(
            session=SESSION,
            _current_user=USER,
            user_id=None,
            call_type="chat",
            preset="p",
            status="success",
            start_date=None,
            end_date=None,
            skip=0,
            limit=50,
        )
        == "list_template_calls"
    )
    assert captured["call_type"] == "chat"
    assert captured["preset"] == "p"
    assert captured["call_status"] == "success"


@pytest.mark.parametrize(("source", "include"), [("all", True), ("api_key", False)])
def test_list_users_usage(
    monkeypatch: pytest.MonkeyPatch, source: str, include: bool
) -> None:
    captured = _recorder(monkeypatch, "list_users_usage")
    assert (
        ai_monitoring.list_users_usage(
            session=SESSION,
            _current_user=USER,
            start_date=None,
            end_date=None,
            skip=0,
            limit=20,
            source=source,
        )
        == "list_users_usage"
    )
    assert captured["include_template"] is include
    assert captured["limit"] == 20


# ─── 匯出 ─────────────────────────────────────────────────────────────────────


def test_export_rejects_start_after_effective_end() -> None:
    start = datetime(2030, 1, 2, tzinfo=UTC)
    end = start - timedelta(days=1)
    with pytest.raises(HTTPException) as info:
        ai_monitoring.export_ai_usage(
            session=SESSION,
            _current_user=USER,
            start_date=start,
            end_date=end,
            source="all",
            status=None,
            model_name=None,
            call_type=None,
            user_id=None,
        )
    assert info.value.status_code == 422
    assert "start_date" in info.value.detail


# ─── LiteLLM runtime snapshot ─────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        path = url.removeprefix("http://litellm.internal")
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value


def _install(monkeypatch: pytest.MonkeyPatch, responses: dict[str, Any]) -> None:
    monkeypatch.setattr(
        ai_monitoring,
        "ai_api_settings",
        SimpleNamespace(
            litellm_runtime_api_key="obs-key",
            litellm_runtime_base_url="http://litellm.internal/",
        ),
    )
    monkeypatch.setattr(
        ai_monitoring.httpx, "AsyncClient", lambda **_kwargs: _FakeClient(responses)
    )


def _ok(payload: Any = None) -> httpx.Response:
    return httpx.Response(200, json=payload if payload is not None else {})


async def test_runtime_snapshot_request_error_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            "/health/liveliness": httpx.ConnectError("refused"),
            "/health/readiness": _ok(),
            "/health": _ok(),
            "/model/info": _ok(),
            "/v1/models": _ok(),
        },
    )
    with pytest.raises(HTTPException) as info:
        await ai_monitoring.get_litellm_runtime_snapshot(USER)
    assert info.value.status_code == 503


async def test_runtime_snapshot_tolerates_malformed_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            "/health/liveliness": _ok("alive"),
            "/health/readiness": httpx.Response(503, json={}),
            # 200 但不是 JSON → ValueError → 當成空 dict
            "/health": httpx.Response(200, content=b"not json"),
            # 探測失敗：映射表無法建立，要記 ERROR
            "/model/info": httpx.Response(500, json={}),
            "/v1/models": httpx.Response(200, content=b"<html>"),
        },
    )
    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(USER)
    assert snapshot["gateway"]["status"] == "degraded"
    assert snapshot["models"] == []
    assert snapshot["model_discovery"] == "unavailable"
    assert snapshot["healthy_deployment_count"] == 0


async def test_runtime_snapshot_non_dict_health_and_non_list_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            "/health/liveliness": httpx.Response(500, json={}),
            "/health/readiness": httpx.Response(500, json={}),
            "/health": _ok(["not", "a", "dict"]),
            "/model/info": _ok({"data": "nope"}),
            "/v1/models": _ok(["list-payload"]),
        },
    )
    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(USER)
    assert snapshot["gateway"]["status"] == "unavailable"
    assert snapshot["models"] == []


async def test_runtime_snapshot_health_lists_with_wrong_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            "/health/liveliness": _ok(),
            "/health/readiness": _ok(),
            "/health": _ok({"healthy_endpoints": "x", "unhealthy_endpoints": {"a": 1}}),
            "/model/info": _ok({"data": []}),
            "/v1/models": _ok({"data": [{"id": "only-advertised"}]}),
        },
    )
    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(USER)
    assert snapshot["healthy_deployment_count"] == 0
    assert snapshot["unhealthy_deployment_count"] == 0
    assert snapshot["models"] == [
        {
            "name": "only-advertised",
            "status": "unknown",
            "healthy_deployments": 0,
            "unhealthy_deployments": 0,
        }
    ]


async def test_runtime_snapshot_alias_mapping_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        {
            "/health/liveliness": _ok(),
            "/health/readiness": _ok(),
            "/health": _ok(
                {
                    "healthy_endpoints": [
                        # 沒有 model_id，改從 model_info.id 對應
                        {"model_info": {"id": "dep-1"}},
                        # 非 dict 項目
                        "garbage",
                        # 對不到映射，退回 model_name（乾淨的別名）
                        {"model_id": "unknown", "model_name": "fallback-alias"},
                    ],
                    "unhealthy_endpoints": [
                        {"model_id": "dep-2"},
                        # model_name 是 URL，不可顯示
                        {"model_name": "http://internal:8000"},
                        # model_info 不是 dict
                        {"model_info": "x"},
                    ],
                }
            ),
            "/model/info": _ok(
                {
                    "data": [
                        {"model_name": "shared", "model_info": {"id": "dep-1"}},
                        {"model_name": "shared", "model_info": {"id": "dep-2"}},
                        # 別名是空字串／URL／非字串 → 略過
                        {"model_name": "   ", "model_info": {"id": "dep-3"}},
                        {"model_name": "https://x", "model_info": {"id": "dep-4"}},
                        {"model_name": 123},
                        # 沒有 model_info dict 的別名仍會被宣告
                        {"model_name": "no-info", "model_info": None},
                        "not-a-dict",
                    ]
                }
            ),
            "/v1/models": _ok({"data": [{"id": None}, {"id": "shared"}]}),
        },
    )
    snapshot = await ai_monitoring.get_litellm_runtime_snapshot(USER)
    by_name = {model["name"]: model for model in snapshot["models"]}
    assert by_name["shared"]["status"] == "degraded"
    assert by_name["shared"]["healthy_deployments"] == 1
    assert by_name["shared"]["unhealthy_deployments"] == 1
    assert by_name["fallback-alias"]["status"] == "online"
    assert by_name["no-info"]["status"] == "unknown"
    assert "http://internal:8000" not in str(snapshot)
    assert snapshot["summary"] == {
        "online": 1,
        "degraded": 1,
        "offline": 0,
        "unknown": 1,
    }
    assert snapshot["healthy_deployment_count"] == 3
    assert snapshot["unhealthy_deployment_count"] == 3
