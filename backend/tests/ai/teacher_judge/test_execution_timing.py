"""Timing boundaries and process-wide Teacher Judge SSH admission."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, current_thread
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge.script_run_service import get_script_run_batch_public
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)


def _remote_result() -> str:
    return json.dumps({
        "schema_version": "teacher_judge_result.v1",
        "metadata": {"timestamp": "2026-10-14T00:00:00Z", "platform": "linux"},
        "checks": [], "errors": [],
    })


def test_remote_phases_use_backend_clock_and_include_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.ai.teacher_judge.test_scripts_execution import _FakeClient, _target

    clock = [0.0]
    client = _FakeClient()

    def connect(*args: Any, **kwargs: Any) -> Any:
        clock[0] += 2
        return client

    def command(_client: Any, command: str, **kwargs: Any) -> tuple[int, str, str]:
        clock[0] += 3 if "rm -f" in command else 7 if "python3 script.py" in command else 1
        return 0, "", ""

    def read(_sftp: Any, path: str, limit: int) -> tuple[str, bool]:
        clock[0] += 0.5
        return (_remote_result() if path.endswith("result.json") else ""), False

    monkeypatch.setattr(executor, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(executor, "create_key_client", connect)
    monkeypatch.setattr(executor, "exec_command", command)
    monkeypatch.setattr(executor, "_read_remote_text", read)
    result = executor._execute_scheduled_target(
        target=_target(), script_content="pass", queued_at=-4, on_start=lambda: None,
    )

    assert result["status"] == "completed"
    timing = result["timing"]
    assert timing["queue_seconds"] == 4
    assert timing["prepare_seconds"] == 3
    assert timing["script_seconds"] == 7
    assert timing["download_seconds"] == 1
    assert timing["cleanup_seconds"] == 3
    assert timing["total_seconds"] == 14  # Queue is reported separately.
    assert datetime.fromisoformat(timing["received_at"]).tzinfo is not None


@pytest.mark.parametrize("failure_phase", ["prepare", "script", "download"])
def test_failed_remote_execution_preserves_measured_phases_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, failure_phase: str,
) -> None:
    from tests.ai.teacher_judge.test_scripts_execution import _FakeClient, _target

    clock = [0.0]
    client = _FakeClient()
    closed: list[str] = []
    commands: list[str] = []

    def connect(*args: Any, **kwargs: Any) -> Any:
        clock[0] += 2
        if failure_phase == "prepare":
            raise TimeoutError("synthetic connection timeout")
        return client

    def command(_client: Any, command: str, **kwargs: Any) -> tuple[int, str, str]:
        commands.append(command)
        clock[0] += 3 if "rm -f" in command else 7 if "python3 script.py" in command else 1
        if failure_phase == "script" and "then timeout" in command:
            raise TimeoutError("synthetic script timeout")
        return 0, "", ""

    def read(*args: Any) -> tuple[str, bool]:
        clock[0] += 0.5
        raise TimeoutError("synthetic download timeout")

    monkeypatch.setattr(executor, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(executor, "create_key_client", connect)
    monkeypatch.setattr(executor, "exec_command", command)
    monkeypatch.setattr(executor, "_read_remote_text", read)
    monkeypatch.setattr(client, "close", lambda: closed.append("ssh"))
    monkeypatch.setattr(client.sftp, "close", lambda: closed.append("sftp"))
    result = executor._execute_scheduled_target(
        target=_target(), script_content="pass", queued_at=-4, on_start=lambda: None,
    )
    assert result["reason_code"] == "execution_timeout"
    timing = result["timing"]
    assert timing["queue_seconds"] == 4
    assert "received_at" not in timing
    assert timing["prepare_seconds"] == (2 if failure_phase == "prepare" else 3)
    if failure_phase != "prepare":
        assert timing["script_seconds"] == 7
        assert timing["cleanup_seconds"] == 3
        assert closed == ["sftp", "ssh"]
        assert "rm -f" in commands[-1]
    if failure_phase == "download":
        assert timing["download_seconds"] == 0.5


def test_remote_file_absence_is_optional_but_read_timeout_propagates() -> None:
    class SFTP:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def file(self, *args: Any) -> Any:
            raise self.error

    assert executor._read_remote_text(SFTP(FileNotFoundError()), "missing", 100) == ("", False)
    with pytest.raises(TimeoutError):
        executor._read_remote_text(SFTP(TimeoutError()), "result", 100)
    with pytest.raises(OSError, match="connection lost"):
        executor._read_remote_text(SFTP(OSError("connection lost")), "result", 100)


@pytest.mark.parametrize("command_phase", ["mkdir", "script"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_output_limit_reason_survives_remote_and_scheduled_wrappers(
    monkeypatch: pytest.MonkeyPatch, command_phase: str, stream: str,
) -> None:
    from tests.ai.teacher_judge.test_scripts_execution import _FakeClient, _target

    client = _FakeClient()
    closed: list[str] = []
    commands: list[str] = []

    def command(
        _client: Any, command: str, *, timeout: float, max_output_bytes: int,
    ) -> tuple[int, str, str]:
        commands.append(command)
        assert max_output_bytes == 1024 * 1024
        if (command_phase == "mkdir" and command.startswith("mkdir")) or (
            command_phase == "script" and "then timeout" in command
        ):
            raise executor.SSHOutputTooLargeError(stream, max_output_bytes)
        return 0, "", ""

    monkeypatch.setattr(executor, "create_key_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(executor, "exec_command", command)
    monkeypatch.setattr(client, "close", lambda: closed.append("ssh"))
    monkeypatch.setattr(client.sftp, "close", lambda: closed.append("sftp"))
    result = executor._execute_scheduled_target(
        target=_target(), script_content="pass", queued_at=time.perf_counter(),
        on_start=lambda: None,
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "output_too_large"
    assert "1 MB" in result["validation"]["error"]
    assert result["timing"]["prepare_seconds"] >= 0
    assert result["timing"]["cleanup_seconds"] >= 0
    assert result["timing"]["total_seconds"] >= 0
    assert "received_at" not in result["timing"]
    assert closed == (["sftp", "ssh"] if command_phase == "script" else ["ssh"])
    assert "rm -f" in commands[-1]


def test_missing_remote_timeout_reports_unsupported_deadline() -> None:
    result = executor._target_result({"vmid": 101}, executor.RemoteScriptResult(
        executor.TIMEOUT_UNAVAILABLE_EXIT_CODE, "", "timeout command unavailable",
    ))
    assert result["status"] == "failed"
    assert result["reason_code"] == "timeout_unavailable"


@pytest.mark.parametrize("close_stage", ["sftp", "ssh"])
def test_cleanup_failure_preserves_result_and_attempts_all_cleanup(
    monkeypatch: pytest.MonkeyPatch, close_stage: str,
) -> None:
    from tests.ai.teacher_judge.test_scripts_execution import _FakeClient, _target

    client = _FakeClient()
    closed: list[str] = []
    commands: list[str] = []

    def close(stage: str) -> None:
        closed.append(stage)
        if stage == close_stage:
            raise OSError("synthetic close failure")

    def command(_client: Any, command: str, **kwargs: Any) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(executor, "create_key_client", lambda *args, **kwargs: client)
    monkeypatch.setattr(executor, "exec_command", command)
    monkeypatch.setattr(executor, "_read_remote_text", lambda _sftp, path, limit: (
        _remote_result() if path.endswith("result.json") else "", False,
    ))
    monkeypatch.setattr(client.sftp, "close", lambda: close("sftp"))
    monkeypatch.setattr(client, "close", lambda: close("ssh"))
    result = executor._execute_scheduled_target(
        target=_target(), script_content="pass", queued_at=time.perf_counter(),
        on_start=lambda: None,
    )
    assert result["status"] == "completed"
    assert closed == ["sftp", "ssh"]
    assert "rm -f" in commands[-1]
    assert result["timing"]["cleanup_seconds"] >= 0


def _prepare_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
    run_count: int, targets_per_run: int,
) -> list[uuid.UUID]:
    db_engine = create_engine(f"sqlite:///{tmp_path / 'concurrency.sqlite'}")
    SQLModel.metadata.create_all(db_engine)
    with Session(db_engine) as session:
        artifact = TeacherJudgeScriptArtifact(
            teaching_class_id=uuid.uuid4(), name="concurrency", template_key="linux",
            script_content="pass", status=TeacherJudgeScriptStatus.approved,
        )
        session.add(artifact)
        session.flush()
        runs = [TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            target_snapshot_json={"targets": [
                {"vmid": (index + 1) * 1000 + offset}
                for offset in range(targets_per_run)
            ]},
        ) for index in range(run_count)]
        session.add_all(runs)
        session.commit()
        run_ids = [run.id for run in runs]
    monkeypatch.setattr(executor, "engine", db_engine)
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
    monkeypatch.setattr(executor, "_resolve_runtime_target", lambda **kwargs: {
        **kwargs["target"], "run_id": str(kwargs["run"].id),
        "private_key_pem": "SECRET_MUST_NOT_BE_LOGGED",
    })
    return run_ids


@pytest.mark.parametrize(
    ("run_count", "targets_per_run", "expected_peak"), [(1, 125, 50), (8, 100, 200)],
)
def test_runs_use_fifty_slots_each_and_share_two_hundred_workers(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Any,
    run_count: int, targets_per_run: int, expected_peak: int,
) -> None:
    run_ids = _prepare_runs(monkeypatch, tmp_path, run_count, targets_per_run)
    lock = Lock()
    full = Event()
    release = Event()
    running_reported = Event()
    active = peak = 0
    active_by_run: dict[str, int] = {}
    peak_by_run: dict[str, int] = {}
    started: list[int] = []
    progress: dict[uuid.UUID, list[tuple[str, int]]] = {}
    progress_threads: set[str] = set()

    def save_progress(**kwargs: Any) -> None:
        with lock:
            progress.setdefault(kwargs["run_id"], []).append((kwargs["stage"], kwargs["done"]))
            progress_threads.add(current_thread().name)
            if "running" in kwargs["statuses"].values():
                running_reported.set()

    def ssh(*, target: dict[str, Any], script_content: str) -> executor.RemoteScriptResult:
        nonlocal active, peak
        run_id = target["run_id"]
        with lock:
            active += 1
            peak = max(peak, active)
            active_by_run[run_id] = active_by_run.get(run_id, 0) + 1
            peak_by_run[run_id] = max(peak_by_run.get(run_id, 0), active_by_run[run_id])
            started.append(target["vmid"])
            if active == expected_peak:
                full.set()
        try:
            assert release.wait(15)
            time.sleep(0.002)
            if target["vmid"] % 17 == 0:
                raise TimeoutError("synthetic timeout")
            if target["vmid"] % 19 == 0:
                raise OSError("synthetic SSH failure")
            return executor.RemoteScriptResult(0, _remote_result(), "")
        finally:
            with lock:
                active -= 1
                active_by_run[run_id] -= 1

    executor.shutdown_script_executor()
    monkeypatch.setattr(executor, "_execute_target_script", ssh)
    monkeypatch.setattr(executor, "_save_run_progress", save_progress)
    caplog.set_level(logging.INFO, logger=executor.__name__)
    try:
        with ThreadPoolExecutor(max_workers=run_count) as runs:
            futures = [runs.submit(executor._execute_targets, run_id) for run_id in run_ids]
            try:
                assert full.wait(15)
                assert active == expected_peak
                # Long SSH work still reports progress without a completion event.
                assert running_reported.wait(5)
            finally:
                release.set()
            collected = [future.result(timeout=15) for future in futures]
    finally:
        executor.shutdown_script_executor()
    results = [result for run in collected if run is not None for result in run.results]

    assert peak == expected_peak
    assert max(peak_by_run.values()) == 50
    assert all(value <= 50 for value in peak_by_run.values())
    assert active == 0
    assert len(started) == len(set(started)) == len(results) == run_count * targets_per_run
    assert {result["reason_code"] for result in results} == {
        "success", "execution_timeout", "executor_error",
    }
    assert all(result["timing"]["total_seconds"] >= 0 for result in results)
    assert max(result["timing"]["queue_seconds"] for result in results) >= 0.5
    assert all(rows[-1] == ("finalizing", targets_per_run) for rows in progress.values())
    # DB writes are coalesced by the coordinator, rather than two writes per VM.
    assert all(len(rows) < targets_per_run // 2 for rows in progress.values())
    assert all(not name.startswith("teacher-judge-ssh") for name in progress_threads)
    assert "SECRET_MUST_NOT_BE_LOGGED" not in caplog.text


def test_run_refills_window_before_its_slowest_target_finishes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    run_id = _prepare_runs(monkeypatch, tmp_path, 1, 51)[0]
    release = Event()
    tail_started = Event()

    def ssh(*, target: dict[str, Any], script_content: str) -> executor.RemoteScriptResult:
        if target["vmid"] == 1000:
            assert release.wait(5)
        if target["vmid"] == 1050:
            tail_started.set()
        return executor.RemoteScriptResult(0, _remote_result(), "")

    monkeypatch.setattr(executor, "_execute_target_script", ssh)
    monkeypatch.setattr(executor, "_save_run_progress", lambda **kwargs: None)
    try:
        with ThreadPoolExecutor(max_workers=1) as coordinator:
            future = coordinator.submit(executor._execute_targets, run_id)
            try:
                assert tail_started.wait(5)
                assert not future.done()
            finally:
                release.set()
            result = future.result(timeout=5)
    finally:
        executor.shutdown_script_executor()
    assert result is not None
    assert len(result.results) == 51


async def test_cancellation_stops_queued_and_remaining_targets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    run_id = _prepare_runs(monkeypatch, tmp_path, 1, 75)[0]
    entered = Event()
    release = Event()
    started: list[int] = []

    def ssh(*, target: dict[str, Any], script_content: str) -> executor.RemoteScriptResult:
        started.append(target["vmid"])
        entered.set()
        assert release.wait(5)
        return executor.RemoteScriptResult(0, _remote_result(), "")

    executor.shutdown_script_executor()
    monkeypatch.setattr(executor, "MAX_TOTAL_SSH_CONCURRENCY", 1)
    monkeypatch.setattr(executor, "_execute_target_script", ssh)
    monkeypatch.setattr(executor, "_save_run_progress", lambda **kwargs: None)
    task = asyncio.create_task(executor.execute_script_run(run_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.to_thread(executor.shutdown_script_executor)
    assert started == [1000]
    with Session(executor.engine) as session:
        stored = session.get(TeacherJudgeScriptRun, run_id)
        assert stored is not None
        assert stored.status == TeacherJudgeScriptRunStatus.failed
        assert stored.progress_json["stage"] == "failed"
        assert stored.result_summary_json["timing"]["total_seconds"] >= 0
        assert stored.result_summary_json["timing"]["execution_seconds"] >= 0


def test_progress_failure_drains_running_ssh_before_propagating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    run_id = _prepare_runs(monkeypatch, tmp_path, 1, 1)[0]
    entered = Event()
    release = Event()
    progress_failed = Event()

    def ssh(**kwargs: Any) -> executor.RemoteScriptResult:
        entered.set()
        assert release.wait(5)
        return executor.RemoteScriptResult(0, _remote_result(), "")

    def save_progress(**kwargs: Any) -> None:
        if "running" in kwargs["statuses"].values():
            progress_failed.set()
            raise RuntimeError("synthetic progress failure")

    monkeypatch.setattr(executor, "_execute_target_script", ssh)
    monkeypatch.setattr(executor, "_save_run_progress", save_progress)
    try:
        with ThreadPoolExecutor(max_workers=1) as coordinator:
            future = coordinator.submit(executor._execute_targets, run_id)
            try:
                assert entered.wait(5)
                assert progress_failed.wait(5)
                assert not future.done()
            finally:
                release.set()
            with pytest.raises(RuntimeError, match="synthetic progress failure"):
                future.result(timeout=5)
    finally:
        executor.shutdown_script_executor()


def test_shared_pool_shutdown_drains_workers_and_allows_a_new_lifespan() -> None:
    executor.shutdown_script_executor()
    pool = executor._get_ssh_executor()
    entered = Event()
    release = Event()
    closing = Event()

    def block() -> None:
        entered.set()
        assert release.wait(5)

    def close() -> None:
        closing.set()
        executor.shutdown_script_executor()

    worker = pool.submit(block)
    try:
        with ThreadPoolExecutor(max_workers=1) as coordinator:
            shutdown = coordinator.submit(close)
            try:
                assert entered.wait(5)
                assert closing.wait(5)
                assert not shutdown.done()
            finally:
                release.set()
            shutdown.result(timeout=5)
        assert worker.done()
        restarted = executor._get_ssh_executor()
        assert restarted is not pool
        assert restarted.submit(lambda: "ready").result(timeout=5) == "ready"
    finally:
        release.set()
        executor.shutdown_script_executor()


def test_saved_timing_and_batch_projection_include_later_nodes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    db_engine = create_engine(f"sqlite:///{tmp_path / 'timing.sqlite'}")
    SQLModel.metadata.create_all(db_engine)
    base = datetime(2026, 10, 14, tzinfo=timezone.utc)
    class_id, batch_id = uuid.uuid4(), uuid.uuid4()
    with Session(db_engine) as session:
        artifacts = [TeacherJudgeScriptArtifact(
            teaching_class_id=class_id, target_node_key=node, name=node,
            template_key="linux", script_content="pass", rubric_snapshot_json={},
            status=TeacherJudgeScriptStatus.approved,
        ) for node in ("web", "db")]
        session.add_all(artifacts)
        session.flush()
        first = TeacherJudgeScriptRun(
            artifact_id=artifacts[0].id, run_batch_id=batch_id,
            status=TeacherJudgeScriptRunStatus.running,
            target_snapshot_json={"targets": [{"vmid": 101, "student_id": "s1"}]},
            created_at=base, started_at=base + timedelta(seconds=2),
        )
        second = TeacherJudgeScriptRun(
            artifact_id=artifacts[1].id, run_batch_id=batch_id,
            status=TeacherJudgeScriptRunStatus.completed,
            finished_at=base + timedelta(seconds=30),
        )
        session.add_all([first, second])
        session.commit()
        first_id = first.id
        second_id = second.id

    monkeypatch.setattr(executor, "engine", db_engine)
    monkeypatch.setattr(executor, "_now", lambda: base + timedelta(seconds=12))
    target = executor._target_result({"vmid": 101, "student_id": "s1"},
                                     executor.RemoteScriptResult(0, _remote_result(), ""))
    target["timing"] = {"download_seconds": 1.0,
                        "received_at": (base + timedelta(seconds=8)).isoformat()}
    executor._save_results(first_id, [target])

    with Session(db_engine) as session:
        first = session.get(TeacherJudgeScriptRun, first_id)
        assert first is not None
        assert first.result_summary_json["timing"] == {
            "queue_seconds": 2, "execution_seconds": 10, "total_seconds": 12,
        }
        assert first.result_summary_json["ssh_concurrency_per_run"] == 50
        assert first.result_summary_json["ssh_concurrency_total"] == 200
        assert first.target_results_json["targets"][0]["timing"]["finalize_wait_seconds"] == 4
        batch = get_script_run_batch_public(
            session=session, teaching_class_id=class_id, run_batch_id=batch_id,
        )
        timing = batch.students[0]["nodes"][0]["timing"]
        assert timing["batch_wait_seconds"] == 18
        assert timing["download_seconds"] + timing["finalize_wait_seconds"] + timing["batch_wait_seconds"] == 23
        # Derived projection fields never mutate persisted target timing.
        assert "batch_wait_seconds" not in first.target_results_json["targets"][0]["timing"]
        second = session.get(TeacherJudgeScriptRun, second_id)
        assert second is not None
        second.status = TeacherJudgeScriptRunStatus.running
        second.finished_at = None
        session.add(second)
        session.commit()
        pending_batch = get_script_run_batch_public(
            session=session, teaching_class_id=class_id, run_batch_id=batch_id,
        )
        assert pending_batch.students[0]["nodes"][0]["timing"]["batch_wait_seconds"] is None


def test_missing_timestamps_do_not_invent_elapsed_time() -> None:
    assert executor._seconds_between(None, datetime.now(timezone.utc)) is None
