"""Teacher Judge managed script run service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge.schemas import TeacherJudgeScriptRunPublic
from app.ai.teacher_judge.script_artifact_service import get_artifact
from app.infrastructure.proxmox import operations as proxmox_ops
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptStatus
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.repositories import group as group_repo

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run_to_public(run: TeacherJudgeScriptRun) -> TeacherJudgeScriptRunPublic:
    return TeacherJudgeScriptRunPublic(
        id=str(run.id),
        group_id=str(run.group_id),
        artifact_id=str(run.artifact_id),
        target_scope=run.target_scope.value,
        target_snapshot_json=run.target_snapshot_json,
        status=run.status.value,
        progress_json=run.progress_json,
        result_summary_json=run.result_summary_json,
        target_results_json=run.target_results_json,
        started_by=str(run.started_by) if run.started_by else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
    )


def _group_member_by_vmid(
    *,
    session: Session,
    group_id: uuid.UUID,
) -> dict[int, dict[str, Any]]:
    member_vmids = group_repo.get_member_vmids(session=session, group_id=group_id)
    users = group_repo.get_group_members(session=session, group_id=group_id)
    users_by_id = {user.id: user for user in users}

    result: dict[int, dict[str, Any]] = {}
    for user_id, vmid in member_vmids.items():
        if vmid is None:
            continue
        user = users_by_id.get(user_id)
        if user is None:
            continue
        result[int(vmid)] = {
            "user_id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
        }
    return result


def _running_resources_by_vmid() -> dict[int, dict[str, Any]]:
    try:
        resources = proxmox_ops.list_all_resources()
    except Exception as exc:
        logger.warning("Teacher Judge run target status lookup failed", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="無法確認 VM/LXC 即時狀態，請稍後再試。",
        ) from exc

    result: dict[int, dict[str, Any]] = {}
    for resource in resources:
        try:
            vmid = int(resource.get("vmid"))
        except (TypeError, ValueError):
            continue
        result[vmid] = dict(resource)
    return result


def _resolve_running_targets(
    *,
    session: Session,
    group_id: uuid.UUID,
    target_vmids: list[int],
) -> list[dict[str, Any]]:
    member_by_vmid = _group_member_by_vmid(session=session, group_id=group_id)
    live_by_vmid = _running_resources_by_vmid()

    targets: list[dict[str, Any]] = []
    for vmid in target_vmids:
        member = member_by_vmid.get(vmid)
        if member is None:
            raise HTTPException(
                status_code=400,
                detail=f"VMID {vmid} 不屬於此群組或尚未建立可用機器。",
            )

        live = live_by_vmid.get(vmid)
        live_type = str(live.get("type") or "") if live else ""
        live_status = str(live.get("status") or "") if live else ""
        if live is None or live_type not in {"qemu", "lxc"}:
            raise HTTPException(
                status_code=400,
                detail=f"VMID {vmid} 不是可執行的 VM/LXC。",
            )
        if live_status != "running":
            raise HTTPException(
                status_code=400,
                detail=f"VMID {vmid} 目前不是運行中，不能執行腳本。",
            )

        targets.append(
            {
                "vmid": vmid,
                "name": str(vmid),
                "type": live_type,
                "status": live_status,
                "node": live.get("node"),
                "user_id": member["user_id"],
                "email": member["email"],
                "full_name": member["full_name"],
            }
        )

    return targets


def create_script_run(
    *,
    session: Session,
    group_id: uuid.UUID,
    artifact_id: uuid.UUID,
    target_scope: TeacherJudgeScriptRunTargetScope,
    target_vmids: list[int],
    started_by: uuid.UUID | None,
) -> TeacherJudgeScriptRunPublic:
    if target_scope != TeacherJudgeScriptRunTargetScope.manual:
        raise HTTPException(status_code=400, detail="第一版只支援手動選擇執行機器。")

    artifact = get_artifact(
        session=session,
        group_id=group_id,
        artifact_id=artifact_id,
    )
    if artifact.status != TeacherJudgeScriptStatus.approved:
        raise HTTPException(status_code=400, detail="只有已核准的腳本可以執行。")

    targets = _resolve_running_targets(
        session=session,
        group_id=group_id,
        target_vmids=target_vmids,
    )
    if not targets:
        raise HTTPException(status_code=400, detail="請至少選擇一台運行中的 VM/LXC。")

    run = TeacherJudgeScriptRun(
        group_id=group_id,
        artifact_id=artifact.id,
        target_scope=target_scope,
        target_snapshot_json={
            "script": {
                "id": str(artifact.id),
                "name": artifact.name,
                "version": artifact.version,
                "template_key": artifact.template_key,
            },
            "targets": targets,
        },
        status=TeacherJudgeScriptRunStatus.pending,
        progress_json={
            "stage": "pending_executor",
            "total": len(targets),
            "done": 0,
            "targets": [
                {"vmid": target["vmid"], "name": target["name"], "status": "queued"}
                for target in targets
            ],
        },
        result_summary_json={},
        target_results_json={},
        started_by=started_by,
        started_at=None,
        updated_at=_now(),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return _run_to_public(run)
