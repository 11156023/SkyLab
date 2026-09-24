import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings as core_settings
from app.infrastructure import worker
from app.infrastructure.queue import arq_client, dispatch, registry


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """This unit-test module does not require the external test database."""


@pytest.fixture(autouse=True)
def reset_arq_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arq_client, "_pool", None)


async def test_init_arq_pool_skips_connection_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_pool = AsyncMock()
    # redis_enabled 已改為代理 core settings 的唯讀 property
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    await arq_client.init_arq_pool()

    create_pool.assert_not_awaited()
    assert arq_client._pool is None


async def test_get_arq_pool_does_not_connect_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_pool = AsyncMock()
    # redis_enabled 已改為代理 core settings 的唯讀 property
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    with pytest.raises(RuntimeError, match="REDIS_ENABLED=false"):
        await arq_client.get_arq_pool()

    create_pool.assert_not_awaited()


async def test_init_arq_pool_connects_when_redis_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = AsyncMock()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    await arq_client.init_arq_pool()

    create_pool.assert_awaited_once()
    assert arq_client._pool is pool


async def test_enqueue_uses_local_runner_when_redis_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(id=uuid.uuid4())
    scheduled: list[object] = []
    executed: list[tuple[str, str, dict[str, object]]] = []

    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(
        dispatch.task_record_repo,
        "create_task_record",
        lambda **_: record,
    )

    async def fake_run(
        name: str,
        record_id: str,
        payload: dict[str, object],
    ) -> None:
        executed.append((name, record_id, payload))

    def fake_submit(coro: object, **kwargs: object) -> str:
        scheduled.append(coro)
        return str(kwargs["task_id"])

    monkeypatch.setattr(registry, "run_registered_task_locally", fake_run)
    monkeypatch.setattr(worker, "submit", fake_submit)

    result = await dispatch.enqueue_task(
        session=object(),  # type: ignore[arg-type]
        task_type="template.convert",
        user_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        payload={"vmid": 101},
    )

    assert result is record
    assert len(scheduled) == 1
    await asyncio.wait_for(scheduled[0], timeout=5)  # type: ignore[misc]
    assert executed == [
        ("template.convert", str(record.id), {"vmid": 101})
    ]


def test_enqueue_task_sync_runs_without_bound_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒有 lifespan（CLI／單元測試）時就地開 loop 完成入列。"""
    record = SimpleNamespace(id=uuid.uuid4())
    executed: list[tuple[str, str, dict[str, object]]] = []

    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )
    monkeypatch.setattr(worker, "get_runner", lambda: None)

    async def fake_run(name: str, record_id: str, payload: dict[str, object]) -> None:
        executed.append((name, record_id, payload))

    def fake_submit(coro: object, **kwargs: object) -> str:
        coro.close()  # type: ignore[attr-defined]
        executed.append(("submitted", str(kwargs["task_id"]), {}))
        return str(kwargs["task_id"])

    monkeypatch.setattr(registry, "run_registered_task_locally", fake_run)
    monkeypatch.setattr(worker, "submit", fake_submit)

    result = dispatch.enqueue_task_sync(
        session=object(),  # type: ignore[arg-type]
        task_type="resource.reset",
        user_id=uuid.uuid4(),
        payload={"vmid": 101},
    )

    assert result is record
    assert executed == [("submitted", str(record.id), {})]


def test_enqueue_task_sync_uses_bound_loop_from_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sync 路由（threadpool）呼叫時，enqueue 要跑在 lifespan 綁定的主 loop 上。"""
    record = SimpleNamespace(id=uuid.uuid4())
    seen_loops: list[asyncio.AbstractEventLoop] = []

    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )

    async def fake_dispatch(**_kwargs: object) -> None:
        seen_loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(dispatch, "_dispatch", fake_dispatch)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            worker, "get_runner", lambda: SimpleNamespace(bound_loop=lambda: loop)
        )
        result = await asyncio.to_thread(
            dispatch.enqueue_task_sync,
            session=object(),  # type: ignore[arg-type]
            task_type="resource.reset",
            user_id=uuid.uuid4(),
            payload={},
        )
        assert result is record
        assert seen_loops == [loop]

    asyncio.run(main())


async def test_enqueue_task_sync_refuses_event_loop_thread() -> None:
    with pytest.raises(RuntimeError, match="await enqueue_task"):
        dispatch.enqueue_task_sync(
            session=object(),  # type: ignore[arg-type]
            task_type="resource.reset",
            user_id=uuid.uuid4(),
            payload={},
        )


