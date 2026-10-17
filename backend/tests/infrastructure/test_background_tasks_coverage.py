"""Coverage tests for app.infrastructure.worker.background_tasks."""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from app.infrastructure.worker import background_tasks as bt
from app.infrastructure.worker.background_tasks import BackgroundTaskRunner


async def _drain(runner: BackgroundTaskRunner) -> None:
    for _ in range(50):
        if not runner._tasks:
            return
        await asyncio.sleep(0)
    await asyncio.gather(*list(runner._tasks.values()), return_exceptions=True)


@pytest.fixture(autouse=True)
def _reset_global_runner(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=bt.__name__)
    monkeypatch.setattr(bt, "_runner", None)
    yield
    bt._runner = None


async def test_submit_rejected_when_shutting_down() -> None:
    runner = BackgroundTaskRunner()
    runner._shutting_down = True

    async def work() -> None:
        return None

    coro = work()
    assert runner.submit(coro, name="x") == ""
    # coroutine was closed, awaiting it again raises
    with pytest.raises(RuntimeError):
        await asyncio.gather(coro)


async def test_submit_duplicate_id_is_skipped() -> None:
    runner = BackgroundTaskRunner()
    gate = asyncio.Event()

    async def work() -> None:
        await gate.wait()

    assert runner.submit(work(), task_id="dup") == "dup"
    second = work()
    assert runner.submit(second, task_id="dup") == "dup"
    with pytest.raises(RuntimeError):
        await asyncio.gather(second)
    gate.set()
    await _drain(runner)


async def test_submit_failure_is_logged_and_recorded(caplog) -> None:
    runner = BackgroundTaskRunner()

    async def boom() -> None:
        raise ValueError("bad")

    runner.submit(boom(), name="boom", task_id="b")
    await _drain(runner)
    assert "Background task 'boom' failed" in caplog.text


async def test_submit_factory_rejected_when_shutting_down() -> None:
    runner = BackgroundTaskRunner()
    runner._shutting_down = True
    assert runner.submit_factory(lambda: asyncio.sleep(0), name="n") == ""


async def test_submit_factory_duplicate_is_skipped() -> None:
    runner = BackgroundTaskRunner()
    gate = asyncio.Event()
    calls = []

    def factory():
        calls.append(1)
        return gate.wait()

    assert runner.submit_factory(factory, name="n", task_id="d") == "d"
    assert runner.submit_factory(factory, name="n", task_id="d") == "d"
    await asyncio.sleep(0)
    gate.set()
    await _drain(runner)
    assert calls == [1]


async def test_submit_factory_gives_up_after_max_retries(caplog) -> None:
    runner = BackgroundTaskRunner()
    attempts = []

    async def always_fail() -> None:
        attempts.append(1)
        raise RuntimeError("nope")

    runner.submit_factory(always_fail, name="af", max_retries=1, retry_delay=-1.0)
    await _drain(runner)
    assert len(attempts) == 2
    assert "failed after 2 attempt(s)" in caplog.text


async def test_submit_factory_cancelled_during_attempt(caplog) -> None:
    runner = BackgroundTaskRunner()
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.Event().wait()

    runner.submit_factory(forever, name="fa", task_id="f")
    await started.wait()
    task = runner._tasks["f"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    assert "cancelled (attempt 1)" in caplog.text


async def test_submit_factory_cancelled_while_waiting_for_retry(caplog) -> None:
    runner = BackgroundTaskRunner()
    failed = asyncio.Event()

    async def fail() -> None:
        failed.set()
        raise RuntimeError("x")

    runner.submit_factory(
        fail, name="wait", task_id="w", max_retries=5, retry_delay=3600
    )
    await failed.wait()
    await asyncio.sleep(0)
    task = runner._tasks["w"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(task)
    assert "cancelled while waiting for retry" in caplog.text


async def test_spawn_from_thread_uses_bound_loop() -> None:
    runner = BackgroundTaskRunner()
    runner.bind_loop()
    out = []

    def work() -> None:
        out.append("ran")

    result = {}

    def call() -> None:
        result["tid"] = runner.submit_sync(work, task_id="thr")

    th = threading.Thread(target=call)
    th.start()
    th.join()
    tid = result["tid"]
    assert tid == "thr"
    for _ in range(20):
        await asyncio.sleep(0)
        if "thr" in runner._tasks:
            break
    await _drain(runner)
    assert out == ["ran"]


def test_spawn_with_closed_loop_drops_task() -> None:
    runner = BackgroundTaskRunner()
    loop = asyncio.new_event_loop()
    loop.close()
    runner._loop = loop

    async def work() -> None:
        return None

    coro = work()
    assert runner.submit(coro, name="closed") == ""
    coro.close()


async def test_shutdown_timeout_cancels_late_registered_task() -> None:
    """A task registered after shutdown snapshotted the list is cancelled too."""
    runner = BackgroundTaskRunner()
    started = asyncio.Event()
    late_holder: dict[str, asyncio.Task] = {}

    async def late() -> None:
        await asyncio.Event().wait()

    async def first() -> None:
        await asyncio.sleep(0)
        late_task = asyncio.create_task(late())
        late_holder["t"] = late_task
        runner._tasks["late"] = late_task
        started.set()
        await asyncio.Event().wait()

    runner.submit(first(), task_id="first")
    await runner.shutdown(timeout=0.05)
    assert started.is_set()
    late_task = late_holder["t"]
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(late_task)


def test_close_quietly_swallows_errors() -> None:
    class Bad:
        def close(self) -> None:
            raise RuntimeError("x")

    bt._close_quietly(Bad())  # type: ignore[arg-type]
    bt._close_quietly(object())  # type: ignore[arg-type]


def test_init_background_runner_outside_loop_warns(caplog) -> None:
    runner = bt.init_background_runner(max_concurrency=2)
    assert bt.get_runner() is runner
    assert runner.bound_loop() is None
    assert "outside a running event loop" in caplog.text


async def test_module_helpers_without_runner(caplog) -> None:
    await bt.shutdown_background_runner()

    async def work() -> None:
        return None

    coro = work()
    assert bt.submit(coro, name="x") == ""
    with pytest.raises(RuntimeError):
        await asyncio.gather(coro)

    def fn() -> None:
        return None

    assert bt.submit_sync(fn) == ""
    assert bt.is_active("anything") is False
    assert "Background runner not initialized" in caplog.text


async def test_module_helpers_with_runner() -> None:
    bt.init_background_runner()
    out = []
    gate = asyncio.Event()

    async def work() -> None:
        await gate.wait()
        out.append("a")

    def fn(x) -> None:
        out.append(x)

    assert bt.submit(work(), name="m", task_id="m1") == "m1"
    assert bt.submit_sync(fn, "b", task_id="m2", max_retries=1) == "m2"
    await asyncio.sleep(0)
    assert bt.is_active("m1") is True
    gate.set()
    await bt.shutdown_background_runner(timeout=5)
    assert sorted(out) == ["a", "b"]
