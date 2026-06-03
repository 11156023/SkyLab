"""Executor for Teacher Judge managed script runs."""

from __future__ import annotations

import json
import logging
import shlex
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.ai.teacher_judge.script_policy import validate_managed_script_output
from app.ai.teacher_judge.script_result_analysis_service import (
    analyze_target_results_sync,
    pending_judgement,
)
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.core.db import engine
from app.core.security import decrypt_value
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.ssh import create_key_client, exec_command
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.repositories import resource as resource_repo

logger = logging.getLogger(__name__)

MAX_RUN_TARGETS = 5
MAX_SSH_CONCURRENCY = 5
STDOUT_LIMIT = 16 * 1024
STDERR_LIMIT = 16 * 1024
RAW_RESULT_LIMIT = 256 * 1024
SSH_TIMEOUT_SECONDS = 60
REMOTE_ROOT = "/tmp/campus-cloud-judge"


@dataclass(frozen=True)
class RemoteScriptResult:
    exit_code: int
    result_json_text: str
    stderr_text: str


class TargetExecutionError(RuntimeError):
    """Target-scoped executor error with a stable JSON reason code."""

    def __init__(self, message: str, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _target_vmid(target: dict[str, Any]) -> int:
    return int(target["vmid"])


def _target_resource_type(target: dict[str, Any]) -> str | None:
    value = target.get("resource_type") or target.get("type")
    return str(value) if value is not None else None


def _target_proxmox_node(target: dict[str, Any]) -> str | None:
    value = target.get("proxmox_node") or target.get("node")
    return str(value) if value is not None else None


def _target_user(target: dict[str, Any]) -> dict[str, Any]:
    user = target.get("user")
    if isinstance(user, dict):
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "full_name": user.get("full_name"),
        }
    return {
        "id": target.get("user_id"),
        "email": target.get("email"),
        "full_name": target.get("full_name"),
    }


def _target_metadata(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "vmid": _target_vmid(target),
        "proxmox_node": _target_proxmox_node(target),
        "resource_type": _target_resource_type(target),
        "user": _target_user(target),
        "name": str(target.get("name") or target.get("vmid")),
    }


def _target_progress(
    targets: list[dict[str, Any]],
    statuses: dict[int, str],
) -> list[dict[str, Any]]:
    return [
        {
            **_target_metadata(target),
            "status": statuses.get(_target_vmid(target), "queued"),
            "reason_code": None,
        }
        for target in targets
    ]


def _save_run_progress(
    *,
    session: Session,
    run: TeacherJudgeScriptRun,
    stage: str,
    targets: list[dict[str, Any]],
    statuses: dict[int, str],
    done: int,
) -> None:
    run.progress_json = {
        "stage": stage,
        "total": len(targets),
        "done": done,
        "targets": _target_progress(targets, statuses),
    }
    run.updated_at = _now()
    session.add(run)
    session.commit()
    session.refresh(run)


def _load_run_and_artifact(
    *,
    session: Session,
    run_id: uuid.UUID,
) -> tuple[TeacherJudgeScriptRun, TeacherJudgeScriptArtifact]:
    run = session.get(TeacherJudgeScriptRun, run_id)
    if run is None:
        raise RuntimeError(f"Teacher Judge script run {run_id} not found")
    artifact = session.get(TeacherJudgeScriptArtifact, run.artifact_id)
    if artifact is None:
        raise RuntimeError(f"Teacher Judge script artifact {run.artifact_id} not found")
    return run, artifact


def _live_running_by_vmid() -> dict[int, dict[str, Any]]:
    resources = proxmox_ops.list_all_resources()
    result: dict[int, dict[str, Any]] = {}
    for resource in resources:
        try:
            raw_vmid = resource.get("vmid")
            if raw_vmid is None:
                continue
            vmid = int(raw_vmid)
        except (TypeError, ValueError):
            continue
        result[vmid] = dict(resource)
    return result


