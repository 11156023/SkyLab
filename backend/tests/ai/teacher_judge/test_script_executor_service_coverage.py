"""Branch coverage for app.ai.teacher_judge.script_executor_service."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session

from app.ai.teacher_judge import script_executor_service as executor
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.repositories import resource as resource_repo
from app.services import os_identity_service
from tests.ai.teacher_judge.helpers import make_session


def _artifact(
    db: Session,
    *,
    status: TeacherJudgeScriptStatus = TeacherJudgeScriptStatus.approved,
    **kwargs: Any,
) -> TeacherJudgeScriptArtifact:
    kwargs.setdefault("teaching_class_id", uuid.uuid4())
    artifact = TeacherJudgeScriptArtifact(
        name="rubric",
        template_key="linux",
        rubric_snapshot_json={},
        script_content="print('x')",
        status=status,
        policy_check_result_json={},
        ai_review_result_json={},
        **kwargs,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def _run(
    db: Session,
    artifact_id: uuid.UUID,
    *,
    status: TeacherJudgeScriptRunStatus = TeacherJudgeScriptRunStatus.pending,
    snapshot: dict[str, Any] | None = None,
    run_batch_id: uuid.UUID | None = None,
) -> TeacherJudgeScriptRun:
    run = TeacherJudgeScriptRun(
        artifact_id=artifact_id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        status=status,
        target_snapshot_json=snapshot or {},
        run_batch_id=run_batch_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@pytest.fixture
def sqlite_db(monkeypatch: pytest.MonkeyPatch) -> Session:
    session = make_session()
    monkeypatch.setattr(executor, "engine", session.get_bind())
    return session


def test_script_run_timeout_seconds_reads_every_timeout_source() -> None:
    snapshot = {
        "items": [
            "not-an-item",
            {
                "check_steps": [
                    {"timeout_seconds": 100},
                    {"parameters": {"timeout_seconds": 200}},
                    {},
                    {"collector": {"type": "file_stat"}},
                    {"collector": {"timeout_seconds": "soon"}},
                    "not-a-step",
                ]
            },
        ]
    }
    # 100 + 200 + 30 (command default) + 0 + 30 (unparseable) + 5 overhead * 5 + 30 margin
    assert executor.script_run_timeout_seconds(snapshot) == 415


def test_load_run_and_artifact_errors(sqlite_db: Session) -> None:
    with pytest.raises(RuntimeError, match="run"):
        executor._load_run_and_artifact(session=sqlite_db, run_id=uuid.uuid4())
    # The run relationship eager-joins its artifact, so an orphan row is only
    # reachable when the session hands back the in-memory instance.
    orphan = TeacherJudgeScriptRun(
        artifact_id=uuid.uuid4(),
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
    )
    sqlite_db.add(orphan)
    sqlite_db.flush()
    with pytest.raises(RuntimeError, match="artifact"):
        executor._load_run_and_artifact(session=sqlite_db, run_id=orphan.id)


def _resource(user_id: uuid.UUID, *, key: str | None = "enc") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        guest_os=None,
        os_info=None,
        environment_type="linux",
        ssh_private_key_encrypted=key,
    )


@pytest.mark.parametrize(
    ("resource", "live", "ip", "reason"),
    [
        (None, {}, "10.0.0.1", "missing_db_resource"),
        ("other-owner", {}, "10.0.0.1", "owner_mismatch"),
        ("owner", {}, "10.0.0.1", "not_running"),
        (
            "owner",
            {"status": "running", "type": "storage"},
            "10.0.0.1",
            "invalid_resource_type",
        ),
        (
            "owner",
            {"status": "running", "type": "lxc", "node": "pve"},
            None,
            "missing_ip",
        ),
        (
            "no-key",
            {"status": "running", "type": "qemu", "node": "pve"},
            "10.0.0.1",
            "missing_ssh_key",
        ),
    ],
)
def test_resolve_runtime_target_failures(
    monkeypatch: pytest.MonkeyPatch,
    resource: str | None,
    live: dict[str, Any],
    ip: str | None,
    reason: str,
) -> None:
    owner = uuid.uuid4()
    resources = {
        None: None,
        "other-owner": _resource(uuid.uuid4()),
        "owner": _resource(owner),
        "no-key": _resource(owner, key=None),
    }
    monkeypatch.setattr(
        resource_repo,
        "get_resource_by_vmid",
        lambda *, session, vmid: resources[resource],
    )
    monkeypatch.setattr(os_identity_service, "ensure_guest_os", lambda **kwargs: None)
    monkeypatch.setattr(executor, "resolve_target_ip_address", lambda **kwargs: ip)
    with pytest.raises(executor.TargetExecutionError) as exc:
        executor._resolve_runtime_target(
            session=None,  # type: ignore[arg-type]
            run=SimpleNamespace(id=uuid.uuid4()),  # type: ignore[arg-type]
            target={"vmid": 101, "user_id": str(owner)},
            live_by_vmid={101: live} if live else {},
        )
    assert exc.value.reason_code == reason


class _FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_execute_target_script_mkdir_failure_and_cleanup_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ticks = iter([10.0, 11.0, 12.0, 13.0])
    monkeypatch.setattr(executor, "perf_counter", lambda: next(ticks))
    client = _FakeClient()
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lambda *args: {"status": "running"})
    monkeypatch.setattr(executor, "create_key_client", lambda *a, **k: client)
    calls: list[str] = []

    def fake_exec(_client, command, *, timeout, max_output_bytes):
        calls.append(command)
        if command.startswith("mkdir"):
            return 1, "", "permission denied"
        raise OSError("connection dropped")

    monkeypatch.setattr(executor, "exec_command", fake_exec)
    with caplog.at_level("WARNING", logger=executor.logger.name):
        result = executor._execute_target_script(
            target={
                "vmid": 101,
                "host": "10.0.0.1",
                "ssh_user": "root",
                "private_key_pem": "KEY",
                "run_id": "run-1",
                "proxmox_node": "pve1",
                "resource_type": "qemu",
            },
            script_content="print(1)",
        )
    assert result == executor.RemoteScriptResult(
        exit_code=1, result_json_text="", stderr_text="permission denied",
        timing={"prepare_seconds": 1.0, "cleanup_seconds": 1.0, "failure_stage": "preparing"},
        reason_code="preparation_failed",
    )
    assert len(calls) == 2
    assert client.closed is True
    assert "remote cleanup failed" in caplog.text


def test_read_remote_text_handles_missing_file_and_text_reads() -> None:
    class _MissingSFTP:
        def file(self, path: str, mode: str) -> Any:
            raise FileNotFoundError("no such file")

    assert executor._read_remote_text(_MissingSFTP(), "/x", 10) == ("", False)

    class _TextFile:
        def __enter__(self) -> _TextFile:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int) -> str:
            return "abcdefghijkl"[:size]

    class _TextSFTP:
        def file(self, path: str, mode: str) -> _TextFile:
            return _TextFile()

    assert executor._read_remote_text(_TextSFTP(), "/x", 5) == ("abcde", True)


def _assert_unstarted_failure_summary(
    run: TeacherJudgeScriptRun, expected: dict[str, Any],
) -> None:
    assert run.finished_at is not None
    assert run.result_summary_json == {
        **expected,
        "ssh_concurrency_per_run": 50,
        "ssh_concurrency_total": 200,
        "timing": {
            "queue_seconds": None,
            "preparation_seconds": None,
            "execution_seconds": None,
            "total_seconds": pytest.approx(
                (run.finished_at - run.created_at).total_seconds(), abs=0.001,
            ),
        },
    }


def test_mark_run_failed_with_missing_judge_session(sqlite_db: Session) -> None:
    artifact = _artifact(sqlite_db, session_id=uuid.uuid4())
    run = _run(sqlite_db, artifact.id, status=TeacherJudgeScriptRunStatus.running)
    executor._mark_run_executor_failed(run.id, "boom")
    sqlite_db.expire_all()
    stored = sqlite_db.get(TeacherJudgeScriptRun, run.id)
    assert stored is not None
    assert stored.status == TeacherJudgeScriptRunStatus.failed


def test_execute_targets_rejects_unapproved_artifact(sqlite_db: Session) -> None:
    artifact = _artifact(sqlite_db, status=TeacherJudgeScriptStatus.draft)
    run = _run(sqlite_db, artifact.id, snapshot={"targets": [{"vmid": 101}]})
    assert executor._execute_targets(run.id) is None
    sqlite_db.expire_all()
    stored = sqlite_db.get(TeacherJudgeScriptRun, run.id)
    assert stored is not None
    assert stored.status == TeacherJudgeScriptRunStatus.failed
    _assert_unstarted_failure_summary(stored, {"error": "只有已核准的腳本可以執行。"})


def test_execute_targets_rejects_empty_targets(sqlite_db: Session) -> None:
    artifact = _artifact(sqlite_db)
    run = _run(sqlite_db, artifact.id)
    assert executor._execute_targets(run.id) is None
    sqlite_db.expire_all()
    stored = sqlite_db.get(TeacherJudgeScriptRun, run.id)
    assert stored is not None
    assert stored.status == TeacherJudgeScriptRunStatus.failed
    _assert_unstarted_failure_summary(stored, {"error": "執行目標數量不合法。"})


def test_execute_targets_records_unexpected_resolution_errors(
    sqlite_db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = _artifact(sqlite_db)
    run = _run(
        sqlite_db, artifact.id, snapshot={"targets": [{"vmid": 101, "name": "vm"}]}
    )
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})

    def explode(**kwargs: object) -> dict[str, Any]:
        raise ValueError("lookup exploded")

    monkeypatch.setattr(executor, "_resolve_runtime_target", explode)

    collected = executor._execute_targets(run.id)

    assert collected is not None
    assert len(collected.results) == 1
    failure = collected.results[0]
    assert failure["reason_code"] == "executor_error"
    assert failure["stderr_excerpt"] == "lookup exploded"


async def test_await_worker_drains_failing_worker_after_cancel() -> None:
    release = asyncio.Event()

    async def work() -> None:
        await release.wait()
        raise RuntimeError("boom")

    worker = asyncio.create_task(work())
    waiter = asyncio.create_task(executor._await_worker(worker))
    await asyncio.sleep(0)
    waiter.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(waiter)
    assert worker.done()
    assert isinstance(worker.exception(), RuntimeError)
