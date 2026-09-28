"""虛擬教室互動 API 路由（thin controller，權限與編排在 classroom_service）"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentUser, InstructorUser, SessionDep
from app.core.authorizers import require_classroom_monitor
from app.core.i18n import t
from app.models import User
from app.schemas.classroom import (
    ClassroomControlRequest,
    ClassroomLivePublic,
    ClassroomSessionCreate,
    ClassroomSessionPublic,
    ClassroomStudent,
    ClassroomVm,
)
from app.schemas.common import Message
from app.services.classroom import classroom_service
from app.services.classroom.vnc_session_manager import ClassroomSession
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/classroom", tags=["classroom"])


def _require_monitor_permission(user: User) -> None:
    """觀看與接管學生畫面要有「上課監看」權限，不只是班級擁有者。

    班級的 owner_id 不會因為帳號被降為學生而改變；只看擁有者的話，
    被降級的前任老師仍能對原班學生的 VM 開監看、接管鍵盤滑鼠。
    停止 session 刻意不檢查，讓發起者仍能把殘留的 session 收掉。
    """
    require_classroom_monitor(user, detail=t("classroom.monitor_forbidden"))


def _to_public(session: ClassroomSession) -> ClassroomSessionPublic:
    return ClassroomSessionPublic(
        id=session.id,
        vmid=session.vmid,
        mode=session.mode.value,
        class_id=session.class_id,
        started_by=session.started_by,
        controller_user_id=session.controller_user_id,
        subscriber_count=session.subscriber_count,
    )


async def _safe_cluster_listing() -> list[dict[str, Any]]:
    """叢集資源快照（經 services 層的 proxmox_service）；拿不到就當成空清單。

    classroom_service 的 list_* 以 ``cluster_resources`` 參數接收，測試可直接注入。
    """
    try:
        return await asyncio.to_thread(proxmox_service.list_all_resources)
    except Exception:
        logger.warning("Classroom: failed to list cluster resources", exc_info=True)
        return []


@router.get("/classes/{class_id}/students", response_model=list[ClassroomStudent])
async def list_teaching_class_students(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[ClassroomStudent]:
    cluster_resources = await _safe_cluster_listing()
    return classroom_service.list_class_students(
        session, class_id, current_user, cluster_resources=cluster_resources
    )


@router.get("/classes/{class_id}/broadcast-sources", response_model=list[ClassroomVm])
async def list_teaching_class_broadcast_sources(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[ClassroomVm]:
    cluster_resources = await _safe_cluster_listing()
    return classroom_service.list_class_broadcast_sources(
        session, class_id, current_user, cluster_resources=cluster_resources
    )


@router.post("/sessions", response_model=ClassroomSessionPublic)
async def create_classroom_session(
    body: ClassroomSessionCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomSessionPublic:
    _require_monitor_permission(current_user)
    if body.mode == "broadcast":
        live = await classroom_service.start_class_broadcast(
            session, current_user, body.vmid, body.class_id
        )
    else:
        live = await classroom_service.start_class_watch(
            session, current_user, body.vmid, body.class_id
        )
    return _to_public(live)


@router.delete("/sessions/{session_id}", response_model=Message)
async def stop_classroom_session(
    session_id: str,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    await classroom_service.stop_session(session, current_user, session_id)
    return Message(message="Classroom session stopped")


@router.post("/sessions/{session_id}/control", response_model=ClassroomSessionPublic)
async def set_classroom_control(
    session_id: str,
    body: ClassroomControlRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomSessionPublic:
    _require_monitor_permission(current_user)
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
    return ClassroomLivePublic(
        session=_to_public(live) if live else None,
        taken_over_vmids=classroom_service.list_taken_over_vmids_for_user(
            session, current_user
        ),
    )
