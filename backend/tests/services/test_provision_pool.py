"""克隆 fan-out：API 端入列（job id 去重）、worker 端名額滿時 Retry 重排。"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from arq.worker import Retry

from app.models import VMProvisioningStatus
from app.services.scheduling import provision_pool


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture(autouse=True)
def _fresh_counter():
    provision_pool.reset_in_flight()
    yield
    provision_pool.reset_in_flight()


# ─── API 端：入列 ─────────────────────────────────────────────────────────────


def test_submit_provision_enqueues_with_stable_job_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    record = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(
        provision_pool, "enqueue_task_sync", lambda **kw: calls.append(kw) or record
    )
    request_id, user_id = uuid.uuid4(), uuid.uuid4()

    result = provision_pool.submit_provision(
        object(),  # type: ignore[arg-type]
        request_id=request_id,
        user_id=user_id,
        concurrency=4,
    )

    assert result is record
    assert calls[0]["task_type"] == provision_pool.TASK_PROVISION
    assert calls[0]["user_id"] == user_id
    assert calls[0]["payload"] == {"request_id": str(request_id), "concurrency": 4}
    # 同一張單所有入列路徑共用這個 job id，排隊中／執行中不會再入列
    assert calls[0]["job_id"] == f"vm_request:{request_id}"


# ─── worker 端：名額、結果 ────────────────────────────────────────────────────


async def test_worker_runs_when_slot_available(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    async def fake_execute(_request_id: uuid.UUID) -> bool:
        seen.append(provision_pool.in_flight_count())
        return True

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)
    monkeypatch.setattr(provision_pool, "_provisioning_failure", lambda _rid: None)

    result = await provision_pool.run_provision_job(uuid.uuid4(), concurrency=2)

    assert result["started"] is True
    assert seen == [1]  # 執行期間計數 +1
    assert provision_pool.in_flight_count() == 0  # 結束後歸還


async def test_worker_defers_with_retry_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """名額滿時不佔 slot 等待，改丟 Retry 讓 arq 稍後重排。"""
    gate = asyncio.Event()
    started = asyncio.Event()

    async def slow_execute(_request_id: uuid.UUID) -> bool:
        started.set()
        await gate.wait()
        return True

    monkeypatch.setattr(provision_pool, "_execute_provision", slow_execute)
    monkeypatch.setattr(provision_pool, "_provisioning_failure", lambda _rid: None)

    running = asyncio.create_task(
        provision_pool.run_provision_job(uuid.uuid4(), concurrency=1)
    )
    await started.wait()

    with pytest.raises(Retry) as excinfo:
        await provision_pool.run_provision_job(uuid.uuid4(), concurrency=1)
    assert excinfo.value.defer_score == provision_pool.PROVISION_RETRY_DEFER_SECONDS * 1000
    assert provision_pool.in_flight_count() == 1  # 被擋下的那個沒有佔名額

    gate.set()
    await running
    assert provision_pool.in_flight_count() == 0


async def test_worker_releases_slot_when_execute_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(_request_id: uuid.UUID) -> bool:
        raise RuntimeError("pve down")

    monkeypatch.setattr(provision_pool, "_execute_provision", boom)

    with pytest.raises(RuntimeError, match="pve down"):
        await provision_pool.run_provision_job(uuid.uuid4(), concurrency=1)
    assert provision_pool.in_flight_count() == 0


async def test_worker_raises_when_request_ends_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(_request_id: uuid.UUID) -> bool:
        return False

    monkeypatch.setattr(provision_pool, "_execute_provision", fake_execute)
    monkeypatch.setattr(
        provision_pool, "_provisioning_failure", lambda _rid: "clone exploded"
    )

    with pytest.raises(RuntimeError, match="clone exploded"):
        await provision_pool.run_provision_job(uuid.uuid4(), concurrency=1)


def test_provisioning_failure_reads_request_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repositories import vm_request as vm_request_repo

    failed = SimpleNamespace(
        vmid=None,
        provisioning_status=VMProvisioningStatus.failed,
        provisioning_error="no space left",
    )
    monkeypatch.setattr(
        vm_request_repo, "get_vm_request_by_id", lambda **_kw: failed
    )

    class _Session:
        def __init__(self, _engine: Any) -> None:
            """測試替身。"""

        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *_exc: Any) -> None:
            """測試替身。"""

    monkeypatch.setattr(provision_pool, "Session", _Session)

    assert provision_pool._provisioning_failure(uuid.uuid4()) == "no space left"

    failed.provisioning_status = VMProvisioningStatus.completed
    failed.vmid = 150
    assert provision_pool._provisioning_failure(uuid.uuid4()) is None


def test_provision_task_retry_budget_covers_long_waits() -> None:
    # 每次重排算一次 try；上限要遠大於大班級開機時可能的等待輪數
    assert (
        provision_pool.PROVISION_MAX_TRIES * provision_pool.PROVISION_RETRY_DEFER_SECONDS
        >= 24 * 3600
    )
