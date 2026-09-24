"""Teacher Judge managed script run service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlmodel import Session, col, select

from app.ai.teacher_judge.machine_context import (
    load_class_machine_nodes,
    machine_node_display_label,
    peer_node_keys_from_snapshot,
    resolve_class_machine_node,
    target_node_keys_from_snapshot,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRunBatchNodePublic,
    TeacherJudgeRunBatchPublic,
    TeacherJudgeScriptRunPublic,
)
from app.ai.teacher_judge.script_artifact_service import get_artifact
from app.ai.teacher_judge.target_ip_resolver import resolve_target_ip_address
from app.ai.teacher_judge.target_os import is_windows_target, resource_os_context
from app.core.i18n import t
from app.infrastructure.proxmox import operations as proxmox_ops
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
    TeachingClassMachineNode,
    TeachingClassStudent,
    TeachingClassStudentMachine,
)
from app.models.user import User
from app.repositories import resource as resource_repo
from app.services import os_identity_service

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


def _run_to_public(
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
            run.target_results_json
            if include_internal
            else _public_targets_payload(
                _redact_peer_ips(run.target_results_json, peer_ips)
            )
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
                col(TeachingClassStudentMachine.vmid).is_not(None),
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
        run_batch_id=run_batch_id,
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


def _item_result_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status") or "unknown") for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warning" in statuses:
        return "warning"
    if not checks or "unknown" in statuses:
        return "unknown"
    if statuses == {"skipped"}:
        return "skipped"
    if "collected" in statuses:
        return "collected"
    return "pass"


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
            else _item_result_status(checks)
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
                "evidence_state": "unavailable" if not peer_available else "available",
                "checks": checks,
                "missing_check_ids": [
                    check_id
                    for check_id in mapped_by_item.get(item_id, [])
                    if check_id not in checks_by_id
                ],
                "reason_code": (
                    target_result.get("reason_code")
                    if execution_failed
                    else "peer_unavailable"
                    if not peer_available
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
    statuses = {run.status for run in runs}
    if TeacherJudgeScriptRunStatus.running in statuses:
        return "running"
    if TeacherJudgeScriptRunStatus.pending in statuses:
        return "pending"
    completed = [run for run in runs if run.status == TeacherJudgeScriptRunStatus.completed]
    failed_targets = sum(
        int((run.result_summary_json or {}).get("failed") or 0) for run in runs
    )
    if completed and (len(completed) != len(runs) or failed_targets):
        return "completed_with_failures"
    if completed and len(completed) == len(runs):
        return "completed"
    return "failed"


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
            .join(
                TeacherJudgeScriptArtifact,
                col(TeacherJudgeScriptArtifact.id)
                == col(TeacherJudgeScriptRun.artifact_id),
            )
            .where(
                TeacherJudgeScriptRun.teaching_class_id == teaching_class_id,
                TeacherJudgeScriptRun.run_batch_id == run_batch_id,
            )
        ).all()
    )
    if session_id is not None:
        rows = [row for row in rows if row[1].session_id == session_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Run batch not found")
    nodes = load_class_machine_nodes(session, teaching_class_id)
    labels = {node.node_key: machine_node_display_label(node) for node in nodes}
    runs = [row[0] for row in rows]
    student_nodes: dict[str, list[dict[str, Any]]] = {}
    total_targets = completed_targets = failed_targets = 0
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
        raw_targets = (run.target_results_json or {}).get("targets")
        targets = raw_targets if isinstance(raw_targets, list) else []
        total_targets += int((run.progress_json or {}).get("total") or len(targets))
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
            if not isinstance(target, dict):
                continue
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
            student_nodes.setdefault(student_id, []).append(projected_node)
    first_artifact = rows[0][1]
    return TeacherJudgeRunBatchPublic(
        run_batch_id=str(run_batch_id),
        teaching_class_id=str(teaching_class_id),
        session_id=str(first_artifact.session_id) if first_artifact.session_id else None,
        status=cast(
            Literal["pending", "running", "completed", "completed_with_failures", "failed"],
            _batch_status(runs),
        ),
        summary={
            "nodes": len(rows),
            "students": len(student_nodes),
            "targets": total_targets,
            "completed": completed_targets,
            "failed": failed_targets,
        },
        nodes=node_outputs,
        students=[
            {"student_id": student_id, "nodes": student_nodes[student_id]}
            for student_id in sorted(student_nodes)
        ],
    )


def create_script_run_batch(
    *,
    session: Session,
    teaching_class_id: uuid.UUID,
    artifact_set_id: uuid.UUID,
    started_by: uuid.UUID | None,
    session_id: uuid.UUID | None = None,
) -> TeacherJudgeRunBatchPublic:
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
    latest: dict[str, TeacherJudgeScriptArtifact] = {}
    for row in rows:
        if row.status == TeacherJudgeScriptStatus.archived:
            continue
        node_key = str(row.target_node_key or "")
        if node_key not in latest or row.version > latest[node_key].version:
            latest[node_key] = row
    nodes = load_class_machine_nodes(session, teaching_class_id)
    node_order = {node.node_key: node.sort_order for node in nodes}
    children = sorted(
        latest.values(),
        key=lambda row: (
            node_order.get(str(row.target_node_key or ""), 10**9),
            str(row.target_node_key or ""),
        ),
    )
    if any(child.status != TeacherJudgeScriptStatus.approved for child in children):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "teacher_judge_artifact_set_not_approved",
                "message": "artifact set 的所有節點腳本都必須通過審查後才能執行。",
            },
        )
    run_batch_id = uuid.uuid4()
    try:
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
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return get_script_run_batch_public(
        session=session,
        teaching_class_id=teaching_class_id,
        run_batch_id=run_batch_id,
        session_id=session_id,
    )
