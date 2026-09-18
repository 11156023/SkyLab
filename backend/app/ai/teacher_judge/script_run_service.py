"""Teacher Judge managed script run service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.ai.teacher_judge.machine_context import (
    machine_node_display_label,
    resolve_class_machine_node,
    target_node_keys_from_snapshot,
)
from app.ai.teacher_judge.schemas import TeacherJudgeScriptRunPublic
from app.ai.teacher_judge.script_artifact_service import get_artifact
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.core.i18n import t
from app.infrastructure.proxmox import operations as proxmox_ops
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptStatus
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.models.teaching_class import (
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.user import User
from app.repositories import resource as resource_repo

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        "node",
    }
)


def _public_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    return {
        key: value
        for key, value in target.items()
        if key not in _INTERNAL_TARGET_KEYS
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


def _run_to_public(
    run: TeacherJudgeScriptRun,
    *,
    include_internal: bool = False,
) -> TeacherJudgeScriptRunPublic:
    return TeacherJudgeScriptRunPublic(
        id=str(run.id),
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
            run.target_results_json
            if include_internal
            else _public_targets_payload(run.target_results_json)
        ),
        started_by=str(run.started_by) if run.started_by else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
    )


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


def _class_member_by_vmid(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
) -> dict[int, dict[str, Any]]:
    enrollments = list(
        session.exec(
            select(TeachingClassStudent).where(
                TeachingClassStudent.class_id == teaching_class_id
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
            select(TeachingClassStudentMachine).where(
                col(TeachingClassStudentMachine.class_student_id).in_(
                    list(enrollments_by_id)
                ),
                TeachingClassStudentMachine.vmid.is_not(None),
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
        resources = proxmox_ops.list_all_resources()
    except Exception as exc:
        logger.warning("Teacher Judge run target status lookup failed", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=t("run.status_lookup_failed"),
        ) from exc

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


def _resource_os_context(resource: Any) -> str:
    return " ".join(
        str(value).strip()
        for value in (
            getattr(resource, "os_info", None),
            getattr(resource, "environment_type", None),
        )
        if value is not None and str(value).strip()
    )


def _ensure_linux_executor_capability(resource: Any, vmid: int) -> None:
    """Reject known Windows resources until a Windows executor exists."""

    os_context = _resource_os_context(resource)
    normalized = os_context.casefold()
    windows_markers = ("windows", "win32", "win64", "win10", "win11", "microsoft")
    if any(marker in normalized for marker in windows_markers):
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
                TeachingClassStudent.class_id == teaching_class_id
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
        members.sort(key=lambda member: (str(member.get("student_id") or ""), int(member.get("vmid") or 0)))
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
        _ensure_linux_executor_capability(resource, vmid)
        ip_address = resolve_target_ip_address(
            session=session,
            vmid=vmid,
            live_resource=live,
        )
        if not ip_address:
            raise HTTPException(status_code=400, detail=t("run.no_ip", vmid=vmid))
        if not resource.ssh_private_key_encrypted:
            raise HTTPException(
                status_code=400, detail=t("run.no_ssh_key", vmid=vmid)
            )

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
        "stderr_excerpt": message[:16 * 1024],
        "raw_result_json": "",
        "parsed_result": None,
    }


def _resolve_node_targets(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    target_node_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve every student machine attached to one class-local node."""

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

    member_by_vmid = _class_member_by_vmid(
        session=session,
        teaching_class_id=teaching_class_id,
    )
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
) -> TeacherJudgeScriptRunPublic:
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
    if artifact_node_key and requested_node_key and artifact_node_key != requested_node_key:
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
        if artifact_node_key:
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
                        "message": "手動執行目標不屬於腳本指定的 target_node_key。",
                        "artifact_target_node_key": artifact_node_key,
                        "mismatched_node_keys": mismatched_targets,
                    },
                )
    elif effective_node_key and not target_vmids:
        targets, preflight_results = _resolve_node_targets(
            session=session,
            teaching_class_id=teaching_class_id,
            target_node_key=effective_node_key,
        )
    elif target_vmids:
        # Keep old all_with_vm/running_only callers functional during migration.
        targets = _resolve_running_targets(
            session=session,
            teaching_class_id=teaching_class_id,
            target_vmids=target_vmids,
        )
        if artifact_node_key:
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
                        "message": "舊式 VMID 執行目標不屬於腳本指定的 target_node_key。",
                        "artifact_target_node_key": artifact_node_key,
                        "mismatched_node_keys": mismatched_targets,
                    },
                )
    else:
        raise HTTPException(
            status_code=400,
            detail=t("schemas.target_node_key_required"),
        )
    if not targets and not preflight_results:
        raise HTTPException(status_code=400, detail=t("run.no_target_selected"))

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
    progress_targets.extend(
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
        for result in preflight_results
    )

    run = TeacherJudgeScriptRun(
        teaching_class_id=teaching_class_id,
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
        started_at=None,
        updated_at=_now(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return _run_to_public(run, include_internal=True)
