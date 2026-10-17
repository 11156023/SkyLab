"""app.infrastructure.queue（registry／arq_client／dispatch）剩餘分支覆蓋：TaskRecord 寫入、連線池生命週期與入列失敗。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings as core_settings
from app.infrastructure import worker
from app.infrastructure.queue import arq_client, dispatch, registry
from app.models import TaskRecordStatus


@pytest.fixture(autouse=True)
def _reset_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arq_client, "_pool", None)


class _FakeSession:
    instances: list[_FakeSession] = []

    def __init__(self, bind: object) -> None:
        self.bind = bind
        self.entered = False
        _FakeSession.instances.append(self)

    def __enter__(self) -> _FakeSession:
        self.entered = True
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture()
def repo_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    _FakeSession.instances.clear()
    monkeypatch.setattr(registry, "Session", _FakeSession)
    for name in (
        "mark_task_running",
        "mark_task_finished",
        "mark_task_requeued",
        "set_task_progress",
    ):
        monkeypatch.setattr(
            registry.task_record_repo,
            name,
            lambda _n=name, **kw: calls.append((_n, kw)),
        )
    return calls


# ─── registry：TaskRecord 狀態寫入 ──────────────────────────────────────


def test_mark_running_uses_own_session(repo_calls) -> None:
    tid = uuid.uuid4()
    registry._mark_running(tid)
    name, kw = repo_calls[0]
    assert name == "mark_task_running"
    assert kw["task_id"] == tid
    assert kw["session"] is _FakeSession.instances[0]
    assert _FakeSession.instances[0].bind is registry.engine


@pytest.mark.parametrize(
    ("result", "expected_vmid"),
    [
        (None, None),
        ({"vmid": 105}, 105),
        ({"vmid": "105"}, None),
        ({"other": 1}, None),
    ],
)
def test_mark_finished_extracts_int_vmid(repo_calls, result, expected_vmid) -> None:
    tid = uuid.uuid4()
    registry._mark_finished(tid, TaskRecordStatus.succeeded, result=result, error=None)
    name, kw = repo_calls[0]
    assert name == "mark_task_finished"
    assert kw["task_id"] == tid
    assert kw["status"] == TaskRecordStatus.succeeded
    assert kw["result"] == result
    assert kw["resource_vmid"] == expected_vmid


def test_mark_requeued_and_report_progress(repo_calls) -> None:
    tid = uuid.uuid4()
    registry._mark_requeued(tid)
    registry.report_progress(tid, 42)
    assert repo_calls[0] == (
        "mark_task_requeued",
        {"session": _FakeSession.instances[0], "task_id": tid},
    )
    assert repo_calls[1] == (
        "set_task_progress",
        {"session": _FakeSession.instances[1], "task_id": tid, "progress": 42},
    )


async def test_run_registered_task_locally_unknown_name() -> None:
    with pytest.raises(RuntimeError, match="is not registered"):
        await registry.run_registered_task_locally("no.such.task", "x", {})


# ─── arq_client：連線池生命週期 ─────────────────────────────────────────


async def test_init_arq_pool_is_noop_when_pool_exists(monkeypatch) -> None:
    existing = object()
    create_pool = AsyncMock()
    monkeypatch.setattr(arq_client, "_pool", existing)
    monkeypatch.setattr(arq_client, "create_pool", create_pool)

    await arq_client.init_arq_pool()

    create_pool.assert_not_awaited()
    assert arq_client._pool is existing


async def test_get_arq_pool_initializes_lazily(monkeypatch) -> None:
    pool = AsyncMock()
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(arq_client, "create_pool", AsyncMock(return_value=pool))

    assert await arq_client.get_arq_pool() is pool
    # second call reuses the pool
    assert await arq_client.get_arq_pool() is pool


async def test_get_arq_pool_raises_when_connect_fails(monkeypatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(
        arq_client, "create_pool", AsyncMock(side_effect=ConnectionError("down"))
    )

    with pytest.raises(RuntimeError, match="arq pool is not available"):
        await arq_client.get_arq_pool()


async def test_close_arq_pool(monkeypatch) -> None:
    await arq_client.close_arq_pool()  # nothing to close

    pool = AsyncMock()
    monkeypatch.setattr(arq_client, "_pool", pool)
    await arq_client.close_arq_pool()

    pool.aclose.assert_awaited_once()
    assert arq_client._pool is None


# ─── dispatch：入列失敗與去重 ─────────────────────────────────────────


def _record() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), task_type="t.x")


async def test_enqueue_task_marks_failed_on_dispatch_error(monkeypatch) -> None:
    record = _record()
    marked: list[dict[str, Any]] = []
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )
    monkeypatch.setattr(
        dispatch.task_record_repo, "mark_task_finished", lambda **kw: marked.append(kw)
    )
    monkeypatch.setattr(
        dispatch, "_dispatch", AsyncMock(side_effect=ConnectionError("redis gone"))
    )

    with pytest.raises(ConnectionError):
        await dispatch.enqueue_task(
            session=object(),  # type: ignore[arg-type]
            task_type="t.x",
            user_id=uuid.uuid4(),
            payload={},
        )

    assert marked[0]["task_id"] == record.id
    assert marked[0]["status"] == TaskRecordStatus.failed
    assert "redis gone" in marked[0]["error"]


def test_enqueue_task_sync_discards_duplicate(monkeypatch) -> None:
    record = _record()
    deleted: list[object] = []
    session = SimpleNamespace(delete=deleted.append, commit=lambda: None)
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(worker, "get_runner", lambda: None)
    monkeypatch.setattr(
        dispatch.task_record_repo, "create_task_record", lambda **_: record
    )

    async def not_exists(_job_id: str) -> bool:
        return False

    async def dup(**_kw: object) -> None:
        raise dispatch.DuplicateJobError("job-1")

    monkeypatch.setattr(dispatch, "_job_exists", not_exists)
    monkeypatch.setattr(dispatch, "_dispatch", dup)

    result = dispatch.enqueue_task_sync(
        session=session,  # type: ignore[arg-type]
        task_type="t.x",
        user_id=uuid.uuid4(),
        payload={},
        job_id="job-1",
    )

    assert result is None
    assert deleted == [record]


def test_run_then_close_pool_closes_when_redis_enabled(monkeypatch) -> None:
    import asyncio

    closed: list[bool] = []

    async def fake_close() -> None:
        closed.append(True)

    async def work() -> str:
        return "done"

    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    monkeypatch.setattr(arq_client, "close_arq_pool", fake_close)

    assert asyncio.run(dispatch._run_then_close_pool(work())) == "done"
    assert closed == [True]


async def test_local_dispatch_rejects_active_job_id(monkeypatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(worker, "is_active", lambda _tid: True)

    with pytest.raises(dispatch.DuplicateJobError):
        await dispatch._dispatch(
            record_id=uuid.uuid4(), task_type="queue.ping", payload={}, job_id="j"
        )


async def test_local_dispatch_without_runner_raises(monkeypatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", False)
    monkeypatch.setattr(worker, "is_active", lambda _tid: False)

    def fake_submit(coro: Any, **_kw: object) -> str:
        coro.close()
        return ""

    monkeypatch.setattr(worker, "submit", fake_submit)

    with pytest.raises(RuntimeError, match="local background runner"):
        await dispatch._dispatch(
            record_id=uuid.uuid4(), task_type="queue.ping", payload={}
        )


async def test_redis_dispatch_duplicate_without_job_id_raises(monkeypatch) -> None:
    monkeypatch.setattr(core_settings, "REDIS_ENABLED", True)
    pool = SimpleNamespace(enqueue_job=AsyncMock(return_value=None))
    monkeypatch.setattr(dispatch, "get_arq_pool", AsyncMock(return_value=pool))
    rid = uuid.uuid4()

    with pytest.raises(RuntimeError, match=f"duplicate job id {rid}"):
        await dispatch._dispatch(record_id=rid, task_type="queue.ping", payload={})
