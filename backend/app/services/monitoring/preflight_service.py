"""登入檢查：每次登入後前端顯示的「服務檢查」畫面背後的真實探測。

探測本身沿用 system_health_service（DB、Redis、worker、PVE、Gateway、AI），
這裡只負責：
- 各項並行跑（PVE／Gateway／AI 各自可能要好幾秒，串起來太久）
- 整份結果快取 ``PREFLIGHT_CACHE_SECONDS`` 秒，而且同一時間只算一次
  （上課時一整班同時登入，不能每個人各開一條 SSH 到 Gateway）
- 依身分裁切：學生／老師只拿到每項的 key 與成敗，管理員才有元件名稱與錯誤細節
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

from starlette.concurrency import run_in_threadpool

from app.core.permissions import is_admin
from app.services.monitoring import health_policy, system_health_service

PREFLIGHT_CACHE_SECONDS = 10.0


class _PreflightCache:
    # 計算期間一直持有：同時進來的請求排隊等同一份結果
    lock: ClassVar[threading.Lock] = threading.Lock()
    expires_at: ClassVar[float] = 0.0
    checks: ClassVar[list[dict[str, Any]]] = []
    # run_checks_async 共用的計算中 task（只在 event loop 上讀寫）
    inflight: ClassVar[asyncio.Future[list[dict[str, Any]]] | None] = None


def reset_cache() -> None:
    """測試用。"""
    with _PreflightCache.lock:
        _PreflightCache.checks = []
        _PreflightCache.expires_at = 0.0
        _PreflightCache.inflight = None


def _collect_components(*, use_cache: bool) -> list[dict[str, Any]]:
    def core() -> list[dict[str, Any]]:
        database = system_health_service.check_database()
        redis = system_health_service.check_redis()
        worker = system_health_service.check_worker(redis_ok=redis["status"] == "ok")
        return [database, redis, worker]

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="login-preflight") as pool:
        futures = [
            pool.submit(core),
            pool.submit(system_health_service.check_pve, use_cache=use_cache),
            pool.submit(system_health_service.check_gateway, use_cache=use_cache),
            pool.submit(system_health_service.check_ai, use_cache=use_cache),
        ]
        # 各探測自己有逾時，也不會丟例外
        return [component for future in futures for component in future.result()]


def run_checks(*, use_cache: bool = True) -> list[dict[str, Any]]:
    with _PreflightCache.lock:
        if use_cache and time.monotonic() < _PreflightCache.expires_at:
            return [dict(check) for check in _PreflightCache.checks]
        checks = health_policy.preflight_checks(_collect_components(use_cache=use_cache))
        _PreflightCache.checks = [dict(check) for check in checks]
        _PreflightCache.expires_at = time.monotonic() + PREFLIGHT_CACHE_SECONDS
    return checks


async def run_checks_async(*, use_cache: bool = True) -> list[dict[str, Any]]:
    """給 async 端點用：同一時間只有一個 task 佔 threadpool 去算，其他人 await 它。

    同步版在計算期間（PVE／Gateway／AI 探測，最久十幾秒）一直持有
    threading.Lock；一整班同時登入時，排隊的請求各自佔著一條 anyio 執行緒
    （全站只有 40 條）乾等，其他同步端點跟著塞住。這裡改成共用一個 in-flight
    task，等待者不佔執行緒。
    """
    if not use_cache:
        return await run_in_threadpool(run_checks, use_cache=False)
    if time.monotonic() < _PreflightCache.expires_at:
        return [dict(check) for check in _PreflightCache.checks]
    task = _PreflightCache.inflight
    if (
        task is None
        or task.done()
        or task.get_loop() is not asyncio.get_running_loop()
    ):
        task = asyncio.ensure_future(run_in_threadpool(run_checks, use_cache=True))
        _PreflightCache.inflight = task
    # shield：發起的請求斷線時，其他等待者的結果不能跟著被取消
    checks = await asyncio.shield(task)
    return [dict(check) for check in checks]


def _shape(user: Any, checks: list[dict[str, Any]]) -> dict[str, Any]:
    detailed = is_admin(user)
    ok = all(check["status"] != "fail" for check in checks)
    if not detailed:
        checks = [{"key": check["key"], "status": check["status"]} for check in checks]
    return {"ok": ok, "detailed": detailed, "checks": checks}


def preflight_for(user: Any, *, refresh: bool = False) -> dict[str, Any]:
    """``refresh`` 只對管理員有效（重新檢查時略過快取，含 PVE／Gateway／AI 各自的快取）。"""
    return _shape(user, run_checks(use_cache=not (refresh and is_admin(user))))


async def preflight_for_async(user: Any, *, refresh: bool = False) -> dict[str, Any]:
    """``preflight_for`` 的 async 版，等待結果時不佔 threadpool。"""
    checks = await run_checks_async(use_cache=not (refresh and is_admin(user)))
    return _shape(user, checks)


__all__ = [
    "PREFLIGHT_CACHE_SECONDS",
    "preflight_for",
    "preflight_for_async",
    "reset_cache",
    "run_checks",
    "run_checks_async",
]