async def test_enqueue_with_job_id_discards_duplicate_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """固定 job id 已在隊列：arq 回 None，剛建的 TaskRecord 要刪掉、回傳 None。"""
    record = SimpleNamespace(id=uuid.uuid4(), task_type="vm_request.provision")
    deleted: list[object] = []
    committed: list[bool] = []
    session = SimpleNamespace(
        delete=lambda obj: deleted.append(obj), commit=lambda: committed.append(True)
    )
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=None))
    monkeypatch.setattr(dispatch, "get_arq_pool", AsyncMock(return_value=pool))
    # 先問 arq 時還不在隊列（與 enqueue 之間的競態），enqueue 才發現重複
    monkeypatch.setattr(dispatch, "_job_exists", AsyncMock(return_value=False))
    marked: list[object] = []
    monkeypatch.setattr(
        dispatch.task_record_repo, "mark_task_finished", lambda **kw: marked.append(kw)
    )

    result = await dispatch.enqueue_task(
        session=session,  # type: ignore[arg-type]
        task_type="vm_request.provision",
        user_id=uuid.uuid4(),
        payload={},
        job_id="vm_request:abc",
    )

    assert result is None
    assert deleted == [record]
    assert committed == [True]
    assert marked == []  # 去重不是失敗，不能把 record 標成 failed
    assert pool.enqueue_job.await_args.kwargs["_job_id"] == "vm_request:abc"


async def test_enqueue_without_job_id_uses_record_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=object()))
    monkeypatch.setattr(dispatch, "get_arq_pool", AsyncMock(return_value=pool))

    result = await dispatch.enqueue_task(
        session=object(),  # type: ignore[arg-type]
        task_type="template.convert",
        user_id=uuid.uuid4(),
        payload={},
    )

    assert result is record
    assert pool.enqueue_job.await_args.kwargs["_job_id"] == str(record.id)


async def test_enqueue_with_job_id_skips_record_when_job_already_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """排程每 tick 重送排隊中的申請單：先問 arq，已在隊列就連 TaskRecord 都不建。"""
    created: list[object] = []
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **kw: created.append(kw)
    )
    monkeypatch.setattr(dispatch, "_job_exists", AsyncMock(return_value=True))

    result = await dispatch.enqueue_task(
        session=object(),  # type: ignore[arg-type]
        task_type="vm_request.provision",
        user_id=uuid.uuid4(),
        payload={},
        job_id="vm_request:abc",
    )

    assert result is None
    assert created == []


async def test_job_exists_maps_arq_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from arq.jobs import JobStatus

    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(dispatch, "get_arq_pool", AsyncMock(return_value=object()))
    statuses: list[JobStatus] = []

    class _Job:
        def __init__(self, *_a: object, **_k: object) -> None:
            """測試替身。"""

        async def status(self) -> JobStatus:
            return statuses.pop(0)

    import arq.jobs

    monkeypatch.setattr(arq.jobs, "Job", _Job)

    statuses[:] = [JobStatus.queued, JobStatus.in_progress, JobStatus.not_found, JobStatus.complete]
    assert await dispatch._job_exists("x") is True
    assert await dispatch._job_exists("x") is True
    assert await dispatch._job_exists("x") is False
    assert await dispatch._job_exists("x") is False


def test_enqueue_task_sync_marks_failed_and_cancels_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主 loop 逾時沒回應：取消 future、TaskRecord 標 failed、對呼叫端拋錯。"""
    record = SimpleNamespace(id=uuid.uuid4(), task_type="resource.reset")
    marked: list[dict[str, object]] = []
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )
    monkeypatch.setattr(
        dispatch.task_record_repo, "mark_task_finished", lambda **kw: marked.append(kw)
    )
    monkeypatch.setattr(dispatch, "ENQUEUE_SYNC_TIMEOUT_SECONDS", 0.05)

    async def never_returns(**_kwargs: object) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(dispatch, "_dispatch", never_returns)

    async def main() -> None:
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            worker, "get_runner", lambda: SimpleNamespace(bound_loop=lambda: loop)
        )
        with pytest.raises(TimeoutError):
            await asyncio.to_thread(
                dispatch.enqueue_task_sync,
                session=object(),  # type: ignore[arg-type]
                task_type="resource.reset",
                user_id=uuid.uuid4(),
                payload={},
            )
        # 讓被取消的 coroutine 收尾
        await asyncio.sleep(0)

    asyncio.run(main())

    assert len(marked) == 1
    assert marked[0]["task_id"] == record.id
    assert "入列失敗" in str(marked[0]["error"])
