"""Executor for Teacher Judge managed script runs."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from sqlmodel import Session, col, select

from app.ai.teacher_judge.script_policy import validate_managed_script_output
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.core.db import engine
from app.core.security import decrypt_value
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.proxmox.os_detection import is_windows_guest_identity
from app.infrastructure.ssh import create_key_client, exec_command
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teaching_class import TeachingClassMachineNode
from app.repositories import resource as resource_repo
from app.services import os_identity_service

logger = logging.getLogger(__name__)
_WorkerResult = TypeVar("_WorkerResult")

# Class-wide node fan-out may contain more targets than the SSH concurrency
# limit. Keep concurrency bounded, but do not silently cap a run at five VMs.
MAX_RUN_TARGETS: int | None = None
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


def _resource_os_context(resource: Any) -> str:
    identity = getattr(resource, "guest_os", None)
    if isinstance(identity, dict):
        structured = (
            str(identity.get("pretty_name") or "").strip()
            or str(identity.get("id") or "").strip()
        )
        if structured:
            return structured
    return " ".join(
        str(value).strip()
        for value in (
            getattr(resource, "os_info", None),
            getattr(resource, "environment_type", None),
        )
        if value is not None and str(value).strip()
    )


def _is_windows_target(resource: Any) -> bool:
    """結構化 guest_os 為準；無結構資料時退回舊的字串判斷。"""

    structured = is_windows_guest_identity(getattr(resource, "guest_os", None))
    if structured is not None:
        return structured
    os_context = _resource_os_context(resource)
    normalized = os_context.casefold()
    windows_markers = ("windows", "win32", "win64", "win10", "win11", "microsoft")
    return any(marker in normalized for marker in windows_markers)


def _ensure_linux_executor_capability(resource: Any, vmid: int) -> None:
    if _is_windows_target(resource):
        os_context = _resource_os_context(resource)
        raise TargetExecutionError(
            f"VMID {vmid} 的作業系統（{os_context or 'unknown'}）不在目前 Linux SSH/python3 執行器支援範圍。",
            "unsupported_os",
        )


def _target_metadata(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "vmid": _target_vmid(target),
        "student_id": target.get("student_id"),
        "node_key": target.get("node_key"),
        "node_name": target.get("node_name"),
        "node_role": target.get("node_role"),
        "display_label": target.get("display_label"),
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


def _preflight_progress(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "vmid": result.get("vmid"),
            "name": result.get("name"),
            "student_id": result.get("student_id"),
            "node_key": result.get("node_key"),
            "node_name": result.get("node_name"),
            "display_label": result.get("display_label"),
            "proxmox_node": result.get("proxmox_node"),
            "resource_type": result.get("resource_type"),
            "user": result.get("user"),
            "status": result.get("status", "failed"),
            "reason_code": result.get("reason_code"),
        }
        for result in results
    ]


def _save_run_progress(
    *,
    run_id: uuid.UUID,
    stage: str,
    targets: list[dict[str, Any]],
    statuses: dict[int, str],
    done: int,
    preflight_results: list[dict[str, Any]] | None = None,
) -> None:
    with Session(engine) as session:
        run = session.get(TeacherJudgeScriptRun, run_id)
        if run is None:
            return
        run.progress_json = {
            "stage": stage,
            "total": len(targets) + len(preflight_results or []),
            "done": done,
            "targets": _target_progress(targets, statuses)
            + _preflight_progress(preflight_results or []),
        }
        run.updated_at = _now()
        session.add(run)
        session.commit()


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
    # Guest OS 身份補偵測（僅欄位為空時探測一次並回寫；best-effort）
    os_identity_service.ensure_guest_os(
        session=session,
        resource=resource,
        node=str(live.get("node") or "") if live else "",
        resource_type=str(live.get("type") or "") if live else "",
    )
    _ensure_linux_executor_capability(resource, vmid)

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
            with sftp.file(
                f"{remote_dir}/runtime_context.json", "wb"
            ) as remote_context:
                remote_context.write(
                    json.dumps(
                        target.get("runtime_context")
                        or {
                            "schema_version": "teacher_judge_runtime_context.v1",
                            "executor": {"node_key": target.get("node_key")},
                            "peers": {},
                        },
                        ensure_ascii=False,
                    ).encode()
                )

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
        cleanup_command = (
            f"rm -f -- {quoted_dir}/script.py {quoted_dir}/runtime_context.json "
            f"{quoted_dir}/result.json "
            f"{quoted_dir}/stderr.log && rmdir -- {quoted_dir} 2>/dev/null || true"
        )
        try:
            exec_command(client, cleanup_command, timeout=SSH_TIMEOUT_SECONDS)
        except Exception:
            logger.warning(
                "Teacher Judge remote cleanup failed run=%s vmid=%s",
                target["run_id"],
                vmid,
                exc_info=True,
            )
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


# running／pending 超過這個時數且沒有任何進度更新的 run，視為執行器已死
# （行程被 OOM／SIGKILL 殺掉不會走 cancel 路徑，run 會永遠停在 running）
STALE_RUN_HOURS = 2.0
STALE_RUN_MESSAGE = "Executor lost: run reaped after {hours:g}h without progress"


def reap_stale_script_runs(session: Session, *, now: datetime | None = None) -> int:
    """把長時間沒有進度的 pending／running run 標成 failed；回傳處理數。

    正常路徑（完成、cancel、例外）都會收尾，只有行程被硬殺才會留下殭屍；
    回收只看 ``updated_at``，執行中有進度回報就不會被誤殺。
    """
    current = now or _now()
    cutoff = current - timedelta(hours=STALE_RUN_HOURS)
    stale_ids = list(
        session.exec(
            select(TeacherJudgeScriptRun.id)
            .where(
                col(TeacherJudgeScriptRun.status).in_(
                    [
                        TeacherJudgeScriptRunStatus.pending,
                        TeacherJudgeScriptRunStatus.running,
                    ]
                ),
                TeacherJudgeScriptRun.updated_at <= cutoff,
            )
            .limit(20)
        ).all()
    )
    for run_id in stale_ids:
        logger.warning("Reaping stale Teacher Judge script run %s", run_id)
        _mark_run_executor_failed(
            run_id, STALE_RUN_MESSAGE.format(hours=STALE_RUN_HOURS)
        )
    return len(stale_ids)


def _mark_run_executor_failed(run_id: uuid.UUID, message: str) -> None:

    with Session(engine) as session:
        run = session.get(TeacherJudgeScriptRun, run_id)
        if run is None or run.status == TeacherJudgeScriptRunStatus.completed:
            return
        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        statuses = {_target_vmid(target): "failed" for target in targets}
        run.status = TeacherJudgeScriptRunStatus.failed
        run.progress_json = {
            "stage": "failed",
            "total": len(targets) + len(preflight_results),
            "done": len(preflight_results),
            "targets": _target_progress(targets, statuses)
            + _preflight_progress(preflight_results),
        }
        run.result_summary_json = {
            "executor_error": message,
            "preflight_failed": len(preflight_results),
        }
        if preflight_results:
            run.target_results_json = {
                "schema_version": "teacher_judge_run_results.v2",
                "targets": preflight_results,
            }
        run.finished_at = _now()
        run.updated_at = _now()
        session.add(run)
        artifact = session.get(TeacherJudgeScriptArtifact, run.artifact_id)
        if artifact is not None:
            _touch_judge_session(session, artifact)
        session.commit()


def _touch_judge_session(
    session: Session,
    artifact: TeacherJudgeScriptArtifact,
) -> None:
    if artifact.session_id is None:
        return
    judge_session = session.get(TeacherJudgeSession, artifact.session_id)
    if judge_session is None:
        return
    judge_session.last_activity_at = _now()
    judge_session.updated_at = judge_session.last_activity_at
    session.add(judge_session)


@dataclass(frozen=True)
class _ExecutedTargets:
    results: list[dict[str, Any]]


def _execute_targets(run_id: uuid.UUID) -> _ExecutedTargets | None:
    """Own all synchronous target execution and its Sessions in one worker."""
    with Session(engine) as session:
        run, artifact = _load_run_and_artifact(session=session, run_id=run_id)
        if artifact.status != TeacherJudgeScriptStatus.approved:
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "只有已核准的腳本可以執行。"}
            run.finished_at = _now()
            run.updated_at = _now()
            session.add(run)
            _touch_judge_session(session, artifact)
            session.commit()
            return None

        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        if (not targets and not preflight_results) or (
            MAX_RUN_TARGETS is not None and len(targets) > MAX_RUN_TARGETS
        ):
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "執行目標數量不合法。"}
            run.finished_at = _now()
            run.updated_at = _now()
            session.add(run)
            _touch_judge_session(session, artifact)
            session.commit()
            return None

        live_by_vmid = _live_running_by_vmid() if targets else {}
        statuses = {_target_vmid(target): "queued" for target in targets}
        run.status = TeacherJudgeScriptRunStatus.running
        run.started_at = run.started_at or _now()
        session.add(run)
        session.commit()
        session.refresh(run)
        _save_run_progress(
            run_id=run.id,
            stage="executing",
            targets=targets,
            statuses=statuses,
            done=len(preflight_results),
            preflight_results=preflight_results,
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
            run_id=run.id,
            stage="executing",
            targets=targets,
            statuses=statuses,
            done=len(preflight_results) + len(early_results),
            preflight_results=preflight_results,
        )

        results = list(preflight_results) + early_results
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
                        run_id=run.id,
                        stage="executing",
                        targets=targets,
                        statuses=statuses,
                        done=len(results),
                        preflight_results=preflight_results,
                    )

        results.sort(key=lambda item: int(item.get("vmid") or 0))
        _save_run_progress(
            run_id=run.id,
            stage="finalizing",
            targets=targets,
            statuses=statuses,
            done=len(results),
            preflight_results=preflight_results,
        )
        return _ExecutedTargets(results=results)


def _save_results(run_id: uuid.UUID, results: list[dict[str, Any]]) -> None:
    with Session(engine) as session:
        run, artifact = _load_run_and_artifact(session=session, run_id=run_id)
        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        statuses = {
            _target_vmid(result): str(result["status"])
            for result in results
            if result.get("vmid") is not None
        }
        results.sort(key=lambda item: int(item.get("vmid") or 0))
        run.target_results_json = {
            "schema_version": "teacher_judge_run_results.v2",
            "targets": results,
        }
        run.result_summary_json = _summary(results)
        run.progress_json = {
            "stage": "completed",
            "total": len(targets) + len(preflight_results),
            "done": len(results),
            "targets": _target_progress(targets, statuses)
            + _preflight_progress(preflight_results),
        }
        run.status = TeacherJudgeScriptRunStatus.completed
        run.finished_at = _now()
        run.updated_at = _now()
        session.add(run)
        _touch_judge_session(session, artifact)
        session.commit()


async def _await_worker(worker: asyncio.Task[_WorkerResult]) -> _WorkerResult:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling the coroutine cannot stop an SSH thread. Drain the worker
        # before recording failure so it cannot write progress after termination.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


async def _execute_script_run(run_id: uuid.UUID) -> None:
    collected = await _await_worker(
        asyncio.create_task(asyncio.to_thread(_execute_targets, run_id))
    )
    if collected is None:
        return
    await _await_worker(
        asyncio.create_task(asyncio.to_thread(_save_results, run_id, collected.results))
    )


async def execute_script_run(run_id: uuid.UUID) -> None:
    """Background task entrypoint that always records executor-level failures."""
    try:
        await _execute_script_run(run_id)
    except asyncio.CancelledError:
        await _await_worker(
            asyncio.create_task(
                asyncio.to_thread(
                    _mark_run_executor_failed,
                    run_id,
                    "執行流程已中斷；請檢查目標狀態後再重試。",
                )
            )
        )
        raise
    except Exception as exc:
        logger.exception("Teacher Judge script run executor failed run=%s", run_id)
        await _await_worker(
            asyncio.create_task(
                asyncio.to_thread(_mark_run_executor_failed, run_id, str(exc))
            )
        )


def _batch_run_ids(run_batch_id: uuid.UUID) -> list[uuid.UUID]:
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(TeacherJudgeScriptRun, TeacherJudgeScriptArtifact).join(
                    TeacherJudgeScriptArtifact,
                    col(TeacherJudgeScriptArtifact.id)
                    == col(TeacherJudgeScriptRun.artifact_id),
                ).where(
                    TeacherJudgeScriptRun.run_batch_id == run_batch_id
                )
            ).all()
        )
        class_ids = {run.teaching_class_id for run, _artifact in rows}
        node_order: dict[tuple[uuid.UUID, str], tuple[int, str]] = {}
        for class_id in class_ids:
            nodes = session.exec(
                select(TeachingClassMachineNode).where(
                    TeachingClassMachineNode.class_id == class_id
                )
            ).all()
            for node in nodes:
                node_order[(class_id, node.node_key)] = (
                    node.sort_order,
                    node.node_key,
                )
        rows.sort(
            key=lambda row: (
                node_order.get(
                    (
                        row[0].teaching_class_id,
                        str(row[1].target_node_key or ""),
                    ),
                    (10**9, str(row[1].target_node_key or "")),
                ),
                str(row[0].artifact_id),
            )
        )
        return [run.id for run, _artifact in rows]


async def execute_script_run_batch(run_batch_id: uuid.UUID) -> None:
    """Execute child runs sequentially so total SSH concurrency stays bounded."""

    run_ids = await asyncio.to_thread(_batch_run_ids, run_batch_id)
    for run_id in run_ids:
        await execute_script_run(run_id)
