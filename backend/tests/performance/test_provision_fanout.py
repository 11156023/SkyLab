"""層 2 壓測：200 個克隆請求的 fan-out 吞吐（無 DB / PVE / Redis）。

走 REDIS_ENABLED=false 的本機 fallback：入列 → 行程內 runner → 註冊的
``vm_request.provision`` handler。驗證：併發峰值 ≤ 上限（名額滿時 Retry
重排）、每個 request 恰好處理一次（job id 去重）、總耗時顯著優於循序。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.core.config import settings as core_settings
from app.infrastructure.queue import dispatch, registry
from app.infrastructure.worker import background_tasks
from app.services.scheduling import provision_pool

pytestmark = pytest.mark.performance

CONCURRENCY = 8
TOTAL_REQUESTS = 200
FAKE_CLONE_SECONDS = 0.02


@pytest.fixture()
async def runner(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[background_tasks.BackgroundTaskRunner]:
    """本機 fallback 環境：沒有 Redis、TaskRecord 寫入全部 stub 掉。"""
    r = background_tasks.init_background_runner(max_concurrency=8)
    provision_pool.reset_in_flight()
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(registry, "_mark_running", lambda _tid: None)
    monkeypatch.setattr(registry, "_mark_requeued", lambda _tid: None)
    monkeypatch.setattr(registry, "_mark_finished", lambda *_a, **_k: None)
    monkeypatch.setattr(
        dispatch.task_record_repo,
        "create_task_record",
        lambda **kw: SimpleNamespace(id=uuid.uuid4(), task_type=kw["task_type"]),
    )
    monkeypatch.setattr(provision_pool, "_provisioning_failure", lambda _rid: None)
    # 壓測不等 15 秒重排
    monkeypatch.setattr(provision_pool, "PROVISION_RETRY_DEFER_SECONDS", 0.005)
    yield r
    await background_tasks.shutdown_background_runner(timeout=10)
    provision_pool.reset_in_flight()


def _submit_all(ids: list[uuid.UUID]) -> None:
    # sync 入列必須在 threadpool（模擬 sync 路由），不能在 loop 執行緒上；
    # 整批在同一條執行緒循序送，等同排程 tick 的行為
    for request_id in ids:
        provision_pool.submit_provision(
            object(),  # type: ignore[arg-type]
            request_id=request_id,
            user_id=uuid.uuid4(),
            concurrency=CONCURRENCY,
        )


async def test_200_requests_fanout_throughput(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_flight = 0
    peak = 0
    done: list[uuid.UUID] = []

    async def fake_execute(request_id: uuid.UUID) -> bool:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(FAKE_CLONE_SECONDS)
            done.append(request_id)
            return True
        finally:
            in_flight -= 1

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)

    ids = [uuid.uuid4() for _ in range(TOTAL_REQUESTS)]
    start = time.monotonic()
    await asyncio.to_thread(_submit_all, ids)

    deadline = time.monotonic() + 30
    while len(done) < TOTAL_REQUESTS and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    elapsed = time.monotonic() - start

    # 不重不漏
    assert len(done) == TOTAL_REQUESTS
    assert len(set(done)) == TOTAL_REQUESTS
    # 併發受名額限制（滿了就 Retry 重排，不是排隊等）
    assert peak <= CONCURRENCY
    # 顯著優於循序（循序需 200 × 0.02 = 4 秒；並行理論值 0.5 秒 + 重排開銷）
    sequential_seconds = TOTAL_REQUESTS * FAKE_CLONE_SECONDS
    assert elapsed < sequential_seconds / 2, (
        f"fan-out took {elapsed:.2f}s — not meaningfully faster than "
        f"sequential {sequential_seconds:.2f}s"
    )


async def test_duplicate_storm_processed_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一批 request 被連續多個 tick 重複提交 — 每個仍只 clone 一次。

    去重靠 job id（本機 fallback 是 runner 的 task id）只擋「仍在進行中」的
    任務，所以假任務要等三個 tick 全部送完才放行；否則在計時器粒度較粗的
    平台（Windows 約 15ms）第三個 tick 會落在第一批任務完成之後，被視為新
    任務而重複執行。
    """
    done: list[uuid.UUID] = []
    release = asyncio.Event()

    async def fake_execute(request_id: uuid.UUID) -> bool:
        await release.wait()
        await asyncio.sleep(0.01)
        done.append(request_id)
        return True

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)

    ids = [uuid.uuid4() for _ in range(50)]
    for _tick in range(3):  # 模擬 3 個 scheduler tick 重複掃到同批 request
        await asyncio.to_thread(_submit_all, ids)
        await asyncio.sleep(0.02)
    release.set()

    deadline = time.monotonic() + 30
    while len(done) < len(ids) and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)  # 若有重複任務會在此期間完成

    assert sorted(map(str, done)) == sorted(map(str, ids))
