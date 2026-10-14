"""Executor for Teacher Judge managed script runs."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from threading import Event, Lock
from time import perf_counter
from typing import Any, TypeVar, cast

from sqlalchemy import exists, func, or_
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, select

from app.ai.teacher_judge.run_record_service import (
    TERMINAL_TARGET_STATUSES,
    fail_unfinished_targets,
    initialize_targets,
    seconds_between,
    store_target_result,
    target_key,
    update_batch,
)
from app.ai.teacher_judge.script_policy import (
    RAW_RESULT_LIMIT,
    normalize_managed_script_checks,
    validate_managed_script_output,
)
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.ai.teacher_judge.target_os import is_windows_target, resource_os_context
from app.core.db import engine
from app.core.security import decrypt_value
from app.infrastructure.proxmox import operations as proxmox_ops
from app.infrastructure.ssh import (
    SSHOutputTooLargeError,
    create_key_client,
    exec_command,
)
from app.models.base import get_datetime_utc as _now
from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
from app.models.teacher_judge_run_target import TeacherJudgeRunTarget
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

# Shared workers cap all runs in this backend process. Each run submits only a
# rolling window so a large run cannot occupy the pool's entire pending queue.
MAX_SSH_CONCURRENCY_PER_RUN = 50
MAX_TOTAL_SSH_CONCURRENCY = 200
PROGRESS_UPDATE_INTERVAL_SECONDS = 1.0
_ssh_executor: ThreadPoolExecutor | None = None
_ssh_executor_lock = Lock()
STDOUT_LIMIT = 16 * 1024
STDERR_LIMIT = 16 * 1024
SSH_TIMEOUT_SECONDS = 60
# 目標機是學生自己有 root 的機器：shell 啟動檔等可以吐出任意多的輸出。執行器的
# 指令本身幾乎沒有輸出（腳本結果都寫進檔案），超過就判這台失敗。
SSH_OUTPUT_LIMIT_BYTES = 1024 * 1024
REMOTE_ROOT = "/tmp/campus-cloud-judge"
# 腳本執行的時間預算：依 Check Plan 各步驟的 collector timeout 加總，再加上
# 餘裕並設上限。腳本的輸出都導向檔案，SSH channel 在腳本結束前完全沒有資料，
# 所以這條 SSH 指令的總時限必須大於整支腳本的執行時間，不能用固定的 60 秒。
_DEFAULT_STEP_TIMEOUTS = {"command": 30, "localhost_http": 10, "peer_ping": 10}
_STEP_OVERHEAD_SECONDS = 5
RUN_TIMEOUT_MARGIN_SECONDS = 30
MAX_RUN_TIMEOUT_SECONDS = 900
# GNU timeout 逾時的結束碼
TIMEOUT_EXIT_CODE = 124
TIMEOUT_UNAVAILABLE_EXIT_CODE = 125
_ACTIVE_RUN_STATUSES = (
    TeacherJudgeScriptRunStatus.pending,
    TeacherJudgeScriptRunStatus.running,
)
# 改 run 狀態的路徑（接手執行、寫進度、寫結果、標失敗）都先鎖住該列，彼此不會
# 在「讀狀態 → 寫回」之間互相覆蓋；只鎖 run 本身，不連帶鎖 eager load 的 artifact
_RUN_ROW_LOCK: dict[str, Any] = {"of": TeacherJudgeScriptRun}


def _get_ssh_executor() -> ThreadPoolExecutor:
    global _ssh_executor
    with _ssh_executor_lock:
        if _ssh_executor is None:
            _ssh_executor = ThreadPoolExecutor(
                max_workers=MAX_TOTAL_SSH_CONCURRENCY,
                thread_name_prefix="teacher-judge-ssh",
            )
        return _ssh_executor


def shutdown_script_executor() -> None:
    """Drain SSH workers after the background runner has stopped accepting work."""
    global _ssh_executor
    with _ssh_executor_lock:
        if _ssh_executor is not None:
            _ssh_executor.shutdown(wait=True)
            _ssh_executor = None


@dataclass(frozen=True)
class RemoteScriptResult:
    exit_code: int
    result_json_text: str
    stderr_text: str
    result_too_large: bool = False
    timing: dict[str, Any] = field(default_factory=dict)


class TargetExecutionError(RuntimeError):
    """Target-scoped executor error with a stable JSON reason code."""

    def __init__(
        self, message: str, reason_code: str, *, timing: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.timing = timing if timing is not None else {}


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def _target_vmid(target: dict[str, Any]) -> int:
    return int(target["vmid"])


def _step_timeout_seconds(step: dict[str, Any]) -> int:
    collector = step.get("collector")
    collector = collector if isinstance(collector, dict) else {}
    parameters = step.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    raw = collector.get("timeout_seconds")
    if raw is None:
        raw = step.get("timeout_seconds")
    if raw is None:
        raw = parameters.get("timeout_seconds")
    if raw is None:
        collector_type = str(collector.get("type") or "command")
        return _DEFAULT_STEP_TIMEOUTS.get(collector_type, 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_STEP_TIMEOUTS["command"]


def script_run_timeout_seconds(rubric_snapshot: dict[str, Any] | None) -> int:
    """Wall-clock budget for one compiled script, derived from its plan steps."""

    total = 0
    items = (rubric_snapshot or {}).get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        for step in item.get("check_steps") or []:
            if isinstance(step, dict):
                total += _step_timeout_seconds(step) + _STEP_OVERHEAD_SECONDS
    return max(
        SSH_TIMEOUT_SECONDS,
        min(MAX_RUN_TIMEOUT_SECONDS, total + RUN_TIMEOUT_MARGIN_SECONDS),
    )


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


def _ensure_linux_executor_capability(resource: Any, vmid: int) -> None:
    if is_windows_target(resource):
        os_context = resource_os_context(resource)
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


def preflight_progress(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Progress rows for targets that already failed preflight.

    Also used by script_run_service when it creates the pending run.
    """

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
    results: list[dict[str, Any]] | None = None,
    target_updates: dict[int, dict[str, Any]] | None = None,
) -> None:
    with Session(engine) as session:
        run = session.get(
            TeacherJudgeScriptRun, run_id, with_for_update=_RUN_ROW_LOCK,
            options=[selectinload(cast(Any, TeacherJudgeScriptRun.target_records)).defer(
                cast(Any, TeacherJudgeRunTarget.result_json),
            )],
        )
        # 執行中途被回收（或已收尾）的 run 不再寫進度，以免蓋掉 failed 的狀態
        if run is None or run.status != TeacherJudgeScriptRunStatus.running:
            return
        initialize_targets(session, run)
        records = {row.target_key: row for row in run.target_records}
        for result in results or []:
            result_row = records.get(target_key(result))
            if result_row is not None and result_row.status not in TERMINAL_TARGET_STATUSES:
                store_target_result(result_row, result)
        for target in targets:
            row = records[target_key(target)]
            update = (target_updates or {}).get(_target_vmid(target), {})
            for key in ("queued_at", "started_at"):
                if key in update and getattr(row, key) is None:
                    setattr(row, key, update[key])
            if row.status in TERMINAL_TARGET_STATUSES:
                continue
            row.status = statuses.get(_target_vmid(target), "queued")
            row.stage = str(update.get("stage") or row.stage)
            row.updated_at = _now()
        run.progress_json = {
            "stage": stage,
            "total": len(targets) + len(preflight_results or []),
            "done": done,
            "targets": _target_progress(targets, statuses)
            + preflight_progress(preflight_results or []),
        }
        run.updated_at = _now()
        session.add(run)
        update_batch(session, run.run_batch_id)
        session.commit()


