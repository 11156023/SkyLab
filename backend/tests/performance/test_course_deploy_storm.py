"""層 2 壓測：多學生同時「秒開」課程實驗機（無 DB / PVE / Redis）。

驗證 submit_course_provision 的派工路徑（REDIS_ENABLED=false 的本機 fallback）：
- 100 個學生同時觸發 → 全部處理、不重不漏
- 同一 request 被連點/多 tick 重複提交 → job id 去重，只 provision 一次
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from app.core.config import settings as core_settings
from app.infrastructure.queue import dispatch, registry
from app.infrastructure.worker import background_tasks
from app.services.scheduling import provision_pool, vm_request_schedule_service
from app.services.vm import vm_request_service

pytestmark = pytest.mark.performance

TOTAL_STUDENTS = 100


@pytest.fixture()
async def runner(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[background_tasks.BackgroundTaskRunner]:
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
    monkeypatch.setattr(
        vm_request_service.governance_repo,
        "get_governance_config",
        lambda **_kw: SimpleNamespace(provision_max_concurrency=8),
    )
    monkeypatch.setattr(provision_pool, "_provisioning_failure", lambda _rid: None)
    monkeypatch.setattr(provision_pool, "PROVISION_RETRY_DEFER_SECONDS", 0.005)
    yield r
    await background_tasks.shutdown_background_runner(timeout=10)
    provision_pool.reset_in_flight()


def _deploy_all(ids: list[uuid.UUID]) -> None:
    # sync 入列必須在 threadpool（模擬 sync 路由），不能在 loop 執行緒上；
    # 整批在同一條執行緒循序送，不跟被擋住的假 clone 搶預設 executor 的執行緒
    for request_id in ids:
        vm_request_service.submit_course_provision(
            object(),  # type: ignore[arg-type]
            request_id=request_id,
            user_id=uuid.uuid4(),
        )


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
    await asyncio.to_thread(_deploy_all, ids)

    deadline = time.monotonic() + 30
    while len(done) < TOTAL_STUDENTS and time.monotonic() < deadline:
        await asyncio.sleep(0.01)

    assert len(done) == TOTAL_STUDENTS
    assert len(set(done)) == TOTAL_STUDENTS


async def test_double_click_deploy_provisions_once(
    runner: background_tasks.BackgroundTaskRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一 request 被重複提交（連點/重試）— job id 去重確保只跑一次。

    去重只擋「仍在進行中」的任務：假 clone 要等三次連點全部送完才放行，
    否則慢一點的 CI 機器上第一批已經跑完，第三批會被當成新任務重跑。
    名額壓到 2：被擋住的假 clone 各占一條 executor 執行緒，CI 的預設
    executor 只有幾條，開太多會把入列用的執行緒也卡死。
    """
    monkeypatch.setattr(
        vm_request_service.governance_repo,
        "get_governance_config",
        lambda **_kw: SimpleNamespace(provision_max_concurrency=2),
    )
    done: list[uuid.UUID] = []
    lock = threading.Lock()
    release = threading.Event()

    def fake_provision(request_id: uuid.UUID) -> bool:
        release.wait(timeout=30)
        time.sleep(0.01)
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
        await asyncio.to_thread(_deploy_all, ids)
        await asyncio.sleep(0.01)
    release.set()

    deadline = time.monotonic() + 30
    while len(done) < len(ids) and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # 若有重複任務，會在這段時間完成並被抓到

    assert sorted(done, key=str) == sorted(ids, key=str)
