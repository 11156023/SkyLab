"""克隆 fan-out 併發池測試（無 DB — monkeypatch 實際 provision 執行）。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable

import pytest

from app.infrastructure.worker import background_tasks
from app.services.scheduling import provision_pool


@pytest.fixture()
async def runner() -> AsyncIterator[background_tasks.BackgroundTaskRunner]:
    r = background_tasks.init_background_runner(max_concurrency=8)
    provision_pool.reset_provision_semaphore()
    yield r
    await background_tasks.shutdown_background_runner(timeout=5)
    provision_pool.reset_provision_semaphore()


async def _wait_until(cond: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


class _Tracker:
    def __init__(self, delay: float = 0.03) -> None:
        self.delay = delay
        self.in_flight = 0
        self.peak = 0
        self.calls: list[uuid.UUID] = []

    async def fake_execute(self, request_id: uuid.UUID) -> None:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            self.calls.append(request_id)
        finally:
            self.in_flight -= 1


async def test_concurrency_capped_by_provision_semaphore(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _Tracker()
    monkeypatch.setattr(provision_pool, "_execute_provision", tracker.fake_execute)

    ids = [uuid.uuid4() for _ in range(20)]
    for rid in ids:
        provision_pool.submit_provision(rid, concurrency=4)

    await _wait_until(lambda: len(tracker.calls) == 20)
    assert tracker.peak <= 4


async def test_duplicate_request_id_submitted_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _Tracker(delay=0.1)
    monkeypatch.setattr(provision_pool, "_execute_provision", tracker.fake_execute)

    rid = uuid.uuid4()
    provision_pool.submit_provision(rid, concurrency=4)
    provision_pool.submit_provision(rid, concurrency=4)  # runner task_id 去重

    await _wait_until(lambda: len(tracker.calls) >= 1)
    await asyncio.sleep(0.15)  # 若有重複任務會在此期間跑完
    assert tracker.calls == [rid]


async def test_all_requests_processed_exactly_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _Tracker(delay=0.01)
    monkeypatch.setattr(provision_pool, "_execute_provision", tracker.fake_execute)

    ids = [uuid.uuid4() for _ in range(50)]
    for rid in ids:
        provision_pool.submit_provision(rid, concurrency=8)

    await _wait_until(lambda: len(tracker.calls) == 50)
    assert sorted(map(str, tracker.calls)) == sorted(map(str, ids))


async def test_semaphore_rebuilt_on_size_change(
    runner: background_tasks.BackgroundTaskRunner,
) -> None:
    sem_a = provision_pool.get_provision_semaphore(2)
    sem_a2 = provision_pool.get_provision_semaphore(2)
    sem_b = provision_pool.get_provision_semaphore(6)
    assert sem_a is sem_a2
    assert sem_b is not sem_a


async def test_provision_does_not_starve_runner_slots(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clone 任務排隊等待時，輕量任務仍能立即取得 runner slot。"""
    tracker = _Tracker(delay=0.2)
    monkeypatch.setattr(provision_pool, "_execute_provision", tracker.fake_execute)

    # 塞爆 provision 池（concurrency=2、提交 12 個 → 10 個在等）
    for _ in range(12):
        provision_pool.submit_provision(uuid.uuid4(), concurrency=2)

    light_done = asyncio.Event()

    async def light_task() -> None:
        light_done.set()

    background_tasks.submit(light_task(), name="light")
    # 輕量任務不應被等待中的 provision 卡住（0.1s 遠小於單個 clone 0.2s）
    await asyncio.wait_for(light_done.wait(), timeout=0.1)