def _load_run_and_artifact(
    *,
    session: Session,
    run_id: uuid.UUID,
    for_update: bool = False,
) -> tuple[TeacherJudgeScriptRun, TeacherJudgeScriptArtifact]:
    run = session.get(
        TeacherJudgeScriptRun,
        run_id,
        with_for_update=_RUN_ROW_LOCK if for_update else None,
    )
    if run is None:
        raise RuntimeError(f"Teacher Judge script run {run_id} not found")
    artifact = session.get(TeacherJudgeScriptArtifact, run.artifact_id)
    if artifact is None:
        raise RuntimeError(f"Teacher Judge script artifact {run.artifact_id} not found")
    return run, artifact


def _live_running_by_vmid() -> dict[int, dict[str, Any]]:
    """Every pool resource keyed by VMID; callers check ``status`` themselves."""

    return proxmox_ops.list_all_resources_by_vmid()


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


def _target_phase(target: dict[str, Any], stage: str) -> None:
    callback = target.get("_on_phase")
    if callback is not None:
        callback(stage)


def _execute_target_script(
    *,
    target: dict[str, Any],
    script_content: str,
) -> RemoteScriptResult:
    started = perf_counter()
    timing: dict[str, Any] = {}
    _target_phase(target, "preparing")
    phase, phase_started = "prepare_seconds", started
    vmid = _target_vmid(target)
    remote_dir = f"{REMOTE_ROOT}/{target['run_id']}/{vmid}"
    quoted_dir = shlex.quote(remote_dir)
    client = None
    sftp = None
    try:
        client = create_key_client(
            str(target["host"]),
            22,
            str(target["ssh_user"]),
            str(target["private_key_pem"]),
            timeout=SSH_TIMEOUT_SECONDS,
        )
        exit_code, _, stderr = exec_command(
            client,
            f"mkdir -p {quoted_dir}",
            timeout=SSH_TIMEOUT_SECONDS,
            max_output_bytes=SSH_OUTPUT_LIMIT_BYTES,
        )
        if exit_code != 0:
            timing["prepare_seconds"] = round(perf_counter() - started, 3)
            return RemoteScriptResult(
                exit_code=exit_code, result_json_text="", stderr_text=stderr,
                timing=timing,
            )

        sftp = client.open_sftp()
        # 目標機是學生自己有 root 的機器：SFTP 讀寫卡住時不能讓執行緒永遠等下去
        sftp.get_channel().settimeout(SSH_TIMEOUT_SECONDS)
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

        run_timeout = int(
            target.get("run_timeout_seconds") or SSH_TIMEOUT_SECONDS
        )
        # timeout(1) 真的停掉逾時的腳本（不然刪掉檔案後 python 仍在背景跑）；
        # 缺少遠端 deadline 時不執行，避免 channel 逾時後腳本仍在背景跑。
        script_started = perf_counter()
        _target_phase(target, "executing")
        timing["prepare_seconds"] = round(script_started - started, 3)
        phase, phase_started = "script_seconds", script_started
        exit_code, _, _ = exec_command(
            client,
            f"cd {quoted_dir} && "
            "if command -v timeout >/dev/null 2>&1; "
            f"then timeout -k 5 {run_timeout} python3 script.py; "
            f"else echo 'timeout command unavailable' >&2; exit {TIMEOUT_UNAVAILABLE_EXIT_CODE}; "
            "fi > result.json 2> stderr.log",
            timeout=run_timeout + 15,
            max_output_bytes=SSH_OUTPUT_LIMIT_BYTES,
        )
        download_started = perf_counter()
        timing["script_finished_at"] = _now().isoformat()
        _target_phase(target, "downloading")
        timing["script_seconds"] = round(download_started - script_started, 3)
        phase, phase_started = "download_seconds", download_started
        result_json_text, result_too_large = _read_remote_text(
            sftp, f"{remote_dir}/result.json", RAW_RESULT_LIMIT
        )
        stderr_text, stderr_too_large = _read_remote_text(
            sftp, f"{remote_dir}/stderr.log", STDERR_LIMIT
        )
        if stderr_too_large:
            stderr_text += "\n...[truncated]"
        timing["download_seconds"] = round(perf_counter() - download_started, 3)
        timing["received_at"] = _now().isoformat()
        return RemoteScriptResult(
            exit_code=exit_code,
            result_json_text=result_json_text,
            stderr_text=stderr_text,
            result_too_large=result_too_large,
            timing=timing,
        )
    except Exception as exc:
        timing[phase] = round(perf_counter() - phase_started, 3)
        timing["failure_stage"] = {
            "prepare_seconds": "preparing", "script_seconds": "executing", "download_seconds": "downloading",
        }[phase]
        raise TargetExecutionError(
            str(exc),
            _target_exception_reason(exc),
            timing=timing,
        ) from exc
    finally:
        cleanup_started = perf_counter()
        _target_phase(target, "cleanup")
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                logger.warning(
                    "Teacher Judge SFTP close failed run=%s vmid=%s",
                    target["run_id"], vmid, exc_info=True,
                )
        cleanup_command = (
            f"rm -f -- {quoted_dir}/script.py {quoted_dir}/runtime_context.json "
            f"{quoted_dir}/result.json "
            f"{quoted_dir}/stderr.log && rmdir -- {quoted_dir} 2>/dev/null || true"
        )
        try:
            if client is not None:
                exec_command(
                    client,
                    cleanup_command,
                    timeout=SSH_TIMEOUT_SECONDS,
                    max_output_bytes=SSH_OUTPUT_LIMIT_BYTES,
                )
        except Exception:
            logger.warning(
                "Teacher Judge remote cleanup failed run=%s vmid=%s",
                target["run_id"],
                vmid,
                exc_info=True,
            )
        try:
            if client is not None:
                client.close()
        except Exception:
            logger.warning(
                "Teacher Judge SSH close failed run=%s vmid=%s",
                target["run_id"], vmid, exc_info=True,
            )
        finally:
            timing["cleanup_seconds"] = round(perf_counter() - cleanup_started, 3)


