"""層 2 壓測：200 個克隆請求的 fan-out 吞吐（無 DB / PVE）。

驗證：併發峰值 ≤ 上限、每個 request 恰好處理一次（不重不漏）、
總耗時顯著優於循序。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from app.infrastructure.worker import background_tasks
from app.services.scheduling import provision_pool

pytestmark = pytest.mark.performance

CONCURRENCY = 8
TOTAL_REQUESTS = 200
FAKE_CLONE_SECONDS = 0.02


@pytest.fixture()
async def runner() -> AsyncIterator[background_tasks.BackgroundTaskRunner]:
    r = background_tasks.init_background_runner(max_concurrency=8)
    provision_pool.reset_provision_semaphore()
    yield r
    await background_tasks.shutdown_background_runner(timeout=10)
    provision_pool.reset_provision_semaphore()


async def test_200_requests_fanout_throughput(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_flight = 0
    peak = 0
    done: list[uuid.UUID] = []

    async def fake_execute(request_id: uuid.UUID) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(FAKE_CLONE_SECONDS)
            done.append(request_id)
        finally:
            in_flight -= 1

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)

    ids = [uuid.uuid4() for _ in range(TOTAL_REQUESTS)]
    start = time.monotonic()
    for rid in ids:
        provision_pool.submit_provision(rid, concurrency=CONCURRENCY)

    deadline = time.monotonic() + 30
    while len(done) < TOTAL_REQUESTS and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    elapsed = time.monotonic() - start

    # 不重不漏
    assert len(done) == TOTAL_REQUESTS
    assert len(set(done)) == TOTAL_REQUESTS
    # 併發受獨立 semaphore 限制
    assert peak <= CONCURRENCY
    # 顯著優於循序（循序需 200 × 0.02 = 4 秒；並行理論值 0.5 秒 + 排程開銷）
    sequential_seconds = TOTAL_REQUESTS * FAKE_CLONE_SECONDS
    assert elapsed < sequential_seconds / 2, (
        f"fan-out took {elapsed:.2f}s — not meaningfully faster than "
        f"sequential {sequential_seconds:.2f}s"
    )


async def test_duplicate_storm_processed_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一批 request 被連續多個 tick 重複提交 — 每個仍只 clone 一次。"""
    done: list[uuid.UUID] = []

    async def fake_execute(request_id: uuid.UUID) -> None:
        await asyncio.sleep(0.05)
        done.append(request_id)

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)

    ids = [uuid.uuid4() for _ in range(50)]
    for _tick in range(3):  # 模擬 3 個 scheduler tick 重複掃到同批 request
        for rid in ids:
            provision_pool.submit_provision(rid, concurrency=CONCURRENCY)
        await asyncio.sleep(0.02)

    deadline = time.monotonic() + 30
    while len(done) < len(ids) and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.1)  # 若有重複任務會在此期間完成

    assert sorted(map(str, done)) == sorted(map(str, ids))
