"""資源監控頁的 Grafana 連結：探測內網 /api/health 決定是否啟用，結果快取。

以 httpx.MockTransport 取代真的連線，不需要 Grafana 容器。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.services.monitoring import grafana_service


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grafana_service._ProbeCache, "expires_at", 0.0)
    monkeypatch.setattr(grafana_service._ProbeCache, "enabled", False)
    monkeypatch.setattr(settings, "GRAFANA_INTERNAL_URL", "http://grafana:3000/grafana/")
    monkeypatch.setattr(settings, "GRAFANA_ROOT_URL", None)


def _mock_grafana(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[str]:
    calls: list[str] = []
    real_client = httpx.AsyncClient

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(grafana_service.httpx, "AsyncClient", _factory)
    return calls


async def test_enabled_when_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_grafana(monkeypatch, lambda _: httpx.Response(200, json={"database": "ok"}))

    assert await grafana_service.get_grafana_link() == {"enabled": True, "url": "/grafana/"}
    assert calls == ["http://grafana:3000/grafana/api/health"]


async def test_uses_root_url_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GRAFANA_ROOT_URL", " https://skylab.example.edu/grafana/ ")
    _mock_grafana(monkeypatch, lambda _: httpx.Response(200))

    link = await grafana_service.get_grafana_link()
    assert link == {"enabled": True, "url": "https://skylab.example.edu/grafana/"}


async def test_disabled_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known", request=request)

    _mock_grafana(monkeypatch, _refuse)

    assert await grafana_service.get_grafana_link() == {"enabled": False, "url": None}


async def test_disabled_when_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_grafana(monkeypatch, lambda _: httpx.Response(503))

    assert await grafana_service.get_grafana_link() == {"enabled": False, "url": None}


async def test_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _mock_grafana(monkeypatch, lambda _: httpx.Response(200))

    await grafana_service.get_grafana_link()
    await grafana_service.get_grafana_link()
    assert len(calls) == 1

    await grafana_service.get_grafana_link(use_cache=False)
    assert len(calls) == 2