def _resolve_runtime_target(
    *,
    session: Session,
    run: TeacherJudgeScriptRun,
    target: dict[str, Any],
    live_by_vmid: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    vmid = _target_vmid(target)
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise TargetExecutionError(
            f"VMID {vmid} 未在資料庫中登記。",
            "missing_db_resource",
        )
    snapshot_user_id = _target_user(target).get("id")
    if str(resource.user_id) != str(snapshot_user_id):
        raise TargetExecutionError(
            f"VMID {vmid} 目前資源擁有者與 run target snapshot 不一致。",
            "owner_mismatch",
        )

    live = live_by_vmid.get(vmid)
    if not live or str(live.get("status") or "") != "running":
        raise TargetExecutionError(f"VMID {vmid} 目前不是運行中。", "not_running")
    if str(live.get("type") or "") not in {"qemu", "lxc"}:
        raise TargetExecutionError(
            f"VMID {vmid} 不是可執行的 VM/LXC。",
            "invalid_resource_type",
        )

    host = resolve_target_ip_address(
        session=session,
        vmid=vmid,
        live_resource=live,
    )
    if not host:
        raise TargetExecutionError(f"VMID {vmid} 沒有可用 IP。", "missing_ip")
    if not resource.ssh_private_key_encrypted:
        raise TargetExecutionError(
            f"VMID {vmid} 沒有可用 SSH 金鑰。",
            "missing_ssh_key",
        )

    return {
        **target,
        "host": host,
        "ssh_user": "root",
        "private_key_pem": decrypt_value(resource.ssh_private_key_encrypted),
        "run_id": str(run.id),
    }


def _execute_target_script(
    *,
    target: dict[str, Any],
    script_content: str,
) -> RemoteScriptResult:
    vmid = _target_vmid(target)
    remote_dir = f"{REMOTE_ROOT}/{target['run_id']}/{vmid}"
    quoted_dir = shlex.quote(remote_dir)
    client = create_key_client(
        str(target["host"]),
        22,
        str(target["ssh_user"]),
        str(target["private_key_pem"]),
        timeout=SSH_TIMEOUT_SECONDS,
    )
    try:
        exit_code, _, stderr = exec_command(
            client,
            f"mkdir -p {quoted_dir}",
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if exit_code != 0:
            return RemoteScriptResult(
                exit_code=exit_code, result_json_text="", stderr_text=stderr
            )

        sftp = client.open_sftp()
        try:
            with sftp.file(f"{remote_dir}/script.py", "wb") as remote_file:
                remote_file.write(script_content.encode())

            exit_code, _, _ = exec_command(
                client,
                f"cd {quoted_dir} && python3 script.py > result.json 2> stderr.log",
                timeout=SSH_TIMEOUT_SECONDS,
            )
            result_json_text = _read_remote_text(sftp, f"{remote_dir}/result.json")
            stderr_text = _read_remote_text(sftp, f"{remote_dir}/stderr.log")
            return RemoteScriptResult(
                exit_code=exit_code,
                result_json_text=result_json_text,
                stderr_text=stderr_text,
            )
        finally:
            sftp.close()
    finally:
        # First version intentionally keeps the remote temp directory for
        # troubleshooting; add cleanup / retention policy before production scale.
        client.close()


def _read_remote_text(sftp: Any, path: str) -> str:
    try:
        with sftp.file(path, "rb") as remote_file:
            data = remote_file.read()
    except OSError:
        return ""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return str(data)


def _target_failure(
    target: dict[str, Any],
    message: str,
    reason_code: str,
) -> dict[str, Any]:
    return {
        **_target_metadata(target),
        "status": "failed",
        "reason_code": reason_code,
        "exit_code": None,
        "validation": {
            "valid": False,
            "error": message,
            "schema_version": "teacher_judge_result.v1",
        },
        "stdout_excerpt": "",
        "stderr_excerpt": _truncate(message, STDERR_LIMIT),
        "raw_result_json": "",
        "parsed_result": None,
    }


def _target_result(
    target: dict[str, Any],
    remote_result: RemoteScriptResult,
) -> dict[str, Any]:
    raw_result = remote_result.result_json_text
    stdout_excerpt = _truncate(raw_result, STDOUT_LIMIT)
    stderr_excerpt = _truncate(remote_result.stderr_text, STDERR_LIMIT)

    validation: dict[str, Any]
    if len(raw_result) > RAW_RESULT_LIMIT:
        validation = {
            "valid": False,
            "error": "result.json 超過 256KB 保存上限。",
            "schema_version": "teacher_judge_result.v1",
        }
        return {
            **_target_metadata(target),
            "status": "failed",
            "reason_code": "result_too_large",
            "exit_code": remote_result.exit_code,
            "validation": validation,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
            "raw_result_json": "",
            "parsed_result": None,
        }

    validation = dict(validate_managed_script_output(raw_result))
    parsed_result = None
    if validation.get("valid"):
        parsed_result = json.loads(raw_result)

    status = (
        "completed"
        if remote_result.exit_code == 0 and validation.get("valid") is True
        else "failed"
    )
    reason_code = "success"
    if status == "failed":
        stderr_lower = remote_result.stderr_text.lower()
        if remote_result.exit_code == 127 or "python3: not found" in stderr_lower:
            reason_code = "python_missing"
        elif remote_result.exit_code != 0:
            reason_code = "execution_nonzero"
        else:
            reason_code = "invalid_json"

    return {
        **_target_metadata(target),
        "status": status,
        "reason_code": reason_code,
        "exit_code": remote_result.exit_code,
        "validation": validation,
        "stdout_excerpt": stdout_excerpt,
        "stderr_excerpt": stderr_excerpt,
        "raw_result_json": raw_result,
        "parsed_result": parsed_result,
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    completed = sum(1 for result in results if result.get("status") == "completed")
    failed = sum(1 for result in results if result.get("status") == "failed")
    valid_json = sum(
        1 for result in results if result.get("validation", {}).get("valid")
    )
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "valid_json": valid_json,
        "invalid_json": total - valid_json,
    }


def _ai_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    completed = 0
    failed = 0
    skipped = 0
    for result in results:
        judgement = result.get("ai_judgement")
        if not isinstance(judgement, dict):
            continue
        status = judgement.get("status")
        if status == "completed":
            completed += 1
        elif status == "failed":
            failed += 1
        elif status == "skipped":
            skipped += 1
    return {
        "ai_completed": completed,
        "ai_failed": failed,
        "ai_skipped": skipped,
    }


def _with_pending_ai_judgement(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for result in results:
        next_result = dict(result)
        validation = next_result.get("validation")
        if isinstance(validation, dict) and validation.get("valid") is True:
            next_result["ai_judgement"] = pending_judgement()
        prepared.append(next_result)
    return prepared


def _mark_run_executor_failed(run_id: uuid.UUID, message: str) -> None:
    with Session(engine) as session:
        run = session.get(TeacherJudgeScriptRun, run_id)
        if run is None:
            return
        targets = list(run.target_snapshot_json.get("targets") or [])
        statuses = {_target_vmid(target): "failed" for target in targets}
        run.status = TeacherJudgeScriptRunStatus.failed
        run.progress_json = {
            "stage": "failed",
            "total": len(targets),
            "done": 0,
            "targets": _target_progress(targets, statuses),
        }
        run.result_summary_json = {"executor_error": message}
        run.finished_at = _now()
        run.updated_at = _now()
        session.add(run)
        session.commit()


def _execute_script_run(run_id: uuid.UUID) -> None:
    """Execute a stored Teacher Judge script run and persist per-target results."""
    with Session(engine) as session:
        run, artifact = _load_run_and_artifact(session=session, run_id=run_id)
        if artifact.status != TeacherJudgeScriptStatus.approved:
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "只有已核准的腳本可以執行。"}
            run.finished_at = _now()
            run.updated_at = _now()
            session.add(run)
            session.commit()
            return

        targets = list(run.target_snapshot_json.get("targets") or [])
        if not targets or len(targets) > MAX_RUN_TARGETS:
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "執行目標數量不合法。"}
            run.finished_at = _now()
            run.updated_at = _now()
            session.add(run)
            session.commit()
            return

        live_by_vmid = _live_running_by_vmid()
        statuses = {_target_vmid(target): "queued" for target in targets}
        run.status = TeacherJudgeScriptRunStatus.running
        run.started_at = run.started_at or _now()
        session.add(run)
        session.commit()
        session.refresh(run)
        _save_run_progress(
            session=session,
            run=run,
            stage="executing",
            targets=targets,
            statuses=statuses,
            done=0,
        )

        runtime_targets: list[dict[str, Any]] = []
        early_results: list[dict[str, Any]] = []
        for target in targets:
            vmid = _target_vmid(target)
            try:
                runtime_targets.append(
                    _resolve_runtime_target(
                        session=session,
                        run=run,
                        target=target,
                        live_by_vmid=live_by_vmid,
                    )
                )
                statuses[vmid] = "running"
            except Exception as exc:
                statuses[vmid] = "failed"
                reason_code = (
                    exc.reason_code
                    if isinstance(exc, TargetExecutionError)
                    else "executor_error"
                )
                early_results.append(_target_failure(target, str(exc), reason_code))

        _save_run_progress(
            session=session,
            run=run,
            stage="executing",
            targets=targets,
            statuses=statuses,
            done=len(early_results),
        )

        results = list(early_results)
        workers = min(MAX_SSH_CONCURRENCY, len(runtime_targets))
        if workers:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_target = {
                    executor.submit(
                        _execute_target_script,
                        target=target,
                        script_content=artifact.script_content,
                    ): target
                    for target in runtime_targets
                }
                for future in as_completed(future_to_target):
                    target = future_to_target[future]
                    vmid = _target_vmid(target)
                    try:
                        target_result = _target_result(target, future.result())
                    except Exception as exc:
                        logger.warning(
                            "Teacher Judge target execution failed run=%s vmid=%s",
                            run_id,
                            vmid,
                            exc_info=True,
                        )
                        target_result = _target_failure(
                            target,
                            str(exc),
                            "executor_error",
                        )
                    statuses[vmid] = str(target_result["status"])
                    results.append(target_result)
                    _save_run_progress(
                        session=session,
                        run=run,
                        stage="executing",
                        targets=targets,
                        statuses=statuses,
                        done=len(results),
                    )

        results.sort(key=lambda item: int(item.get("vmid") or 0))
        results = _with_pending_ai_judgement(results)
        run.target_results_json = {
            "schema_version": "teacher_judge_run_results.v1",
            "targets": results,
        }
        session.add(run)
        session.commit()
        session.refresh(run)
        _save_run_progress(
            session=session,
            run=run,
            stage="analyzing",
            targets=targets,
            statuses=statuses,
            done=len(results),
        )
        results = analyze_target_results_sync(
            rubric_snapshot=artifact.rubric_snapshot_json,
            script_metadata={
                "id": str(artifact.id),
                "name": artifact.name,
                "version": artifact.version,
                "template_key": artifact.template_key,
            },
            target_results=results,
        )
        results.sort(key=lambda item: int(item.get("vmid") or 0))
        run.target_results_json = {
            "schema_version": "teacher_judge_run_results.v1",
            "targets": results,
        }
        run.result_summary_json = {
            **_summary(results),
            **_ai_summary(results),
        }
        run.progress_json = {
            "stage": "completed",
            "total": len(targets),
            "done": len(results),
            "targets": _target_progress(targets, statuses),
        }
        run.status = TeacherJudgeScriptRunStatus.completed
        run.finished_at = _now()
        run.updated_at = _now()
        session.add(run)
        session.commit()


def execute_script_run(run_id: uuid.UUID) -> None:
    """Background task entrypoint that always records executor-level failures."""
    try:
        _execute_script_run(run_id)
    except Exception as exc:
        logger.exception("Teacher Judge script run executor failed run=%s", run_id)
        _mark_run_executor_failed(run_id, str(exc))
