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
