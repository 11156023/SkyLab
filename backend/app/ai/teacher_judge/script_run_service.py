"""Teacher Judge managed script run service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, col, desc, select

from app.ai.teacher_judge.machine_context import (
    load_class_machine_nodes,
    machine_node_display_label,
    peer_node_keys_from_snapshot,
    resolve_class_machine_node,
    target_node_keys_from_snapshot,
)
from app.ai.teacher_judge.run_record_service import (
    batch_status,
    initialize_targets,
    seconds_between,
    target_key,
    utc_datetime,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRunBatchNodePublic,
    TeacherJudgeRunBatchPublic,
    TeacherJudgeRunBatchSummary,
    TeacherJudgeRunTargetPage,
    TeacherJudgeRunTargetPublic,
    TeacherJudgeScriptRunPublic,
    TeacherJudgeScriptRunSummary,
)
from app.ai.teacher_judge.script_artifact_service import (
    get_artifact,
    latest_set_children,
)
from app.ai.teacher_judge.script_executor_service import preflight_progress
from app.ai.teacher_judge.script_policy import aggregate_check_status
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.ai.teacher_judge.target_os import is_windows_target, resource_os_context
from app.core.i18n import t
from app.core.request_context import get_request_context
from app.infrastructure.proxmox import operations as proxmox_ops
from app.models.base import get_datetime_utc as _now
from app.models.batch_provision import BatchProvisionTask
from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch
from app.models.teacher_judge_run_target import TeacherJudgeRunTarget
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.models.teaching_class import (
    INSTRUCTOR_ENROLLMENT_STATUS,
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.user import User
from app.repositories import resource as resource_repo
from app.services import os_identity_service

logger = logging.getLogger(__name__)


def _batch_timing_fields(batch: TeacherJudgeRunBatch) -> dict[str, Any]:
    return {
        "request_id": batch.request_id,
        "started_by": str(batch.started_by) if batch.started_by else None,
        "created_at": batch.created_at.isoformat(),
        "prepared_at": batch.prepared_at.isoformat() if batch.prepared_at else None,
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "error_message": batch.error_message,
        "timing": {
            "preparation_seconds": seconds_between(batch.requested_at, batch.prepared_at),
            "queue_seconds": seconds_between(batch.prepared_at, batch.started_at),
            "execution_seconds": seconds_between(batch.started_at, batch.finished_at),
            "total_seconds": seconds_between(batch.requested_at, batch.finished_at),
            "elapsed_seconds": seconds_between(batch.requested_at, batch.finished_at or _now()),
        },
    }


_INTERNAL_TARGET_KEYS = frozenset(
    {
        "vmid",
        "proxmox_node",
        "ip_address",
        "host",
        "ssh_user",
        "private_key_pem",
        "run_id",
        "has_ssh_key",
        "name",
        "os_info",
        "environment_type",
        "status_at_selection",
        "runtime_context",
        "node",
    }
)


def _peer_ips_from_target(target: Any) -> set[str]:
    if not isinstance(target, dict):
        return set()
    runtime_context = target.get("runtime_context")
    peers = runtime_context.get("peers") if isinstance(runtime_context, dict) else None
    if not isinstance(peers, dict):
        return set()
    return {
        ip_address.strip()
        for peer in peers.values()
        if isinstance(peer, dict)
        and isinstance((ip_address := peer.get("ip_address")), str)
        and ip_address.strip()
    }


def _peer_ips_from_snapshot(snapshot: Any) -> set[str]:
    if not isinstance(snapshot, dict):
        return set()
    raw_targets = snapshot.get("targets")
    if not isinstance(raw_targets, list):
        return set()
    return {
        ip_address
        for target in raw_targets
        for ip_address in _peer_ips_from_target(target)
    }


def _redact_peer_ips(value: Any, peer_ips: set[str]) -> Any:
    """Remove resolved peer addresses from public evidence without mutating raw DB data."""

    if not peer_ips:
        return value
    if isinstance(value, str):
        redacted = value
        for ip_address in peer_ips:
            redacted = redacted.replace(ip_address, "<peer-ip>")
        return redacted
    if isinstance(value, list):
        return [_redact_peer_ips(item, peer_ips) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_peer_ips(item, peer_ips)
            for key, item in value.items()
        }
    return value


def _peer_resolution_for_target(
    snapshot: Any,
    target_result: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return {}
    raw_targets = snapshot.get("targets")
    if not isinstance(raw_targets, list):
        return {}
    target_student_id = str(target_result.get("student_id") or "")
    target_vmid = target_result.get("vmid")
    for target in raw_targets:
        if not isinstance(target, dict):
            continue
        if target_student_id and str(target.get("student_id") or "") != target_student_id:
            continue
        if target_vmid is not None and str(target.get("vmid")) != str(target_vmid):
            continue
        runtime_context = target.get("runtime_context")
        peers = runtime_context.get("peers") if isinstance(runtime_context, dict) else None
        if not isinstance(peers, dict):
            return {}
        return {
            str(node_key): dict(peer)
            for node_key, peer in peers.items()
            if isinstance(peer, dict)
        }
    return {}


def _public_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    return {
        key: value for key, value in target.items() if key not in _INTERNAL_TARGET_KEYS
    }


def _public_targets_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result = dict(payload)
    raw_targets = result.get("targets")
    if isinstance(raw_targets, list):
        result["targets"] = [_public_target(target) for target in raw_targets]
    return result


def _public_target_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    result = dict(snapshot)
    script = result.get("script")
    if isinstance(script, dict):
        result["script"] = {
            key: value for key, value in script.items() if key != "template_key"
        }
    for key in ("targets", "preflight_results"):
        raw_targets = result.get(key)
        if isinstance(raw_targets, list):
            result[key] = [_public_target(target) for target in raw_targets]
    return result


def run_to_public(
    run: TeacherJudgeScriptRun,
    *,
    include_internal: bool = False,
) -> TeacherJudgeScriptRunPublic:
    peer_ips = _peer_ips_from_snapshot(run.target_snapshot_json)
    return TeacherJudgeScriptRunPublic(
        id=str(run.id),
        run_batch_id=str(run.run_batch_id) if run.run_batch_id else None,
        teaching_class_id=str(run.teaching_class_id),
        artifact_id=str(run.artifact_id),
        target_scope=run.target_scope.value,
        target_snapshot_json=(
            run.target_snapshot_json
            if include_internal
            else _public_target_snapshot(run.target_snapshot_json)
        ),
        status=run.status.value,
        progress_json=(
            run.progress_json
            if include_internal
            else _public_targets_payload(run.progress_json)
        ),
        result_summary_json=run.result_summary_json,
        target_results_json=(
            run.results_document
            if include_internal
            else _public_targets_payload(
                _redact_peer_ips(run.results_document, peer_ips)
            )
        ),
        started_by=str(run.started_by) if run.started_by else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
        prepared_at=run.prepared_at.isoformat() if run.prepared_at else None,
        request_id=run.request_id,
    )


# 舊名相容：模組內其他呼叫點仍用私有名稱
_run_to_public = run_to_public


def get_script_run_public(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TeacherJudgeScriptRunPublic:
    run = session.get(TeacherJudgeScriptRun, run_id)
    if (
        run is None
        or run.teaching_class_id != teaching_class_id
        or run.artifact_id != artifact_id
    ):
        raise HTTPException(status_code=404, detail="Script run not found")
    return _run_to_public(run)


def list_session_run_summaries(
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    skip: int,
    limit: int,
) -> list[TeacherJudgeScriptRunSummary]:
    """列出某個 Teacher Judge session（經由 script artifact）的執行紀錄摘要，新到舊。"""
    rows = session.exec(
        select(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(
            TeacherJudgeScriptArtifact.session_id == session_id,
            TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
        )
        .order_by(desc(TeacherJudgeScriptRun.created_at))
        .offset(skip)
        .limit(limit)
    ).all()
    return [
        TeacherJudgeScriptRunSummary(
            id=str(row.id),
            run_batch_id=str(row.run_batch_id) if row.run_batch_id else None,
            teaching_class_id=str(row.teaching_class_id),
            artifact_id=str(row.artifact_id),
            status=row.status.value,
            progress_json=row.progress_json,
            result_summary_json=row.result_summary_json,
            started_at=row.started_at.isoformat() if row.started_at else None,
            finished_at=row.finished_at.isoformat() if row.finished_at else None,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
            prepared_at=row.prepared_at.isoformat() if row.prepared_at else None,
            request_id=row.request_id,
        )
        for row in rows
    ]


def get_session_run_record(
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TeacherJudgeScriptRun:
    """取得屬於這個 session（經由 script artifact）的執行紀錄，找不到就 404。"""
    run = session.exec(
        select(TeacherJudgeScriptRun)
        .join(TeacherJudgeScriptArtifact)
        .where(
            TeacherJudgeScriptRun.id == run_id,
            TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
            TeacherJudgeScriptArtifact.session_id == session_id,
        )
    ).first()
    if run is None:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.runResultNotFound")
        )
    return run


def run_to_teacher_review_public(
    run: TeacherJudgeScriptRun,
) -> TeacherJudgeScriptRunPublic:
    """Expose only the VM identity needed by the authorized teacher review UI."""

    public = run_to_public(run)
    public_targets = public.target_results_json.get("targets")
    raw_targets = (run.results_document or {}).get("targets")
    if isinstance(public_targets, list) and isinstance(raw_targets, list):
        for public_target, raw_target in zip(public_targets, raw_targets, strict=False):
            if isinstance(public_target, dict) and isinstance(raw_target, dict):
                public_target["vmid"] = raw_target.get("vmid")
    return public


def get_session_run_for_review(
    session: Session,
    teaching_class_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TeacherJudgeScriptRunPublic:
    """老師檢閱用的單筆執行結果（會補回各 target 的 vmid）。"""
    run = get_session_run_record(session, teaching_class_id, session_id, run_id)
    return run_to_teacher_review_public(run)


_REVIEWABLE_CHECK_STATUSES = frozenset({"warning", "unknown", "collected"})


def update_target_review(
    session: Session,
    run: TeacherJudgeScriptRun,
    *,
    vmid: int | None = None,
    student_id: str | None = None,
    feedback: str,
    decisions: dict[str, str],
    reviewer_id: uuid.UUID,
) -> TeacherJudgeScriptRunPublic:
    """寫入（或清除）某個 target 的老師檢閱結果。

    target 以 vmid 或 student_id 比對；只允許對 AI 無法客觀判定的檢查項
    （warning／unknown／collected）做人工決定。feedback 與 decisions 都空白時
    視為撤回檢閱。
    """
    # Lock and reload canonical targets to keep concurrent teacher reviews intact.
    session.refresh(run, with_for_update=True)
    if run.status.value != "completed":
        raise HTTPException(
            status_code=409, detail=t("teacherJudgeSessions.reviewCompletedRunsOnly")
        )

    result_document = dict(run.results_document or {})
    raw_targets = result_document.get("targets")
    targets = (
        [dict(target) for target in raw_targets]
        if isinstance(raw_targets, list)
        else []
    )
    target_index = next(
        (
            index
            for index, target in enumerate(targets)
            if isinstance(target, dict)
            and (
                (vmid is not None and str(target.get("vmid")) == str(vmid))
                or (
                    student_id is not None
                    and str(target.get("student_id")) == student_id
                )
            )
        ),
        None,
    )
    if target_index is None:
        raise HTTPException(
            status_code=404, detail=t("teacherJudgeSessions.studentRunResultNotFound")
        )

    target = targets[target_index]
    parsed_result = target.get("parsed_result")
    raw_checks = parsed_result.get("checks") if isinstance(parsed_result, dict) else None
    if not isinstance(raw_checks, list):
        raw_checks = []
    reviewable_ids = {
        str(check.get("id") or "")
        for check in raw_checks
        if isinstance(check, dict)
        and str(check.get("status") or "") in _REVIEWABLE_CHECK_STATUSES
    }
    invalid_ids = sorted(set(decisions) - reviewable_ids)
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail=t(
                "teacherJudgeSessions.invalidReviewItems",
                items="、".join(invalid_ids),
            ),
        )

    now = _now()
    if feedback or decisions:
        target["teacher_review"] = {
            "feedback": feedback,
            "decisions": dict(decisions),
            "reviewed_by": str(reviewer_id),
            "updated_at": now.isoformat(),
        }
    else:
        target.pop("teacher_review", None)
    targets[target_index] = target
    result_document["targets"] = targets
    if run.target_records:
        row = next(row for row in run.target_records if row.target_key == target_key(target))
        result = dict(row.result_json)
        if "teacher_review" in target:
            result["teacher_review"] = target["teacher_review"]
        else:
            result.pop("teacher_review", None)
        row.result_json = result
        row.updated_at = now
    else:
        run.target_results_json = result_document
    run.updated_at = now
    session.add(run)
    session.commit()
    session.refresh(run)
    return run_to_teacher_review_public(run)


def _class_member_by_vmid(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
) -> dict[int, dict[str, Any]]:
    enrollments = list(
        session.exec(
            select(TeachingClassStudent).where(
                TeachingClassStudent.class_id == teaching_class_id,
                TeachingClassStudent.status != INSTRUCTOR_ENROLLMENT_STATUS,
            )
        ).all()
    )
    if not enrollments:
        return {}

    enrollments_by_id = {row.id: row for row in enrollments}
    users = list(
        session.exec(
            select(User).where(col(User.id).in_([row.user_id for row in enrollments]))
        ).all()
    )
    users_by_id = {user.id: user for user in users}
    nodes_by_id = {
        node.id: node
        for node in session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == teaching_class_id
            )
        ).all()
    }
    machines = list(
        session.exec(
            select(TeachingClassStudentMachine)
            .join(
                BatchProvisionTask,
                col(BatchProvisionTask.id)
                == col(TeachingClassStudentMachine.batch_task_id),
            )
            .where(
                col(TeachingClassStudentMachine.class_student_id).in_(
                    list(enrollments_by_id)
                ),
                col(BatchProvisionTask.vmid).is_not(None),
            )
        ).all()
    )

    result: dict[int, dict[str, Any]] = {}
    for machine in machines:
        enrollment = enrollments_by_id.get(machine.class_student_id)
        if enrollment is None or machine.vmid is None:
            continue
        user = users_by_id.get(enrollment.user_id)
        if user is None:
            continue
        node = nodes_by_id.get(machine.machine_node_id)
        node_context = (
            {
                "node_key": node.node_key,
                "node_name": node.name,
                "node_role": node.role,
                "display_label": machine_node_display_label(node),
            }
            if node is not None
            else {}
        )
        result[int(machine.vmid)] = {
            "student_id": str(enrollment.id),
            "user_id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            **node_context,
        }
    return result


def _running_resources_by_vmid() -> dict[int, dict[str, Any]]:
    try:
        return proxmox_ops.list_all_resources_by_vmid()
    except Exception as exc:
        logger.warning("Teacher Judge run target status lookup failed", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=t("run.status_lookup_failed"),
        ) from exc


def _ensure_linux_executor_capability(resource: Any, vmid: int) -> None:
    """Reject known Windows resources until a Windows executor exists."""

    if is_windows_target(resource):
        os_context = resource_os_context(resource)
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_unsupported_os",
                "message": t(
                    "run.unsupported_os",
                    vmid=vmid,
                    os_info=os_context or "unknown",
                ),
                "vmid": vmid,
                "os_info": os_context or None,
                "executor": "linux_ssh_sftp_python3",
            },
        )


def _class_member_by_node_key(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Return student machines grouped by class-local logical node key."""

    enrollments = list(
        session.exec(
            select(TeachingClassStudent).where(
                TeachingClassStudent.class_id == teaching_class_id,
                TeachingClassStudent.status != INSTRUCTOR_ENROLLMENT_STATUS,
            )
        ).all()
    )
    if not enrollments:
        return {}

    enrollments_by_id = {row.id: row for row in enrollments}
    users_by_id = {
        user.id: user
        for user in session.exec(
            select(User).where(col(User.id).in_([row.user_id for row in enrollments]))
        ).all()
    }
    nodes = list(
        session.exec(
            select(TeachingClassMachineNode).where(
                TeachingClassMachineNode.class_id == teaching_class_id
            )
        ).all()
    )
    nodes_by_id = {node.id: node for node in nodes}
    machines = list(
        session.exec(
            select(TeachingClassStudentMachine).where(
                col(TeachingClassStudentMachine.class_student_id).in_(
                    list(enrollments_by_id)
                )
            )
        ).all()
    )

    result: dict[str, list[dict[str, Any]]] = {}
    for machine in machines:
        enrollment = enrollments_by_id.get(machine.class_student_id)
        node = nodes_by_id.get(machine.machine_node_id)
        if enrollment is None or node is None:
            continue
        user = users_by_id.get(enrollment.user_id)
        if user is None:
            continue
        result.setdefault(node.node_key, []).append(
            {
                "student_id": str(enrollment.id),
                "vmid": machine.vmid,
                "machine_status": machine.status,
                "user_id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "node_key": node.node_key,
                "node_name": node.name,
                "node_role": node.role,
                "display_label": machine_node_display_label(node),
            }
        )
    for members in result.values():
        members.sort(
            key=lambda member: (
                str(member.get("student_id") or ""),
                int(member.get("vmid") or 0),
            )
        )
    return result


