"""層 2 壓測：多學生同時「秒開」課程實驗機（無 DB / PVE）。

驗證 submit_course_provision 的背景派工路徑：
- 100 個學生同時觸發 → 全部處理、不重不漏
- 同一 request 被連點/多 tick 重複提交 → runner task_id 去重，只 provision 一次
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from app.infrastructure.worker import background_tasks
from app.services.scheduling import vm_request_schedule_service
from app.services.vm import vm_request_service

pytestmark = pytest.mark.performance

TOTAL_STUDENTS = 100


@pytest.fixture()
async def runner() -> AsyncIterator[background_tasks.BackgroundTaskRunner]:
    r = background_tasks.init_background_runner(max_concurrency=8)
    yield r
    await background_tasks.shutdown_background_runner(timeout=10)


async def test_100_students_deploy_simultaneously(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done: list[uuid.UUID] = []
    lock = threading.Lock()

    def fake_provision(request_id: uuid.UUID) -> bool:
        time.sleep(0.01)  # 模擬克隆耗時（sync，於 worker thread 執行）
        with lock:
            done.append(request_id)
        return True

    monkeypatch.setattr(
        vm_request_schedule_service,
        "process_single_request_start",
        fake_provision,
    )

    ids = [uuid.uuid4() for _ in range(TOTAL_STUDENTS)]
    for rid in ids:
        vm_request_service.submit_course_provision(rid)

    deadline = time.monotonic() + 30
    while len(done) < TOTAL_STUDENTS and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert len(done) == TOTAL_STUDENTS
    assert len(set(done)) == TOTAL_STUDENTS


async def test_double_click_deploy_provisions_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 request 被重複提交（連點/重試）— runner task_id 去重確保只跑一次。"""
    done: list[uuid.UUID] = []
    lock = threading.Lock()

    def fake_provision(request_id: uuid.UUID) -> bool:
        time.sleep(0.05)
        with lock:
            done.append(request_id)
        return True

    monkeypatch.setattr(
        vm_request_schedule_service,
        "process_single_request_start",
        fake_provision,
    )

    ids = [uuid.uuid4() for _ in range(20)]
    for _burst in range(3):  # 模擬連點三次
        for rid in ids:
            vm_request_service.submit_course_provision(rid)
        await asyncio.sleep(0.01)

    deadline = time.monotonic() + 30
    while len(done) < len(ids) and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # 若有重複任務，會在這段時間完成並被抓到

    assert sorted(done, key=str) == sorted(ids, key=str)
