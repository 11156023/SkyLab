"""AI_API_API_KEY 空白或仍是範本值（local 以外）：只停用 AI API，不擋 backend 啟動。

- AI API 請求回「未設定」錯誤，不送去 LiteLLM
- 啟動時記警告
- 系統健康卡列出「AI API 未設定」，但不拉低整體狀態、不發告警、不影響登入檢查
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.routes import ai_proxy
from app.core.config import settings as core_settings
from app.core.request_context import RequestContext
from app.features.ai.config import AIAPIEnvSettings
from app.features.ai.config import settings as ai_api_settings
from app.services.llm_gateway import relay_service
from app.services.monitoring import health_policy, system_health_service
from tests.api.routes.test_ai_proxy_routes import _issue_ai_api_key

PLACEHOLDER = "ai-api-secret-key-change-me"


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", PLACEHOLDER)


@pytest.fixture(autouse=True)
def _reset_ai_cache() -> Any:
    system_health_service.reset_ai_cache()
    yield
    system_health_service.reset_ai_cache()


def _no_upstream() -> Any:
    pytest.fail("AI API 未設定時不可連到 LiteLLM")


# ─── 判定 ────────────────────────────────────────────────────────────────


def test_shipped_default_is_the_placeholder() -> None:
    assert AIAPIEnvSettings.model_fields["ai_api_api_key"].default == PLACEHOLDER


@pytest.mark.parametrize(
    ("environment", "key", "enabled"),
    [
        ("production", PLACEHOLDER, False),
        ("staging", PLACEHOLDER, False),
        ("production", "", False),
        ("production", "   ", False),
        ("production", "replace-with-the-campus-service-key", False),
        ("production", "sk-real-restricted-key", True),
        ("local", PLACEHOLDER, True),
        ("local", "", True),
    ],
)
def test_ai_api_enabled_only_with_a_real_key_outside_local(
    monkeypatch: pytest.MonkeyPatch, environment: str, key: str, enabled: bool
) -> None:
    monkeypatch.setattr(core_settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", key)

    assert ai_api_settings.ai_api_enabled is enabled


# ─── AI API 請求 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/ai-proxy/chat/completions", {"model": "m", "messages": []}),
        ("post", "/ai-proxy/responses", {"model": "m", "input": "hi"}),
        ("get", "/ai-proxy/models", None),
    ],
)
def test_ai_api_requests_get_not_configured_error(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    api_key, _credential_id = _issue_ai_api_key(
        client=client, db=db, superuser_token_headers=superuser_token_headers
    )
    monkeypatch.setattr(relay_service, "_get_relay_http_client", _no_upstream)

    async def no_rate_limit(**_kwargs: Any) -> None:
        # local 以外 Redis 限流是 fail-closed；這裡只驗證未設定的處理
        return None

    monkeypatch.setattr(ai_proxy, "_enforce_rate_limit", no_rate_limit)
    monkeypatch.setattr(core_settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", PLACEHOLDER)

    kwargs: dict[str, Any] = {"headers": {"Authorization": f"Bearer {api_key}"}}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(f"{core_settings.API_V1_STR}{path}", **kwargs)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "ai_api_not_configured"
    assert "AI API" in error["message"]
    assert PLACEHOLDER not in response.text


def test_not_configured_error_is_translated(unconfigured: None) -> None:
    from app.core.request_context import _request_context

    responses = {}
    for language in ("zh-TW", "en", "ja"):
        token = _request_context.set(RequestContext(language=language))
        try:
            responses[language] = relay_service.ai_api_not_configured().body.decode()
        finally:
            _request_context.reset(token)

    assert "AI API 未設定" in responses["zh-TW"]
    assert "not configured" in responses["en"]
    assert "未設定" in responses["ja"]


# ─── 啟動警告 ────────────────────────────────────────────────────────────


def test_startup_warns_when_ai_api_is_not_configured(
    unconfigured: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=relay_service.logger.name):
        relay_service.start_relay_runtime()

    assert "AI_API_API_KEY" in caplog.text
    assert PLACEHOLDER not in caplog.text


def test_startup_warning_waits_for_logging_inside_the_event_loop(
    unconfigured: None, caplog: pytest.LogCaptureFixture
) -> None:
    async def lifespan_start() -> bool:
        relay_service.start_relay_runtime()
        logged_immediately = "AI_API_API_KEY" in caplog.text
        await asyncio.sleep(0)
        return logged_immediately

    with caplog.at_level(logging.WARNING, logger=relay_service.logger.name):
        logged_immediately = asyncio.run(lifespan_start())

    assert logged_immediately is False
    assert "AI_API_API_KEY" in caplog.text


def test_no_startup_warning_with_a_real_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(core_settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "sk-real-restricted-key")

    with caplog.at_level(logging.WARNING, logger=relay_service.logger.name):
        relay_service.start_relay_runtime()

    assert "AI_API_API_KEY" not in caplog.text


# ─── 系統健康卡 ──────────────────────────────────────────────────────────


def test_health_card_shows_ai_api_not_configured(
    unconfigured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_api_settings, "litellm_runtime_api_key", None)

    components = system_health_service.check_ai(use_cache=False)

    by_name = {component["name"]: component for component in components}
    ai_api = by_name[health_policy.AI_API_COMPONENT]
    assert ai_api["label"] == "AI API"
    assert ai_api["status"] == "disabled"
    assert ai_api["detail"].startswith("未設定")
    assert "alert_message" not in ai_api
    # 健康卡會提示，但不拉低整體、不發告警、不擋登入
    assert health_policy.overall_status(components, [], []) == "ok"
    assert health_policy.build_findings(components, [], []) == []
    [ai_check] = [
        check for check in health_policy.preflight_checks(components) if check["key"] == "ai"
    ]
    assert ai_check["status"] == "skipped"
    assert all(item["label"] != "AI API" for item in ai_check["components"])


def test_health_card_has_no_ai_api_row_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(ai_api_settings, "ai_api_api_key", "sk-real-restricted-key")
    monkeypatch.setattr(ai_api_settings, "litellm_runtime_api_key", None)

    components = system_health_service.check_ai(use_cache=False)

    assert [component["name"] for component in components] == [
        health_policy.AI_GATEWAY_COMPONENT
    ]


def test_relay_generation_rejects_before_admission(unconfigured: None) -> None:
    request = SimpleNamespace(headers={})

    response = asyncio.run(
        relay_service.relay_generation(
            endpoint="chat/completions",
            request=request,  # type: ignore[arg-type]
            payload={"model": "m"},
            model_name="m",
            user=SimpleNamespace(id="u"),
            credential=SimpleNamespace(),
        )
    )

    assert response.status_code == 503
    assert b"ai_api_not_configured" in response.body