def _resolve_running_targets(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    target_vmids: list[int],
    member_by_vmid: dict[int, dict[str, Any]] | None = None,
    live_by_vmid: dict[int, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if member_by_vmid is None:
        member_by_vmid = _class_member_by_vmid(
            session=session,
            teaching_class_id=teaching_class_id,
        )
    if live_by_vmid is None:
        live_by_vmid = _running_resources_by_vmid()

    targets: list[dict[str, Any]] = []
    for vmid in target_vmids:
        member = member_by_vmid.get(vmid)
        if member is None:
            raise HTTPException(
                status_code=400,
                detail=t("run.vmid_not_in_class", vmid=vmid),
            )

        live = live_by_vmid.get(vmid)
        live_type = str(live.get("type") or "") if live else ""
        live_status = str(live.get("status") or "") if live else ""
        if live is None or live_type not in {"qemu", "lxc"}:
            raise HTTPException(
                status_code=400,
                detail=t("run.vmid_not_runnable", vmid=vmid),
            )
        if live_status != "running":
            raise HTTPException(
                status_code=400,
                detail=t("run.vmid_not_running", vmid=vmid),
            )

        resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
        if resource is None:
            raise HTTPException(
                status_code=400, detail=t("run.vmid_not_registered", vmid=vmid)
            )
        if str(resource.user_id) != member["user_id"]:
            raise HTTPException(
                status_code=400,
                detail=t("run.owner_mismatch", vmid=vmid),
            )
        # Guest OS 身份補偵測（僅欄位為空時探測一次並回寫；best-effort）
        os_identity_service.ensure_guest_os(
            session=session,
            resource=resource,
            node=str(live.get("node") or ""),
            resource_type=live_type,
        )
        _ensure_linux_executor_capability(resource, vmid)
        ip_address = resolve_target_ip_address(
            session=session,
            vmid=vmid,
            live_resource=live,
        )
        if not ip_address:
            raise HTTPException(status_code=400, detail=t("run.no_ip", vmid=vmid))
        if not resource.ssh_private_key_encrypted:
            raise HTTPException(status_code=400, detail=t("run.no_ssh_key", vmid=vmid))

        targets.append(
            {
                "vmid": vmid,
                "name": str(vmid),
                "student_id": member.get("student_id"),
                "node_key": member.get("node_key"),
                "node_name": member.get("node_name"),
                "node_role": member.get("node_role"),
                "display_label": member.get("display_label"),
                "resource_type": live_type,
                "status_at_selection": live_status,
                "proxmox_node": live.get("node"),
                "ip_address": ip_address,
                "ssh_user": "root",
                "has_ssh_key": True,
                "os_info": resource.os_info,
                "environment_type": resource.environment_type,
                "user": {
                    "id": member["user_id"],
                    "email": member["email"],
                    "full_name": member["full_name"],
                },
            }
        )

    return targets


def _node_target_failure(
    member: dict[str, Any],
    *,
    reason_code: str,
    detail: Any,
) -> dict[str, Any]:
    """Represent one logical-node target that failed preflight."""
    vmid = member.get("vmid")
    message = str(detail)
    return {
        "vmid": int(vmid) if vmid is not None else None,
        "name": str(vmid) if vmid is not None else member.get("node_name"),
        "student_id": member.get("student_id"),
        "node_key": member.get("node_key"),
        "node_name": member.get("node_name"),
        "node_role": member.get("node_role"),
        "display_label": member.get("display_label"),
        "resource_type": None,
        "status": "failed",
        "reason_code": reason_code,
        "user": {
            "id": member.get("user_id"),
            "email": member.get("email"),
            "full_name": member.get("full_name"),
        },
        "validation": {
            "valid": False,
            "error": message,
            "schema_version": "teacher_judge_result.v1",
        },
        "stdout_excerpt": "",
        "stderr_excerpt": message[: 16 * 1024],
        "raw_result_json": "",
        "parsed_result": None,
        "timing": {"finished_at": _now().isoformat()},
        "failure_stage": "preflight",
    }


def _resolve_node_targets(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    target_node_key: str,
    user_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every student machine attached to one class-local node.

    ``user_ids`` 只留這些學生的機器（導師核查「檢查這位學生／已回報的學生」）。
    """

    node = resolve_class_machine_node(session, teaching_class_id, target_node_key)
    if node is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_target_node_not_in_class",
                "message": "指定的 target_node_key 不屬於目前班級。",
                "target_node_key": target_node_key,
            },
        )

    members_by_node = _class_member_by_node_key(
        session=session,
        teaching_class_id=teaching_class_id,
    )
    members = members_by_node.get(node.node_key, [])
    if user_ids is not None:
        members = [member for member in members if member.get("user_id") in user_ids]
    if not members:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_node_has_no_students",
                "message": "指定的邏輯機器目前沒有學生機器。",
                "target_node_key": node.node_key,
                "display_label": machine_node_display_label(node),
            },
        )

    # The node's members already carry every field _resolve_running_targets
    # reads, so re-key them instead of reloading the class roster by VMID.
    member_by_vmid = {
        int(member["vmid"]): member
        for member in members
        if member.get("vmid") is not None
    }
    live_by_vmid = (
        _running_resources_by_vmid()
        if any(member.get("vmid") is not None for member in members)
        else {}
    )
    targets: list[dict[str, Any]] = []
    preflight_results: list[dict[str, Any]] = []
    for member in members:
        vmid = member.get("vmid")
        if vmid is None:
            preflight_results.append(
                _node_target_failure(
                    member,
                    reason_code="missing_vmid",
                    detail="student machine has no assigned VM/LXC",
                )
            )
            continue
        try:
            targets.extend(
                _resolve_running_targets(
                    session=session,
                    teaching_class_id=teaching_class_id,
                    target_vmids=[int(vmid)],
                    member_by_vmid=member_by_vmid,
                    live_by_vmid=live_by_vmid,
                )
            )
        except HTTPException as exc:
            preflight_results.append(
                _node_target_failure(
                    member,
                    reason_code="target_not_ready",
                    detail=exc.detail,
                )
            )
    return targets, preflight_results


def _attach_peer_runtime_contexts(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    executor_node_key: str,
    peer_node_keys: set[str],
    targets: list[dict[str, Any]],
) -> None:
    """Attach only declared same-student peer addresses to internal targets."""

    if not peer_node_keys:
        for target in targets:
            target["runtime_context"] = {
                "schema_version": "teacher_judge_runtime_context.v1",
                "executor": {"node_key": executor_node_key},
                "peers": {},
            }
        return
    members_by_node = _class_member_by_node_key(
        session=session,
        teaching_class_id=teaching_class_id,
    )
    try:
        live_by_vmid = _running_resources_by_vmid()
    except HTTPException:
        # A peer lookup is optional evidence. Keep the executor target runnable
        # and record peer_unavailable per declared peer instead of aborting the
        # whole child run.
        live_by_vmid = {}
    peers_by_key_and_student = {
        (node_key, str(member.get("student_id") or "")): member
        for node_key in peer_node_keys
        for member in members_by_node.get(node_key, [])
    }
    for target in targets:
        student_id = str(target.get("student_id") or "")
        peers: dict[str, dict[str, Any]] = {}
        for peer_node_key in sorted(peer_node_keys):
            member = peers_by_key_and_student.get((peer_node_key, student_id))
            ip_address: str | None = None
            reason_code: str | None = None
            if member is None:
                reason_code = "peer_machine_missing"
            elif member.get("vmid") is None:
                reason_code = "peer_vmid_missing"
            else:
                vmid = int(member["vmid"])
                live = live_by_vmid.get(vmid)
                try:
                    resource = resource_repo.get_resource_by_vmid(
                        session=session,
                        vmid=vmid,
                    )
                except Exception:
                    logger.warning(
                        "Teacher Judge peer resource lookup failed vmid=%s",
                        vmid,
                        exc_info=True,
                    )
                    resource = None
                if live is None or str(live.get("status") or "") != "running":
                    reason_code = "peer_not_running"
                elif str(live.get("type") or "") not in {"qemu", "lxc"}:
                    reason_code = "peer_resource_type_invalid"
                elif resource is None:
                    reason_code = "peer_resource_missing"
                elif str(resource.user_id) != str(member.get("user_id") or ""):
                    reason_code = "peer_owner_mismatch"
                else:
                    try:
                        ip_address = resolve_target_ip_address(
                            session=session,
                            vmid=vmid,
                            live_resource=live,
                        )
                    except Exception:
                        logger.warning(
                            "Teacher Judge peer IP resolution failed vmid=%s",
                            vmid,
                            exc_info=True,
                        )
                        reason_code = "peer_ip_unavailable"
                    if not ip_address and reason_code is None:
                        reason_code = "peer_ip_unavailable"
            peers[peer_node_key] = {
                "ip_address": ip_address,
                "resolution_status": "ready" if ip_address else "unavailable",
                "reason_code": reason_code,
            }
        target["runtime_context"] = {
            "schema_version": "teacher_judge_runtime_context.v1",
            "executor": {"node_key": executor_node_key},
            "peers": peers,
        }


def _ensure_targets_on_node(
    targets: list[dict[str, Any]],
    artifact_node_key: str | None,
    message: str,
) -> None:
    """Reject VMID-selected targets that are not on the script's target node."""

    if not artifact_node_key:
        return
    mismatched_targets = sorted(
        {
            str(target.get("node_key") or "")
            for target in targets
            if target.get("node_key") != artifact_node_key
        }
    )
    if mismatched_targets:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_target_node_mismatch",
                "message": message,
                "artifact_target_node_key": artifact_node_key,
                "mismatched_node_keys": mismatched_targets,
            },
        )


def create_script_run(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_id: uuid.UUID,
    target_scope: TeacherJudgeScriptRunTargetScope,
    target_vmids: list[int] | None,
    started_by: uuid.UUID | None,
    target_node_key: str | None = None,
    requested_item_id: str | None = None,
    run_batch_id: uuid.UUID | None = None,
    commit: bool = True,
    user_ids: set[str] | None = None,
) -> TeacherJudgeScriptRunPublic:
    requested_at = _now()
    artifact = get_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact_id,
    )
    if artifact.status != TeacherJudgeScriptStatus.approved:
        raise HTTPException(status_code=400, detail=t("run.artifact_not_approved"))

    requested_node_key = str(target_node_key or "").strip() or None
    artifact_node_keys = target_node_keys_from_snapshot(artifact.rubric_snapshot_json)
    if len(artifact_node_keys) > 1:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_mixed_target_nodes",
                "message": "同一份腳本不能同時執行多個 target_node_key。",
                "target_node_keys": sorted(artifact_node_keys),
            },
        )
    artifact_node_key = next(iter(artifact_node_keys), None)
    if (
        artifact_node_key
        and requested_node_key
        and artifact_node_key != requested_node_key
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "teacher_judge_target_node_mismatch",
                "message": "執行目標與腳本的 target_node_key 不一致。",
                "artifact_target_node_key": artifact_node_key,
                "requested_target_node_key": requested_node_key,
            },
        )
    effective_node_key = requested_node_key or artifact_node_key

    preflight_results: list[dict[str, Any]] = []
    if target_scope == TeacherJudgeScriptRunTargetScope.manual:
        targets = _resolve_running_targets(
            session=session,
            teaching_class_id=teaching_class_id,
            target_vmids=target_vmids or [],
        )
        _ensure_targets_on_node(
            targets,
            artifact_node_key,
            "手動執行目標不屬於腳本指定的 target_node_key。",
        )
    elif effective_node_key and not target_vmids:
        targets, preflight_results = _resolve_node_targets(
            session=session,
            teaching_class_id=teaching_class_id,
            target_node_key=effective_node_key,
            user_ids=user_ids,
        )
    elif target_vmids:
        # Keep old all_with_vm/running_only callers functional during migration.
        targets = _resolve_running_targets(
            session=session,
            teaching_class_id=teaching_class_id,
            target_vmids=target_vmids,
        )
        _ensure_targets_on_node(
            targets,
            artifact_node_key,
            "舊式 VMID 執行目標不屬於腳本指定的 target_node_key。",
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=t("schemas.target_node_key_required"),
        )
    if not targets and not preflight_results:
        raise HTTPException(status_code=400, detail=t("run.no_target_selected"))
    if effective_node_key:
        _attach_peer_runtime_contexts(
            session=session,
            teaching_class_id=teaching_class_id,
            executor_node_key=effective_node_key,
            peer_node_keys=peer_node_keys_from_snapshot(
                artifact.rubric_snapshot_json
            ),
            targets=targets,
        )

    progress_targets = [
        {
            "vmid": target["vmid"],
            "name": target["name"],
            "student_id": target.get("student_id"),
            "node_key": target.get("node_key"),
            "node_name": target.get("node_name"),
            "display_label": target.get("display_label"),
            "proxmox_node": target["proxmox_node"],
            "resource_type": target["resource_type"],
            "user": target["user"],
            "status": "queued",
            "reason_code": None,
        }
        for target in targets
    ]
    progress_targets.extend(preflight_progress(preflight_results))

    run = TeacherJudgeScriptRun(
        run_batch_id=run_batch_id,
        artifact_id=artifact.id,
        target_scope=target_scope,
        target_snapshot_json={
            "script": {
                "id": str(artifact.id),
                "name": artifact.name,
                "version": artifact.version,
                "template_key": artifact.template_key,
            },
            "target_node_key": effective_node_key,
            "targets": targets,
            "preflight_results": preflight_results,
            "requested_item_id": requested_item_id,
        },
        status=TeacherJudgeScriptRunStatus.pending,
        progress_json={
            "stage": "pending_executor",
            "total": len(progress_targets),
            "done": len(preflight_results),
            "targets": progress_targets,
        },
        result_summary_json={"preflight_failed": len(preflight_results)},
        target_results_json=(
            {
                "schema_version": "teacher_judge_run_results.v2",
                "targets": preflight_results,
            }
            if preflight_results
            else {}
        ),
        started_by=started_by,
        request_id=get_request_context().request_id,
        created_at=requested_at,
        prepared_at=_now(),
        started_at=None,
        updated_at=_now(),
    )
    session.add(run)
    initialize_targets(session, run)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(run)
    return _run_to_public(run, include_internal=True)


