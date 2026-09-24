"""Unit tests for the arq queue task registry (no Redis / DB required)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.infrastructure.queue import registry
from app.models import TaskRecordStatus


@pytest.fixture
def clean_registry():
    """Snapshot and restore the global task registry around each test."""
    snapshot = dict(registry._registry)
    yield
    registry._registry.clear()
    registry._registry.update(snapshot)


def test_queue_task_registers_handler(clean_registry) -> None:
    @registry.queue_task("test.echo", timeout_seconds=10)
    async def echo(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    assert "test.echo" in registry._registry
    handler, timeout = registry._registry["test.echo"]
    assert timeout == 10

    functions = registry.registered_functions()
    names = [f.name for f in functions]
    assert "test.echo" in names


def test_queue_task_rejects_duplicate_name(clean_registry) -> None:
    @registry.queue_task("test.dup")
    async def first(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
        return None

    with pytest.raises(ValueError, match="already registered"):

        @registry.queue_task("test.dup")
        async def second(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
            return None


@pytest.mark.asyncio
async def test_wrapper_marks_running_then_succeeded(
    clean_registry, monkeypatch
) -> None:
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(
        registry, "_mark_running", lambda tid: calls.append(("running", tid))
    )

    def fake_finished(tid, status, *, result=None, error=None):
        calls.append(("finished", status, result, error))

    monkeypatch.setattr(registry, "_mark_finished", fake_finished)

    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        return {"vmid": 123}

    runner = registry._wrap("test.ok", handler)
    task_id = uuid.uuid4()
    await runner({}, str(task_id), {"x": 1})

    assert calls[0] == ("running", task_id)
    assert calls[1] == ("finished", TaskRecordStatus.succeeded, {"vmid": 123}, None)


@pytest.mark.asyncio
async def test_wrapper_marks_failed_and_reraises(
    clean_registry, monkeypatch
) -> None:
    calls: list[tuple[str, Any]] = []

    monkeypatch.setattr(registry, "_mark_running", lambda tid: None)

    def fake_finished(tid, status, *, result=None, error=None):
        calls.append((status, error))

    monkeypatch.setattr(registry, "_mark_finished", fake_finished)

    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
        raise RuntimeError("PVE exploded")

    runner = registry._wrap("test.fail", handler)
    with pytest.raises(RuntimeError, match="PVE exploded"):
        await runner({}, str(uuid.uuid4()), {})

    assert calls == [(TaskRecordStatus.failed, "PVE exploded")]


@pytest.mark.asyncio
async def test_wrapper_requeues_on_retry_without_marking_failed(
    clean_registry, monkeypatch
) -> None:
    """handler 丟 Retry（名額滿）：TaskRecord 退回 queued，不算失敗。"""
    from arq.worker import Retry

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(registry, "_mark_running", lambda tid: calls.append(("running", tid)))
    monkeypatch.setattr(registry, "_mark_requeued", lambda tid: calls.append(("requeued", tid)))
    monkeypatch.setattr(
        registry, "_mark_finished", lambda *a, **k: calls.append(("finished", a, k))
    )

    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
        raise Retry(defer=15)

    runner = registry._wrap("test.retry", handler)
    task_id = uuid.uuid4()
    with pytest.raises(Retry):
        await runner({}, str(task_id), {})

    assert calls == [("running", task_id), ("requeued", task_id)]


@pytest.mark.asyncio
async def test_wrapper_marks_failed_on_cancellation(clean_registry, monkeypatch) -> None:
    """worker 關機時 arq 取消進行中的 task：TaskRecord 要標 failed，不能停在 running。"""
    import asyncio

    calls: list[Any] = []
    monkeypatch.setattr(registry, "_mark_running", lambda tid: None)
    monkeypatch.setattr(
        registry,
        "_mark_finished",
        lambda tid, status, *, result=None, error=None: calls.append((status, error)),
    )

    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
        raise asyncio.CancelledError()

    runner = registry._wrap("test.cancel", handler)
    with pytest.raises(asyncio.CancelledError):
        await runner({}, str(uuid.uuid4()), {})

    assert calls == [(TaskRecordStatus.failed, "CancelledError")]


def test_queue_task_passes_max_tries_and_keep_result(clean_registry) -> None:
    @registry.queue_task("test.opts", timeout_seconds=5, keep_result_seconds=0, max_tries=99)
    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> None:
        return None

    fn = next(f for f in registry.registered_functions() if f.name == "test.opts")
    assert fn.max_tries == 99
    assert fn.keep_result_s == 0


@pytest.mark.asyncio
async def test_local_runner_reruns_after_retry(clean_registry, monkeypatch) -> None:
    """REDIS_ENABLED=false 沒有 arq 重排：本機 runner 自己等 defer 再跑一次。"""
    from arq.worker import Retry

    monkeypatch.setattr(registry, "_mark_running", lambda tid: None)
    monkeypatch.setattr(registry, "_mark_requeued", lambda tid: None)
    monkeypatch.setattr(registry, "_mark_finished", lambda *a, **k: None)
    attempts: list[int] = []

    @registry.queue_task("test.local_retry")
    async def handler(task_id: uuid.UUID, payload: dict[str, Any]) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) < 3:
            raise Retry(defer=0.001)
        return {"ok": True}

    await registry.run_registered_task_locally(
        "test.local_retry", str(uuid.uuid4()), {}
    )

    assert len(attempts) == 3
