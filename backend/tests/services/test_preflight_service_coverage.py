"""app.services.monitoring.preflight_service：async 版的快取命中與略過快取路徑。"""

from __future__ import annotations

import time

import pytest

from app.services.monitoring import preflight_service


@pytest.fixture(autouse=True)
def _reset():
    preflight_service.reset_cache()
    yield
    preflight_service.reset_cache()


async def test_async_returns_cached_copy_without_computing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = [{"key": "database", "status": "ok"}]
    monkeypatch.setattr(preflight_service._PreflightCache, "checks", cached)
    monkeypatch.setattr(
        preflight_service._PreflightCache, "expires_at", time.monotonic() + 600
    )
    monkeypatch.setattr(
        preflight_service,
        "run_checks",
        lambda **_: pytest.fail("cache hit should not compute"),
    )
    result = await preflight_service.run_checks_async()
    assert result == cached
    assert result[0] is not cached[0]