def _coverage_check_ids_by_item(
    artifact: TeacherJudgeScriptArtifact,
) -> dict[str, list[str]]:
    coverage = (artifact.policy_check_result_json or {}).get("coverage")
    mappings = coverage.get("mappings") if isinstance(coverage, dict) else []
    result: dict[str, list[str]] = {}
    for mapping in mappings if isinstance(mappings, list) else []:
        if not isinstance(mapping, dict):
            continue
        check_id = str(mapping.get("check_id") or "").strip()
        for raw_item_id in mapping.get("rubric_item_ids") or []:
            item_id = str(raw_item_id).strip()
            if item_id and check_id and check_id not in result.setdefault(item_id, []):
                result[item_id].append(check_id)
    return result


def project_run_items(
    *,
    artifact: TeacherJudgeScriptArtifact,
    target_result: dict[str, Any],
    display_labels: dict[str, str],
    peer_ips: set[str] | None = None,
    peer_resolution: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project raw runtime checks through artifact coverage to rubric items."""

    parsed = target_result.get("parsed_result")
    raw_checks = parsed.get("checks") if isinstance(parsed, dict) else []
    if not isinstance(raw_checks, list):
        raw_checks = []
    checks_by_id = {
        str(check.get("id") or ""): check
        for check in raw_checks
        if isinstance(check, dict) and str(check.get("id") or "")
    }
    mapped_by_item = _coverage_check_ids_by_item(artifact)
    mapped_check_ids = {
        check_id for check_ids in mapped_by_item.values() for check_id in check_ids
    }
    items: list[dict[str, Any]] = []
    execution_failed = target_result.get("status") != "completed"
    for raw_item in (artifact.rubric_snapshot_json or {}).get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "")
        checks = [
            checks_by_id[check_id]
            for check_id in mapped_by_item.get(item_id, [])
            if check_id in checks_by_id
        ]
        missing_check_ids = [
            check_id for check_id in mapped_by_item.get(item_id, [])
            if check_id not in checks_by_id
        ]
        checks = cast(
            "list[dict[str, Any]]",
            _redact_peer_ips(checks, peer_ips or set()),
        )
        peer_node_key = str(raw_item.get("peer_node_key") or "") or None
        peer_state = (peer_resolution or {}).get(peer_node_key or "")
        peer_available = not peer_node_key or (
            isinstance(peer_state, dict)
            and peer_state.get("resolution_status") == "ready"
        )
        item_status = (
            "unknown"
            if execution_failed or not peer_available
            else aggregate_check_status(
                [str(check.get("status") or "unknown") for check in checks],
                incomplete=bool(missing_check_ids),
            )
        )
        items.append(
            {
                "rubric_item_id": item_id,
                "title": str(raw_item.get("title") or item_id),
                "judgement_mode": str(raw_item.get("judgement_mode") or "ai"),
                "status": item_status,
                "peer_node_key": peer_node_key,
                "peer_display_label": display_labels.get(peer_node_key or ""),
                "peer_resolution_status": (
                    peer_state.get("resolution_status")
                    if isinstance(peer_state, dict)
                    else None
                ),
                "evidence_state": (
                    "unavailable" if not peer_available
                    else "incomplete" if missing_check_ids else "available"
                ),
                "checks": checks,
                "missing_check_ids": missing_check_ids,
                "reason_code": (
                    target_result.get("reason_code")
                    if execution_failed
                    else "peer_unavailable"
                    if not peer_available
                    else "missing_checks"
                    if missing_check_ids
                    else None
                ),
            }
        )
    raw_teacher_review = target_result.get("teacher_review")
    return {
        "node_key": artifact.target_node_key,
        "display_label": display_labels.get(str(artifact.target_node_key or "")),
        "execution_status": target_result.get("status"),
        "reason_code": target_result.get("reason_code"),
        **(
            {"timing": dict(target_result["timing"])}
            if isinstance(target_result.get("timing"), dict) else {}
        ),
        "vmid": target_result.get("vmid"),
        "teacher_review": (
            cast(
                "dict[str, Any]",
                _redact_peer_ips(raw_teacher_review, peer_ips or set()),
            )
            if isinstance(raw_teacher_review, dict)
            else None
        ),
        "items": items,
        "unmapped_checks": cast(
            "list[dict[str, Any]]",
            _redact_peer_ips(
                [
                    check
                    for check_id, check in checks_by_id.items()
                    if check_id not in mapped_check_ids
                ],
                peer_ips or set(),
            ),
        ),
    }


def _batch_status(runs: list[TeacherJudgeScriptRun]) -> str:
    return batch_status(runs)


def get_script_run_batch_public(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    run_batch_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeRunBatchPublic:
    rows = list(
        session.exec(
            select(TeacherJudgeScriptRun, TeacherJudgeScriptArtifact)
            .options(selectinload(cast(Any, TeacherJudgeScriptRun.target_records)))
            .join(
                TeacherJudgeScriptArtifact,
                col(TeacherJudgeScriptArtifact.id)
                == col(TeacherJudgeScriptRun.artifact_id),
            )
            .where(
                TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
                TeacherJudgeScriptRun.run_batch_id == run_batch_id,
            )
        ).all()
    )
    if session_id is not None:
        rows = [row for row in rows if row[1].session_id == session_id]
    batch = session.get(TeacherJudgeRunBatch, run_batch_id)
    if batch is not None and (batch.teaching_class_id != teaching_class_id or (session_id is not None and batch.session_id != session_id)):
        raise HTTPException(status_code=404, detail="Run batch not found")
    if not rows:
        if batch is not None:
            return TeacherJudgeRunBatchPublic(
                run_batch_id=str(batch.id), teaching_class_id=str(teaching_class_id),
                session_id=str(batch.session_id) if batch.session_id else None,
                status=batch.status,
                summary={"nodes": 0, "students": 0, "targets": 0, "completed": 0, "failed": 0, "queued": 0, "running": 0, "cancelled": 0},
                nodes=[], students=[], **_batch_timing_fields(batch),
            )
        raise HTTPException(status_code=404, detail="Run batch not found")
    nodes = load_class_machine_nodes(session, teaching_class_id)
    labels = {node.node_key: machine_node_display_label(node) for node in nodes}
    runs = [row[0] for row in rows]
    # The review UI reloads results only after every child run is terminal.
    batch_finished_at = (
        max(utc_datetime(run.finished_at) for run in runs if run.finished_at is not None)
        if all(run.finished_at is not None for run in runs)
        else None
    )
    student_nodes: dict[str, list[dict[str, Any]]] = {}
    total_targets = completed_targets = failed_targets = 0
    student_ids: set[str] = set()
    queued_targets = running_targets = cancelled_targets = 0
    node_outputs: list[TeacherJudgeRunBatchNodePublic] = []
    node_order = {node.node_key: node.sort_order for node in nodes}
    for run, artifact in sorted(
        rows,
        key=lambda row: (
            node_order.get(str(row[1].target_node_key or ""), 10**9),
            str(row[1].target_node_key or ""),
        ),
    ):
        peer_ips = _peer_ips_from_snapshot(run.target_snapshot_json)
        raw_targets = (run.results_document or {}).get("targets")
        # 非 dict 的項目在這裡就濾掉；之前的守門放在 .get("status") 之後，
        # 遇到壞資料會先 AttributeError 而永遠到不了。
        targets = (
            [target for target in raw_targets if isinstance(target, dict)]
            if isinstance(raw_targets, list)
            else []
        )
        if run.target_records:
            total_targets += len(run.target_records)
            student_ids.update(row.student_id for row in run.target_records if row.student_id)
            queued_targets += sum(row.status == "queued" for row in run.target_records)
            running_targets += sum(row.status == "running" for row in run.target_records)
            cancelled_targets += sum(row.status == "cancelled" for row in run.target_records)
        else:
            total_targets += int((run.progress_json or {}).get("total") or len(targets))
            student_ids.update(str(target["student_id"]) for target in targets if target.get("student_id"))
        completed_targets += sum(
            1 for target in targets if target.get("status") == "completed"
        )
        failed_targets += sum(
            1 for target in targets if target.get("status") == "failed"
        )
        node_outputs.append(
            TeacherJudgeRunBatchNodePublic(
                target_node_key=str(artifact.target_node_key or ""),
                display_label=labels.get(str(artifact.target_node_key or "")),
                artifact_id=str(artifact.id),
                run_id=str(run.id),
                status=run.status.value,
                progress_json=_public_targets_payload(run.progress_json),
                result_summary_json=run.result_summary_json,
            )
        )
        for target in targets:
            student_id = str(target.get("student_id") or "")
            if not student_id:
                continue
            projected_node = project_run_items(
                artifact=artifact,
                target_result=target,
                display_labels=labels,
                peer_ips=peer_ips,
                peer_resolution=_peer_resolution_for_target(
                    run.target_snapshot_json,
                    target,
                ),
            )
            # Teacher-review persistence is scoped by the child run and VMID.
            # Keep the child run id on every student-node projection so clients do
            # not have to infer it from the batch-level node summary.
            projected_node["run_id"] = str(run.id)
            if "timing" in projected_node:
                projected_node["timing"]["batch_wait_seconds"] = (
                    seconds_between(run.finished_at, batch_finished_at)
                    if batch_finished_at is not None and run.finished_at is not None
                    else None
                )
                projected_node["timing"]["completion_wait_seconds"] = seconds_between(
                    _target_finished_at(target), batch_finished_at,
                )
            student_nodes.setdefault(student_id, []).append(projected_node)
    first_artifact = rows[0][1]
    return TeacherJudgeRunBatchPublic(
        run_batch_id=str(run_batch_id),
        teaching_class_id=str(teaching_class_id),
        session_id=str(first_artifact.session_id) if first_artifact.session_id else None,
        status=cast(
            Literal["pending", "running", "completed", "completed_with_failures", "failed", "cancelled"],
            _batch_status(runs),
        ),
        summary={
            "nodes": len(rows),
            "students": len(student_ids),
            "targets": total_targets,
            "completed": completed_targets,
            "failed": failed_targets,
            "queued": queued_targets,
            "running": running_targets,
            "cancelled": cancelled_targets,
        },
        nodes=node_outputs,
        students=[
            {"student_id": student_id, "nodes": student_nodes[student_id]}
            for student_id in sorted(student_nodes)
        ],
        request_id=batch.request_id if batch else None,
        started_by=str(batch.started_by) if batch and batch.started_by else None,
        created_at=(batch.created_at if batch else min(run.created_at for run in runs)).isoformat(),
        prepared_at=batch.prepared_at.isoformat() if batch and batch.prepared_at else None,
        started_at=min(utc_datetime(run.started_at) for run in runs if run.started_at).isoformat()
        if any(run.started_at for run in runs) else None,
        finished_at=batch_finished_at.isoformat() if batch_finished_at else None,
        error_message=batch.error_message if batch else None,
        timing={
            "preparation_seconds": seconds_between(batch.requested_at, batch.prepared_at) if batch else None,
            "queue_seconds": seconds_between(batch.prepared_at, batch.started_at) if batch else None,
            "execution_seconds": seconds_between(batch.started_at, batch_finished_at) if batch else None,
            "total_seconds": seconds_between(batch.requested_at, batch_finished_at) if batch else None,
            "elapsed_seconds": seconds_between(batch.requested_at, batch_finished_at or _now()) if batch else None,
        },
    )


def list_session_batch_summaries(
    session: Session, teaching_class_id: uuid.UUID, session_id: uuid.UUID,
    skip: int, limit: int,
) -> list[TeacherJudgeRunBatchSummary]:
    batches = session.exec(select(TeacherJudgeRunBatch).where(
        TeacherJudgeRunBatch.teaching_class_id == teaching_class_id,
        TeacherJudgeRunBatch.session_id == session_id,
    ).order_by(desc(TeacherJudgeRunBatch.created_at), desc(TeacherJudgeRunBatch.id))
        .offset(skip).limit(limit)).all()
    result = []
    for batch in batches:
        counts = session.exec(select(
            func.count(func.distinct(TeacherJudgeScriptRun.id)),
            func.count(col(TeacherJudgeRunTarget.id)),
            func.count(func.distinct(TeacherJudgeRunTarget.student_id)),
            *[func.coalesce(func.sum(case((col(TeacherJudgeRunTarget.status) == state, 1), else_=0)), 0)
              for state in ("completed", "failed", "queued", "running", "cancelled")],
        ).select_from(TeacherJudgeScriptRun).outerjoin(
            TeacherJudgeRunTarget, col(TeacherJudgeRunTarget.run_id) == col(TeacherJudgeScriptRun.id),
        ).where(TeacherJudgeScriptRun.run_batch_id == batch.id)).one()
        summary = dict(zip(("nodes", "targets", "students", "completed", "failed", "queued", "running", "cancelled"), counts, strict=True))
        result.append(TeacherJudgeRunBatchSummary(
            run_batch_id=str(batch.id), teaching_class_id=str(teaching_class_id),
            session_id=str(batch.session_id) if batch.session_id else None,
            status=batch.status, summary=summary, **_batch_timing_fields(batch),
        ))
    return result


def _target_finished_at(target: dict[str, Any]) -> datetime | None:
    value = (target.get("timing") or {}).get("finished_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def list_run_targets(
    session: Session, run: TeacherJudgeScriptRun, *, skip: int, limit: int,
    status: str | None = None, student_id: uuid.UUID | None = None,
    vmid: int | None = None,
) -> TeacherJudgeRunTargetPage:
    conditions = [TeacherJudgeRunTarget.run_id == run.id]
    if status:
        conditions.append(TeacherJudgeRunTarget.status == status)
    if student_id:
        conditions.append(TeacherJudgeRunTarget.student_id == str(student_id))
    if vmid is not None:
        conditions.append(TeacherJudgeRunTarget.vmid == vmid)
    total = session.exec(select(func.count()).select_from(TeacherJudgeRunTarget).where(*conditions)).one()
    rows = session.exec(select(TeacherJudgeRunTarget).where(*conditions)
        .order_by(col(TeacherJudgeRunTarget.created_at), col(TeacherJudgeRunTarget.target_key))
        .offset(skip).limit(limit)).all()
    peer_ips = _peer_ips_from_snapshot(run.target_snapshot_json)
    data = []
    for row in rows:
        public_result = _public_target(_redact_peer_ips(row.to_result(), peer_ips))
        times = {key: row.to_result().get("timing", {}).get(key) for key in (
            "queued_at", "started_at", "script_finished_at", "received_at", "finished_at",
        )}
        data.append(TeacherJudgeRunTargetPublic(
            id=str(row.id), run_id=str(row.run_id), student_id=row.student_id,
            vmid=row.vmid, node_key=row.node_key,
            status=cast(Literal["queued", "running", "completed", "failed", "cancelled"], row.status), stage=row.stage,
            reason_code=row.reason_code, exit_code=row.exit_code,
            created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat(),
            **times, timing=row.timing_json, result=public_result,
        ))
    return TeacherJudgeRunTargetPage(data=data, total=total)


def create_script_run_batch(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    started_by: uuid.UUID | None,
    session_id: uuid.UUID | None = None,
    user_ids: list[uuid.UUID] | None = None,
) -> TeacherJudgeRunBatchPublic:
    """整組腳本跑一輪；``user_ids`` 有值時只檢查這些學生（其他人的結果不動）。"""
    requested_at = _now()
    rows = list(
        session.exec(
            select(TeacherJudgeScriptArtifact).where(
                TeacherJudgeScriptArtifact.teaching_class_id == teaching_class_id,
                TeacherJudgeScriptArtifact.artifact_set_id == artifact_set_id,
            )
        ).all()
    )
    if session_id is not None:
        rows = [row for row in rows if row.session_id == session_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Script set not found")
    nodes = load_class_machine_nodes(session, teaching_class_id)
    children = latest_set_children(
        rows,
        node_order={node.node_key: node.sort_order for node in nodes},
    )
    if any(child.status != TeacherJudgeScriptStatus.approved for child in children):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_artifact_set_not_approved",
                "message": "artifact set 的所有節點腳本都必須通過審查後才能執行。",
            },
        )
    wanted_users: set[str] | None = None
    if user_ids:
        wanted_users = {str(user_id) for user_id in user_ids}
        members_by_node = _class_member_by_node_key(
            session=session, teaching_class_id=teaching_class_id
        )
        # 只跑有這些學生機器的那幾台；一台都沒有就不必建立空的批次
        children = [
            child
            for child in children
            if any(
                member.get("user_id") in wanted_users
                for member in members_by_node.get(child.target_node_key or "", [])
            )
        ]
        if not children:
            raise HTTPException(
                status_code=400, detail=t("teacherJudgeSessions.noStudentMachines")
            )
    run_batch_id = uuid.uuid4()
    try:
        batch = TeacherJudgeRunBatch(
            id=run_batch_id, teaching_class_id=teaching_class_id,
            session_id=session_id, artifact_set_id=artifact_set_id,
            started_by=started_by, request_id=get_request_context().request_id,
            requested_at=requested_at, created_at=requested_at,
        )
        session.add(batch)
        # Publish the accepted batch before potentially slow target preparation.
        session.commit()
        for child in children:
            create_script_run(
                session=session,
                teaching_class_id=teaching_class_id,
                artifact_id=child.id,
                target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
                target_vmids=None,
                started_by=started_by,
                target_node_key=child.target_node_key,
                run_batch_id=run_batch_id,
                commit=False,
                user_ids=wanted_users,
            )
        prepared_batch = session.get(TeacherJudgeRunBatch, run_batch_id, with_for_update=True)
        if prepared_batch is None or prepared_batch.status != "pending":
            raise HTTPException(status_code=409, detail="Batch preparation interrupted")
        prepared_batch.prepared_at = _now()
        session.add(prepared_batch)
        session.commit()
    except Exception:
        session.rollback()
        # Failed preparation is still a whole-class execution attempt. Preserve
        # its lifecycle after rolling back incomplete child runs.
        try:
            failure = session.get(TeacherJudgeRunBatch, run_batch_id, with_for_update=True)
            if failure is not None and failure.status == "pending":
                failure.status = "failed"
                failure.finished_at = _now()
                failure.updated_at = failure.finished_at
                failure.error_message = "批次前置處理失敗。"
                session.add(failure)
                session.commit()
        except Exception:
            session.rollback()
            logger.exception("Unable to persist batch preparation failure batch=%s", run_batch_id)
        raise
    return get_script_run_batch_public(
        session=session,
        teaching_class_id=teaching_class_id,
        run_batch_id=run_batch_id,
        session_id=session_id,
    )
