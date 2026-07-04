"""虛擬教室互動 API 路由（thin controller，權限與編排在 classroom_service）"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, InstructorUser, SessionDep
from app.exceptions import BadRequestError
from app.infrastructure.proxmox import operations as proxmox_ops
from app.schemas.classroom import (
    ClassroomControlRequest,
    ClassroomLivePublic,
    ClassroomSessionCreate,
    ClassroomSessionPublic,
    ClassroomStudent,
)
from app.schemas.common import Message
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/classroom", tags=["classroom"])


def _to_public(session: ClassroomSession) -> ClassroomSessionPublic:
    return ClassroomSessionPublic(
        id=session.id,
        vmid=session.vmid,
        mode=session.mode.value,
        group_id=session.group_id,
        started_by=session.started_by,
        controller_user_id=session.controller_user_id,
        subscriber_count=session.subscriber_count,
    )


async def _safe_cluster_listing() -> list[dict[str, Any]]:
    try:
        return await asyncio.to_thread(proxmox_ops.list_all_resources)
    except Exception:
        logger.warning("Classroom: failed to list cluster resources", exc_info=True)
        return []


@router.get("/groups/{group_id}/students", response_model=list[ClassroomStudent])
async def list_classroom_students(
    group_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[ClassroomStudent]:
    cluster_resources = await _safe_cluster_listing()
    return classroom_service.list_classroom_students(
        session, group_id, current_user, cluster_resources=cluster_resources
    )


@router.post("/sessions", response_model=ClassroomSessionPublic)
async def create_classroom_session(
    body: ClassroomSessionCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomSessionPublic:
    if body.mode == "broadcast":
        if body.group_id is None:
            raise BadRequestError("group_id is required for broadcast sessions")
        live = await classroom_service.start_broadcast(
            session, current_user, body.vmid, body.group_id
        )
    else:
        live = await classroom_service.start_watch(session, current_user, body.vmid)
    return _to_public(live)


@router.delete("/sessions/{session_id}", response_model=Message)
async def stop_classroom_session(
    session_id: str,
    current_user: CurrentUser,
) -> Message:
    await classroom_service.stop_session(current_user, session_id)
    return Message(message="Classroom session stopped")


@router.post("/sessions/{session_id}/control", response_model=ClassroomSessionPublic)
async def set_classroom_control(
    session_id: str,
    body: ClassroomControlRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomSessionPublic:
    live = await classroom_service.set_control(
        session, current_user, session_id, body.action
    )
    return _to_public(live)


@router.get("/sessions", response_model=list[ClassroomSessionPublic])
async def list_classroom_sessions(
    current_user: InstructorUser,
) -> list[ClassroomSessionPublic]:
    return [_to_public(s) for s in classroom_service.list_sessions_for(current_user)]


@router.get("/live", response_model=ClassroomLivePublic)
async def get_live_broadcast(
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomLivePublic:
    live = classroom_service.get_live_for_user(session, current_user)
    return ClassroomLivePublic(session=_to_public(live) if live else None)