def _execute_scheduled_target(
    *,
    target: dict[str, Any],
    script_content: str,
    queued_at: float,
    on_start: Callable[[], None],
    stop_event: Event | None = None,
) -> dict[str, Any]:
    """Time targets admitted by the shared pool, including failed executions."""
    started = perf_counter()
    timing: dict[str, Any] = {
        "queue_seconds": round(started - queued_at, 3),
        "started_at": _now().isoformat(),
    }
    if stop_event is not None and stop_event.is_set():
        result = _target_failure(target, "執行流程已中斷。", "executor_cancelled")
        result["timing"] = {**timing, "total_seconds": 0.0, "finished_at": _now().isoformat()}
        return result
    on_start()
    try:
        remote = _execute_target_script(target=target, script_content=script_content)
        timing.update(remote.timing)
        _target_phase(target, "validating")
        validation_started = perf_counter()
        result = _target_result(target, remote)
        timing["validation_seconds"] = round(perf_counter() - validation_started, 3)
    except Exception as exc:
        logger.warning(
            "Teacher Judge target execution failed run=%s vmid=%s",
            target.get("run_id"), _target_vmid(target), exc_info=True,
        )
        if isinstance(exc, TargetExecutionError):
            timing.update(exc.timing)
        result = _target_failure(
            target, str(exc),
            _target_exception_reason(exc),
        )
    timing["total_seconds"] = round(perf_counter() - started, 3)
    timing["finished_at"] = _now().isoformat()
    if result["status"] == "failed":
        result["failure_stage"] = timing.pop("failure_stage", None) or (
            "validating" if result["reason_code"] in {"invalid_json", "result_too_large"} else "executing"
        )
    result["timing"] = timing
    logger.info(
        "Teacher Judge target finished run=%s vmid=%s status=%s timing=%s",
        target.get("run_id"), _target_vmid(target), result["status"],
        json.dumps(timing),
    )
    return result


