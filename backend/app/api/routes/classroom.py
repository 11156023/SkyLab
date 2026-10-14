"""虛擬教室互動 API 路由（thin controller，權限與編排在 classroom_service）"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

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
from app.services.classroom.presence import classroom_presence_hub
from app.services.classroom.vnc_session_manager import ClassroomSession, SessionMode
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
    # presence 是 event loop 上的記憶體狀態，先在這裡讀；其餘 DB 查詢丟到 worker thread
    online = classroom_presence_hub.online_user_ids_for_class(class_id)
    return await run_in_threadpool(
        classroom_service.list_class_students,
        session,
        class_id,
        current_user,
        cluster_resources=cluster_resources,
        online_user_ids=online,
    )


@router.get("/classes/{class_id}/broadcast-sources", response_model=list[ClassroomVm])
async def list_teaching_class_broadcast_sources(
    class_id: uuid.UUID,
    session: SessionDep,
    current_user: InstructorUser,
) -> list[ClassroomVm]:
    cluster_resources = await _safe_cluster_listing()
    # 只查 DB（不碰 session manager 的記憶體狀態），整段丟到 worker thread
    return await run_in_threadpool(
        classroom_service.list_class_broadcast_sources,
        session,
        class_id,
        current_user,
        cluster_resources=cluster_resources,
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


class _SessionSnapshot:
    """在 event loop 上拍下的 session 快照，給 worker thread 裡的查詢用。

    vnc_session_manager 的狀態只在 event loop 上改動、沒有加鎖，不能從別的
    執行緒直接讀；``list_sessions()`` 回傳的是各 session 的快照，可以安全帶走。
    """

    def __init__(self, sessions: list[ClassroomSession]) -> None:
        self._sessions = sessions

    def list_sessions(self) -> list[ClassroomSession]:
        return list(self._sessions)

    def find_broadcast_for_classes(
        self, class_ids: set[uuid.UUID]
    ) -> ClassroomSession | None:
        # 與 VncSessionManager.find_broadcast_for_classes 同一個規則與順序
        for live in self._sessions:
            if live.mode is SessionMode.broadcast and live.class_id in class_ids:
                return live
        return None


def _live_state(
    session: SessionDep, user: User, snapshot: _SessionSnapshot
) -> tuple[ClassroomSession | None, list[int]]:
    return (
        classroom_service.get_live_for_user(session, user, manager=snapshot),
        classroom_service.list_taken_over_vmids_for_user(
            session, user, manager=snapshot
        ),
    )


@router.get("/live", response_model=ClassroomLivePublic)
async def get_live_broadcast(
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassroomLivePublic:
    # 記憶體裡的 session 狀態在 loop 上讀，DB 查詢丟到 worker thread。
    # 經由 classroom_service 取 manager，與服務層自己用的是同一個實例
    manager = classroom_service.vnc_session_manager  # type: ignore[attr-defined]
    snapshot = _SessionSnapshot(manager.list_sessions())
    live, taken_over_vmids = await run_in_threadpool(
        _live_state, session, current_user, snapshot
    )
    return ClassroomLivePublic(
        session=_to_public(live) if live else None,
        taken_over_vmids=taken_over_vmids,
    )
