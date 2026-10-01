"""登入檢查：元件歸類規則、依身分裁切、快取與管理員重新檢查。

不需要真的 DB／Redis／PVE：system_health_service 的探測函式以 monkeypatch 替換。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models import UserRole
from app.services.monitoring import (
    health_policy,
    preflight_service,
    system_health_service,
)


def _component(name: str, status: str, detail: str | None = None) -> dict[str, Any]:
    return {"name": name, "label": name.upper(), "status": status, "latency_ms": None, "detail": detail}


def _by_key(checks: list[dict[str, Any]]) -> dict[str, str]:
    return {check["key"]: check["status"] for check in checks}


# ─── health_policy.preflight_checks ─────────────────────────────────────────


def test_preflight_checks_fixed_order_and_all_ok() -> None:
    checks = health_policy.preflight_checks(
        [
            _component("ai_gateway", "ok"),
            _component("database", "ok"),
            _component("redis", "ok"),
            _component("worker", "ok"),
            _component("pve:1", "ok"),
            _component("gateway", "attention", "憑證 10 天後到期"),
        ]
    )
    assert [check["key"] for check in checks] == list(health_policy.PREFLIGHT_CHECKS)
    # attention（憑證快到期）服務仍可用，不擋登入
    assert set(_by_key(checks).values()) == {"ok"}


def test_preflight_any_pve_connection_down_fails() -> None:
    checks = health_policy.preflight_checks(
        [_component("pve:1", "ok"), _component("pve:2", "down", "timeout")]
    )
    pve = next(check for check in checks if check["key"] == "pve")
    assert pve["status"] == "fail"
    assert [item["detail"] for item in pve["components"]] == [None, "timeout"]


def test_preflight_disabled_or_missing_is_skipped() -> None:
    checks = _by_key(
        health_policy.preflight_checks(
            [
                _component("database", "ok"),
                _component("redis", "disabled"),
                _component("worker", "disabled"),
                _component("pve", "disabled"),
                _component("gateway", "disabled"),
                # ai_gateway 完全沒出現
            ]
        )
    )
    assert checks == {
        "database": "ok",
        "redis": "skipped",
        "worker": "skipped",
        "pve": "skipped",
        "gateway": "skipped",
        "ai": "skipped",
    }


def test_preflight_unknown_fails_and_ai_models_are_ignored() -> None:
    checks = _by_key(
        health_policy.preflight_checks(
            [
                _component("redis", "down"),
                _component("worker", "unknown"),
                _component("ai_gateway", "ok"),
                # 單一模型掛掉不擋登入
                _component("ai_model:gpt", "down"),
            ]
        )
    )
    assert checks["redis"] == "fail"
    assert checks["worker"] == "fail"
    assert checks["ai"] == "ok"


# ─── preflight_service ──────────────────────────────────────────────────────


class _Probes:
    def __init__(self) -> None:
        self.status: dict[str, str] = {
            "database": "ok",
            "redis": "ok",
            "worker": "ok",
            "pve:1": "ok",
            "gateway": "ok",
            "ai_gateway": "ok",
        }
        self.calls = 0
        self.use_cache_args: list[bool] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def single(name: str) -> Any:
            def probe(**_: Any) -> dict[str, Any]:
                if name == "database":
                    self.calls += 1
                return _component(name, self.status[name], "boom" if self.status[name] == "down" else None)

            return probe

        def listed(name: str) -> Any:
            def probe(*, use_cache: bool = True) -> list[dict[str, Any]]:
                self.use_cache_args.append(use_cache)
                return [_component(name, self.status[name], "boom" if self.status[name] == "down" else None)]

            return probe

        monkeypatch.setattr(system_health_service, "check_database", single("database"))
        monkeypatch.setattr(system_health_service, "check_redis", single("redis"))
        monkeypatch.setattr(system_health_service, "check_worker", single("worker"))
        monkeypatch.setattr(system_health_service, "check_pve", listed("pve:1"))
        monkeypatch.setattr(system_health_service, "check_gateway", listed("gateway"))
        monkeypatch.setattr(system_health_service, "check_ai", listed("ai_gateway"))


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch) -> _Probes:
    preflight_service.reset_cache()
    fake = _Probes()
    fake.install(monkeypatch)
    yield fake
    preflight_service.reset_cache()


def _user(role: UserRole, *, superuser: bool = False) -> Any:
    return SimpleNamespace(role=role, is_superuser=superuser)


def test_student_gets_only_key_and_status(probes: _Probes) -> None:
    probes.status["pve:1"] = "down"
    result = preflight_service.preflight_for(_user(UserRole.student))
    assert result["ok"] is False
    assert result["detailed"] is False
    for check in result["checks"]:
        assert set(check) == {"key", "status"}
    assert _by_key(result["checks"])["pve"] == "fail"


def test_admin_gets_component_labels_and_details(probes: _Probes) -> None:
    probes.status["gateway"] = "down"
    result = preflight_service.preflight_for(_user(UserRole.admin))
    assert result["ok"] is False
    assert result["detailed"] is True
    gateway = next(check for check in result["checks"] if check["key"] == "gateway")
    assert gateway["components"][0]["label"] == "GATEWAY"
    assert gateway["components"][0]["detail"] == "boom"


def test_results_are_cached_and_refresh_is_admin_only(probes: _Probes) -> None:
    student = _user(UserRole.student)
    admin = _user(UserRole.admin)

    assert preflight_service.preflight_for(student)["ok"] is True
    assert probes.calls == 1

    # 服務在快取期間掛掉：學生重新檢查仍拿快取（不會讓一整班重打探測）
    probes.status["database"] = "down"
    assert preflight_service.preflight_for(student, refresh=True)["ok"] is True
    assert probes.calls == 1

    # 管理員的重新檢查略過快取，連 PVE／Gateway／AI 各自的快取一起略過
    result = preflight_service.preflight_for(admin, refresh=True)
    assert probes.calls == 2
    assert result["ok"] is False
    assert probes.use_cache_args[-3:] == [False, False, False]


async def test_async_concurrent_logins_share_one_computation(
    probes: _Probes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一整班同時登入：只算一次，等待者不各自佔一條 threadpool 執行緒。"""
    import asyncio
    import threading

    started = threading.Event()
    release = threading.Event()
    original = preflight_service._collect_components

    def slow_collect(*, use_cache: bool) -> list[dict[str, Any]]:
        started.set()
        release.wait(timeout=5)
        return original(use_cache=use_cache)

    monkeypatch.setattr(preflight_service, "_collect_components", slow_collect)

    student = _user(UserRole.student)
    tasks = [
        asyncio.create_task(preflight_service.preflight_for_async(student))
        for _ in range(20)
    ]
    await asyncio.to_thread(started.wait, 5)
    await asyncio.sleep(0.05)
    # 計算進行中：20 個請求共用一個 in-flight task，探測尚未放行
    assert probes.calls == 0
    assert preflight_service._PreflightCache.inflight is not None
    release.set()
    results = await asyncio.gather(*tasks)

    assert probes.calls == 1
    assert all(result["ok"] is True for result in results)
    # 每個請求拿到自己的 copy
    results[0]["checks"][0]["status"] = "mutated"
    assert results[1]["checks"][0]["status"] != "mutated"