def _read_remote_text(sftp: Any, path: str, limit: int) -> tuple[str, bool]:
    """Read at most ``limit`` bytes; the flag says the file was larger.

    The file lives on a student-controlled machine (it can be huge or a link
    to /dev/zero), so never read to EOF.
    """

    try:
        with sftp.file(path, "rb") as remote_file:
            data = remote_file.read(limit + 1)
    except FileNotFoundError:
        return "", False
    if not isinstance(data, bytes):
        data = str(data).encode(errors="replace")
    too_large = len(data) > limit
    return data[:limit].decode(errors="replace"), too_large


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
    if remote_result.result_too_large or len(raw_result) > RAW_RESULT_LIMIT:
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
        parsed_result = normalize_managed_script_checks(json.loads(raw_result))
    else:
        # 完整驗證錯誤只進 log 與老師端 validation.error，學生頁由
        # ai_assignment_service 換成一句通用說明
        logger.warning(
            "Teacher Judge script output invalid run=%s vmid=%s exit_code=%s error=%s",
            target.get("run_id"),
            _target_vmid(target),
            remote_result.exit_code,
            validation.get("error"),
        )

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
        elif remote_result.exit_code == TIMEOUT_EXIT_CODE:
            reason_code = "execution_timeout"
        elif remote_result.exit_code == TIMEOUT_UNAVAILABLE_EXIT_CODE:
            reason_code = "timeout_unavailable"
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
    回收只看 ``updated_at``，執行中有進度回報就不會被誤殺。批次裡的子 run 是
    一個接一個跑，排隊中的子 run 自己不會有進度，改看整個批次最近的進度：
    同批次還有 run 在更新就不回收。
    """
    current = now or _now()
    cutoff = current - timedelta(hours=STALE_RUN_HOURS)
    active_batch_ids = (
        select(TeacherJudgeScriptRun.run_batch_id)
        .where(
            col(TeacherJudgeScriptRun.run_batch_id).is_not(None),
            TeacherJudgeScriptRun.updated_at > cutoff,
            or_(
                col(TeacherJudgeScriptRun.status) != TeacherJudgeScriptRunStatus.failed,
                func.coalesce(TeacherJudgeScriptRun.result_summary_json["executor_reason"].as_string(), "") != "executor_lost",
            ),
        )
        .correlate(None)
    )
    stale_ids = list(
        session.exec(
            select(TeacherJudgeScriptRun.id)
            .where(
                col(TeacherJudgeScriptRun.status).in_(_ACTIVE_RUN_STATUSES),
                TeacherJudgeScriptRun.updated_at <= cutoff,
                or_(
                    col(TeacherJudgeScriptRun.run_batch_id).is_(None),
                    col(TeacherJudgeScriptRun.run_batch_id).not_in(active_batch_ids),
                ),
            )
            .limit(20)
        ).all()
    )
    for run_id in stale_ids:
        logger.warning("Reaping stale Teacher Judge script run %s", run_id)
        _mark_run_executor_failed(
            run_id,
            STALE_RUN_MESSAGE.format(hours=STALE_RUN_HOURS),
            stale_before=cutoff,
        )
    # A process can die after accepting a batch but before committing any child
    # runs. Such attempts must not remain pending forever.
    orphan_batches = session.exec(select(TeacherJudgeRunBatch).where(
        TeacherJudgeRunBatch.status == "pending",
        col(TeacherJudgeRunBatch.prepared_at).is_(None),
        col(TeacherJudgeRunBatch.requested_at).is_not(None),
        TeacherJudgeRunBatch.updated_at <= cutoff,
        ~exists(select(TeacherJudgeScriptRun.id).where(
            col(TeacherJudgeScriptRun.run_batch_id) == col(TeacherJudgeRunBatch.id),
        )),
    ).limit(20).with_for_update(skip_locked=True)).all()
    for batch in orphan_batches:
        batch.status = "failed"
        batch.finished_at = current
        batch.updated_at = current
        batch.error_message = STALE_RUN_MESSAGE.format(hours=STALE_RUN_HOURS)
        session.add(batch)
    if orphan_batches:
        session.commit()
    return len(stale_ids) + len(orphan_batches)


def _mark_run_executor_failed(
    run_id: uuid.UUID, message: str, *, stale_before: datetime | None = None,
    reason_code: str = "executor_error",
) -> None:
    """把 run 標成 failed；``stale_before`` 有值時只處理到那時仍無進度的 run。"""

    with Session(engine) as session:
        statement = select(TeacherJudgeScriptRun).where(
            TeacherJudgeScriptRun.id == run_id
        )
        if stale_before is not None:
            # 回收的 SELECT 之後才被執行器接手（有了新進度）的 run 不能收掉
            statement = statement.where(
                col(TeacherJudgeScriptRun.status).in_(_ACTIVE_RUN_STATUSES),
                TeacherJudgeScriptRun.updated_at <= stale_before,
            )
        run = session.exec(statement.with_for_update(**_RUN_ROW_LOCK)).first()
        if run is None or run.status not in _ACTIVE_RUN_STATUSES:
            return
        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        initialize_targets(session, run)
        finished_at = _now()
        fail_unfinished_targets(run, message, "executor_lost" if stale_before else reason_code, finished_at)
        results = run.results_document.get("targets") or []
        statuses = {int(result["vmid"]): str(result["status"]) for result in results if result.get("vmid") is not None}
        run.status = TeacherJudgeScriptRunStatus.failed
        run.progress_json = {
            "stage": "failed",
            "total": len(targets) + len(preflight_results),
            "done": len(results),
            "targets": _target_progress(targets, statuses)
            + preflight_progress(preflight_results),
        }
        run.result_summary_json = {
            **_summary(results),
            "executor_error": message,
            "executor_reason": "executor_lost" if stale_before else reason_code,
            "preflight_failed": len(preflight_results),
        }
        run.finished_at = finished_at
        run.result_summary_json = {
            **run.result_summary_json,
            **_run_execution_summary(run, run.finished_at),
        }
        run.updated_at = run.finished_at
        session.add(run)
        artifact = session.get(TeacherJudgeScriptArtifact, run.artifact_id)
        if artifact is not None:
            _touch_judge_session(session, artifact)
        update_batch(session, run.run_batch_id)
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


def _target_exception_reason(exc: BaseException) -> str:
    # 逾時／輸出過大只讓這一台失敗，訊息來自 SSH 層（含秒數或上限）
    if isinstance(exc, TargetExecutionError):
        return exc.reason_code
    if isinstance(exc, TimeoutError):
        return "execution_timeout"
    if isinstance(exc, SSHOutputTooLargeError):
        return "output_too_large"
    return "executor_error"


@dataclass(frozen=True)
class _ExecutedTargets:
    results: list[dict[str, Any]]


def _execute_targets(
    run_id: uuid.UUID, *, stop_event: Event | None = None,
) -> _ExecutedTargets | None:
    """Own all synchronous target execution and its Sessions in one worker."""
    with Session(engine) as session:
        if stop_event is not None and stop_event.is_set():
            return None
        run, artifact = _load_run_and_artifact(
            session=session, run_id=run_id, for_update=True
        )
        if run.status != TeacherJudgeScriptRunStatus.pending:
            # 排隊期間已被回收（或已執行、已收尾）的 run 不再執行，也不覆寫它
            logger.warning(
                "Skipping Teacher Judge script run %s: status is already %s",
                run_id,
                run.status.value,
            )
            return None
        if artifact.status != TeacherJudgeScriptStatus.approved:
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "只有已核准的腳本可以執行。"}
            run.finished_at = _now()
            run.result_summary_json = {
                **run.result_summary_json,
                **_run_execution_summary(run, run.finished_at),
            }
            run.updated_at = _now()
            initialize_targets(session, run)
            fail_unfinished_targets(run, str(run.result_summary_json["error"]), "artifact_not_approved", run.finished_at)
            session.add(run)
            _touch_judge_session(session, artifact)
            update_batch(session, run.run_batch_id)
            session.commit()
            return None

        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        if not targets and not preflight_results:
            run.status = TeacherJudgeScriptRunStatus.failed
            run.result_summary_json = {"error": "執行目標數量不合法。"}
            run.finished_at = _now()
            run.result_summary_json = {
                **run.result_summary_json,
                **_run_execution_summary(run, run.finished_at),
            }
            run.updated_at = _now()
            session.add(run)
            _touch_judge_session(session, artifact)
            update_batch(session, run.run_batch_id)
            session.commit()
            return None

        statuses = {_target_vmid(target): "queued" for target in targets}
        run.status = TeacherJudgeScriptRunStatus.running
        run.started_at = run.started_at or _now()
        initialize_targets(session, run)
        session.add(run)
        update_batch(session, run.run_batch_id)
        session.commit()
        session.refresh(run)
        _save_run_progress(
            run_id=run_id,
            stage="executing",
            targets=targets,
            statuses=statuses,
            done=len(preflight_results),
            preflight_results=preflight_results,
        )
        live_by_vmid = _live_running_by_vmid() if targets else {}

        # 先把 SSH 階段要用的值複製成區域變數：下面會 commit 並關掉這個
        # Session，SSH fan-out 期間不能佔著交易（PgBouncer transaction
        # pooling 下會一路釘住一條 server 連線）。
        script_content = artifact.script_content
        run_timeout_seconds = script_run_timeout_seconds(artifact.rubric_snapshot_json)
        runtime_targets: list[dict[str, Any]] = []
        early_results: list[dict[str, Any]] = []
        for target in targets:
            if stop_event is not None and stop_event.is_set():
                session.commit()
                return None
            vmid = _target_vmid(target)
            try:
                runtime_target = _resolve_runtime_target(
                    session=session,
                    run=run,
                    target=target,
                    live_by_vmid=live_by_vmid,
                )
                runtime_targets.append(
                    {**runtime_target, "run_timeout_seconds": run_timeout_seconds}
                )
            except Exception as exc:
                statuses[vmid] = "failed"
                reason_code = (
                    exc.reason_code
                    if isinstance(exc, TargetExecutionError)
                    else "executor_error"
                )
                failure = _target_failure(target, str(exc), reason_code)
                failure["failure_stage"] = "resolving_target"
                failure["timing"] = {"finished_at": _now().isoformat()}
                early_results.append(failure)
        # 收掉解析目標時開的交易（含 ensure_guest_os 只 flush 的補偵測結果）
        session.commit()

    _save_run_progress(
        run_id=run_id,
        stage="executing",
        targets=targets,
        statuses=statuses,
        done=len(preflight_results) + len(early_results),
        preflight_results=preflight_results,
        results=early_results,
    )

    results = list(preflight_results) + early_results
    progress_lock = Lock()
    target_updates: dict[int, dict[str, Any]] = {}

    def mark_started(target: dict[str, Any]) -> None:
        with progress_lock:
            statuses[_target_vmid(target)] = "running"
            target_updates.setdefault(_target_vmid(target), {}).update(
                stage="preparing", started_at=_now(),
            )

    def mark_phase(target: dict[str, Any], stage: str) -> None:
        with progress_lock:
            target_updates.setdefault(_target_vmid(target), {})["stage"] = stage

    if runtime_targets:
        executor = _get_ssh_executor()
        remaining = iter(runtime_targets)
        pending: dict[Future[dict[str, Any]], dict[str, Any]] = {}
        # Include waiting behind this run's window as well as the shared pool.
        queued_at = perf_counter()
        queued_timestamp = _now()
        target_updates = {
            _target_vmid(target): {"stage": "queued", "queued_at": queued_timestamp}
            for target in runtime_targets
        }
        last_progress_at = queued_at

        def submit_next() -> bool:
            if stop_event is not None and stop_event.is_set():
                return False
            target = next(remaining, None)
            if target is None:
                return False
            future = executor.submit(
                _execute_scheduled_target,
                target={**target, "_on_phase": partial(mark_phase, target)},
                script_content=script_content,
                queued_at=queued_at,
                on_start=partial(mark_started, target),
                stop_event=stop_event,
            )
            pending[future] = target
            return True

        interrupted = False
        try:
            for _ in range(MAX_SSH_CONCURRENCY_PER_RUN):
                if not submit_next():
                    break
            while pending:
                if stop_event is not None and stop_event.is_set():
                    for future in list(pending):
                        if future.cancel():
                            pending.pop(future)
                    if not pending:
                        break
                finished, _ = wait(
                    pending,
                    timeout=PROGRESS_UPDATE_INTERVAL_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                for future in finished:
                    target = pending.pop(future)
                    target_result = future.result()
                    results.append(target_result)
                    with progress_lock:
                        statuses[_target_vmid(target)] = str(target_result["status"])
                    submit_next()

                current = perf_counter()
                if current - last_progress_at >= PROGRESS_UPDATE_INTERVAL_SECONDS:
                    with progress_lock:
                        snapshot = dict(statuses)
                        updates = {key: dict(value) for key, value in target_updates.items()}
                    _save_run_progress(
                        run_id=run_id,
                        stage="executing",
                        targets=targets,
                        statuses=snapshot,
                        done=len(results),
                        preflight_results=preflight_results,
                        results=results,
                        target_updates=updates,
                    )
                    last_progress_at = current
        except BaseException:
            interrupted = True
            if stop_event is not None:
                stop_event.set()
            raise
        finally:
            # On coordinator failure, stop queued work and drain running SSH
            # before the caller marks failure; no progress may arrive afterward.
            running = [future for future in pending if not future.cancel()]
            wait(running)
            # A cancelled coordinator still saves successfully drained targets.
            if pending:
                for future in running:
                    if future.exception() is None:
                        result = future.result()
                        results.append(result)
                        statuses[_target_vmid(result)] = str(result["status"])
            if pending or interrupted or (stop_event is not None and stop_event.is_set() and results):
                try:
                    _save_run_progress(
                        run_id=run_id, stage="finalizing", targets=targets,
                        statuses=statuses, done=len(results),
                        preflight_results=preflight_results, results=results,
                        target_updates=target_updates,
                    )
                except Exception:
                    logger.exception("Unable to persist drained targets run=%s", run_id)

    if stop_event is not None and stop_event.is_set():
        return None
    results.sort(key=lambda item: int(item.get("vmid") or 0))
    _save_run_progress(
        run_id=run_id,
        stage="finalizing",
        targets=targets,
        statuses=statuses,
        done=len(results),
        preflight_results=preflight_results,
        results=results,
        target_updates=target_updates,
    )
    return _ExecutedTargets(results=results)


def _save_results(run_id: uuid.UUID, results: list[dict[str, Any]]) -> None:
    with Session(engine) as session:
        run, artifact = _load_run_and_artifact(
            session=session, run_id=run_id, for_update=True
        )
        if run.status != TeacherJudgeScriptRunStatus.running:
            # 執行中途被回收成 failed：保留回收結果，不改回 completed
            logger.warning(
                "Discarding Teacher Judge script run %s results: status is already %s",
                run_id,
                run.status.value,
            )
            return
        targets = list(run.target_snapshot_json.get("targets") or [])
        preflight_results = list(
            run.target_snapshot_json.get("preflight_results") or []
        )
        statuses = {
            _target_vmid(result): str(result["status"])
            for result in results
            if result.get("vmid") is not None
        }
        finished_at = _now()
        initialize_targets(session, run)
        records = {row.target_key: row for row in run.target_records}
        for result in results:
            timing = result.get("timing")
            if isinstance(timing, dict) and timing.get("received_at"):
                timing["finalize_wait_seconds"] = seconds_between(
                    datetime.fromisoformat(timing["received_at"]), finished_at
                )
            row = records.get(target_key(result))
            if row is not None and row.status != "cancelled":
                store_target_result(row, result)
                row.timing_json = {**row.timing_json, "run_wait_seconds": seconds_between(row.finished_at, finished_at)}
        # results arrive already sorted by vmid from _execute_targets
        run.result_summary_json = {
            **_summary(results),
            **_run_execution_summary(run, finished_at),
        }
        run.progress_json = {
            "stage": "completed",
            "total": len(targets) + len(preflight_results),
            "done": len(results),
            "targets": _target_progress(targets, statuses)
            + preflight_progress(preflight_results),
        }
        run.status = TeacherJudgeScriptRunStatus.completed
        run.finished_at = finished_at
        run.updated_at = finished_at
        session.add(run)
        _touch_judge_session(session, artifact)
        update_batch(session, run.run_batch_id)
        session.commit()


def _run_execution_summary(
    run: TeacherJudgeScriptRun, finished_at: datetime,
) -> dict[str, Any]:
    return {
        "ssh_concurrency_per_run": MAX_SSH_CONCURRENCY_PER_RUN,
        "ssh_concurrency_total": MAX_TOTAL_SSH_CONCURRENCY,
        "timing": {
            "queue_seconds": seconds_between(run.prepared_at or run.created_at, run.started_at),
            "preparation_seconds": seconds_between(run.created_at, run.prepared_at),
            "execution_seconds": seconds_between(run.started_at, finished_at),
            "total_seconds": seconds_between(run.created_at, finished_at),
        },
    }


async def _await_worker(
    worker: asyncio.Task[_WorkerResult], *, stop_event: Event | None = None,
) -> _WorkerResult:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        if stop_event is not None:
            stop_event.set()
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
    stop_event = Event()
    collected = await _await_worker(
        asyncio.create_task(
            asyncio.to_thread(_execute_targets, run_id, stop_event=stop_event)
        ),
        stop_event=stop_event,
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
                    reason_code="executor_cancelled",
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
        class_ids = {artifact.teaching_class_id for _run, artifact in rows}
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
                        row[1].teaching_class_id,
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
    try:
        for run_id in run_ids:
            await execute_script_run(run_id)
    except asyncio.CancelledError:
        for run_id in run_ids:
            await _await_worker(asyncio.create_task(asyncio.to_thread(
                _mark_run_executor_failed, run_id,
                "整批執行流程已中斷。", reason_code="executor_cancelled",
            )))
        raise
