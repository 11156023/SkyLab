from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.ai.pve_log.chat import chat as pve_chat
from app.ai.pve_log.collector import collect_snapshot
from app.ai.pve_log.schemas import (
    ChatRequest,
    ChatResponse,
    NetworkInterface,
    NodeInfo,
    ResourceConfig,
    ResourceStatus,
    ResourceSummary,
    SSHConfirmRequest,
    SSHExecRequest,
    SSHExecResult,
    StorageInfo,
    SystemSnapshot,
)
from app.api.deps import InstructorUser, SessionDep
from app.core.authorizers import require_group_access
from app.repositories import group as group_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/pve-log", tags=["ai-pve-log"])


async def _snapshot_or_500() -> SystemSnapshot:
    try:
        return await asyncio.to_thread(collect_snapshot)
    except Exception:
        logger.exception("收集 PVE 系統快照失敗")
        raise HTTPException(status_code=500, detail="收集 PVE 資料失敗，請稍後再試")


def _resolve_group_vmids(
    *,
    session: SessionDep,
    current_user: InstructorUser,
    group_id: uuid.UUID,
) -> set[int]:
    db_group = group_repo.get_group_by_id(session=session, group_id=group_id)
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")

    require_group_access(current_user, db_group.owner_id)
    member_vmids = group_repo.get_member_vmids(session=session, group_id=group_id)
    return {vmid for vmid in member_vmids.values() if vmid is not None}


@router.get("/system-snapshot", response_model=SystemSnapshot)
async def get_system_snapshot(
    _current_user: InstructorUser,
) -> SystemSnapshot:
    return await _snapshot_or_500()


@router.get("/nodes", response_model=list[NodeInfo])
async def get_nodes(
    _current_user: InstructorUser,
) -> list[NodeInfo]:
    snapshot = await _snapshot_or_500()
    return snapshot.nodes


@router.get("/storages", response_model=list[StorageInfo])
async def get_storages(
    _current_user: InstructorUser,
    node: str | None = Query(default=None),
) -> list[StorageInfo]:
    snapshot = await _snapshot_or_500()
    if node:
        return [s for s in snapshot.storages if s.node == node]
    return snapshot.storages


@router.get("/resources", response_model=list[ResourceSummary])
async def get_resources(
    _current_user: InstructorUser,
    node: str | None = Query(default=None),
    resource_type: str | None = Query(default=None, pattern="^(qemu|lxc)$"),
    status: str | None = Query(default=None, pattern="^(running|stopped)$"),
) -> list[ResourceSummary]:
    snapshot = await _snapshot_or_500()
    result = snapshot.resources
    if node:
        result = [r for r in result if r.node == node]
    if resource_type:
        result = [r for r in result if r.resource_type == resource_type]
    if status:
        result = [r for r in result if r.status == status]
    return result


@router.get("/resource-statuses", response_model=list[ResourceStatus])
async def get_resource_statuses(
    _current_user: InstructorUser,
    node: str | None = Query(default=None),
    vmid: int | None = Query(default=None),
) -> list[ResourceStatus]:
    snapshot = await _snapshot_or_500()
    result = snapshot.resource_statuses
    if node:
        result = [s for s in result if s.node == node]
    if vmid is not None:
        result = [s for s in result if s.vmid == vmid]
    return result


@router.get(
    "/resource-configs",
    response_model=list[ResourceConfig],
    response_model_exclude={"__all__": {"raw"}},
)
async def get_resource_configs(
    _current_user: InstructorUser,
    node: str | None = Query(default=None),
    vmid: int | None = Query(default=None),
) -> list[ResourceConfig]:
    snapshot = await _snapshot_or_500()
    result = snapshot.resource_configs
    if node:
        result = [c for c in result if c.node == node]
    if vmid is not None:
        result = [c for c in result if c.vmid == vmid]
    return result


@router.get("/network-interfaces", response_model=list[NetworkInterface])
async def get_network_interfaces(
    _current_user: InstructorUser,
    vmid: int | None = Query(default=None),
) -> list[NetworkInterface]:
    snapshot = await _snapshot_or_500()
    result = snapshot.network_interfaces
    if vmid is not None:
        result = [i for i in result if i.vmid == vmid]
    return result


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    _current_user: InstructorUser,
    session: SessionDep,
) -> ChatResponse:
    allowed_vmids = _resolve_group_vmids(
        session=session,
        current_user=_current_user,
        group_id=request.group_id,
    )
    try:
        return await pve_chat(
            message=request.message,
            history=request.messages,
            session=session,
            allowed_vmids=allowed_vmids,
        )
    except Exception:
        logger.exception("AI-PVE 對話失敗")
        raise HTTPException(status_code=500, detail="AI-PVE 對話失敗")


@router.post("/ssh/exec", response_model=SSHExecResult, tags=["ai-pve-log-ssh"])
async def post_ssh_exec(
    request: SSHExecRequest,
    _current_user: InstructorUser,
    session: SessionDep,
) -> SSHExecResult:
    from app.ai.pve_log.ssh_exec import ssh_exec as _ssh_exec

    try:
        return await _ssh_exec(request, session=session)
    except Exception as exc:
        logger.exception("SSH 執行失敗")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ssh/confirm", response_model=SSHExecResult, tags=["ai-pve-log-ssh"])
async def post_ssh_confirm(
    request: SSHConfirmRequest,
    _current_user: InstructorUser,
    session: SessionDep,
) -> SSHExecResult:
    from app.ai.pve_log.ssh_exec import confirm_exec as _confirm_exec

    try:
        return await _confirm_exec(request, session=session)
    except Exception as exc:
        logger.exception("SSH 確認失敗")
        raise HTTPException(status_code=500, detail=str(exc))
