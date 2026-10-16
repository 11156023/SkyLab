"""Persist target results and aggregate batch lifecycle without SSH transactions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, cast

from sqlmodel import Session, select

from app.models.base import get_datetime_utc
from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
from app.models.teacher_judge_run_target import TeacherJudgeRunTarget
from app.models.teacher_judge_script_run import TeacherJudgeScriptRun

TERMINAL_TARGET_STATUSES = {"completed", "failed", "cancelled"}
_METADATA_KEYS = {
    "name",
    "node_name",
    "node_role",
    "display_label",
    "proxmox_node",
    "resource_type",
    "user",
}
_RESULT_KEYS = {
    "validation",
    "stdout_excerpt",
    "stderr_excerpt",
    "raw_result_json",
    "parsed_result",
    "teacher_review",
    "failure_stage",
}
_TIME_KEYS = (
    "queued_at",
    "started_at",
    "script_finished_at",
    "received_at",
    "finished_at",
)


def utc_datetime(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    start, end = utc_datetime(start), utc_datetime(end)
    return round(max(0.0, (end - start).total_seconds()), 3)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return None


def target_key(target: dict[str, Any]) -> str:
    student_id = target.get("student_id")
    if student_id:
        return f"student:{student_id}:node:{target.get('node_key') or ''}"
    return f"vmid:{target.get('vmid')}"


def initialize_targets(session: Session, run: TeacherJudgeScriptRun) -> None:
    """Create every selected target, including failures without a VMID.

    Old result payloads are moved only when a run is written; historical reads
    remain supported. The caller owns the transaction and the run row lock.
    """
    by_key = {row.target_key: row for row in run.target_records}
    snapshot = run.target_snapshot_json or {}
    sources = list(snapshot.get("targets") or []) + list(
        snapshot.get("preflight_results") or []
    )
    legacy_results = (run.target_results_json or {}).get("targets")
    if isinstance(legacy_results, list):
        sources += legacy_results
    for target in sources:
        if not isinstance(target, dict):
            continue
        key = target_key(target)
        if key not in by_key:
            student = target.get("student_id")
            row = TeacherJudgeRunTarget(
                run_id=run.id,
                target_key=key,
                student_id=str(student) if student else None,
                vmid=target.get("vmid"),
                node_key=target.get("node_key"),
                metadata_json={
                    key: target[key] for key in _METADATA_KEYS if key in target
                },
                created_at=run.created_at,
            )
            run.target_records.append(row)
            by_key[key] = row
        if (
            target.get("status") in TERMINAL_TARGET_STATUSES
            and by_key[key].status not in TERMINAL_TARGET_STATUSES
        ):
            store_target_result(by_key[key], target)
    if run.target_records:
        run.target_results_json = {}
    session.add(run)


def store_target_result(row: TeacherJudgeRunTarget, result: dict[str, Any]) -> None:
    row.status = str(result["status"])
    row.stage = row.status
    row.reason_code = result.get("reason_code")
    row.exit_code = result.get("exit_code")
    row.metadata_json = {
        **row.metadata_json,
        **{key: result[key] for key in _METADATA_KEYS if key in result},
    }
    row.result_json = {key: result[key] for key in _RESULT_KEYS if key in result}
    timing = result.get("timing") or {}
    row.timing_json = {
        key: value for key, value in timing.items() if key not in _TIME_KEYS
    }
    for key in _TIME_KEYS:
        value = _timestamp(timing.get(key))
        if value is not None:
            setattr(row, key, value)
    row.updated_at = get_datetime_utc()


def fail_unfinished_targets(
    run: TeacherJudgeScriptRun,
    message: str,
    reason: str,
    finished_at: datetime,
) -> None:
    for row in run.target_records:
        if row.status in TERMINAL_TARGET_STATUSES:
            continue
        row.status = "failed"
        failure_stage = row.stage
        row.stage = "failed"
        row.reason_code = reason
        row.finished_at = finished_at
        row.updated_at = finished_at
        row.result_json = {
            "validation": {
                "valid": False,
                "error": message,
                "schema_version": "teacher_judge_result.v1",
            },
            "stdout_excerpt": "",
            "stderr_excerpt": message[:16384],
            "raw_result_json": "",
            "parsed_result": None,
            "failure_stage": failure_stage,
        }


def batch_status(runs: list[TeacherJudgeScriptRun]) -> str:
    statuses = {run.status.value for run in runs}
    if "running" in statuses:
        return "running"
    if "pending" in statuses:
        return "running" if any(run.started_at for run in runs) else "pending"
    completed = sum(run.status.value == "completed" for run in runs)
    failed_targets = any(
        row.status in {"failed", "cancelled"}
        for run in runs
        for row in run.target_records
    ) or any(int((run.result_summary_json or {}).get("failed") or 0) for run in runs)
    if completed:
        return (
            "completed_with_failures"
            if completed != len(runs) or failed_targets
            else "completed"
        )
    return "cancelled" if statuses == {"cancelled"} else "failed"


def update_batch(session: Session, batch_id: uuid.UUID | None) -> None:
    if batch_id is None:
        return
    # All lifecycle writers lock run before batch; never lock another child run.
    batch = session.get(TeacherJudgeRunBatch, batch_id, with_for_update=True)
    if batch is None:
        return
    session.flush()
    runs = list(
        session.exec(
            select(TeacherJudgeScriptRun).where(
                TeacherJudgeScriptRun.run_batch_id == batch_id
            )
        ).all()
    )
    if not runs:
        return
    batch.status = cast(
        Literal[
            "pending",
            "running",
            "completed",
            "completed_with_failures",
            "failed",
            "cancelled",
        ],
        batch_status(runs),
    )
    starts = [utc_datetime(run.started_at) for run in runs if run.started_at]
    if starts:
        batch.started_at = min(starts)
    if all(run.finished_at is not None for run in runs):
        batch.finished_at = max(
            utc_datetime(run.finished_at) for run in runs if run.finished_at
        )
    errors = [
        str(run.result_summary_json["executor_error"])
        for run in runs
        if run.result_summary_json.get("executor_error")
    ]
    batch.error_message = "\n".join(errors)[:4000] or None
    batch.updated_at = get_datetime_utc()
    session.add(batch)
