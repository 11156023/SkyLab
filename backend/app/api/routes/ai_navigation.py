from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.navigation.intake import read_intake
from app.ai.navigation.schemas import (
    IntakeRequest,
    IntakeState,
    NavigationMessage,
    NavigationResolveRequest,
    NavigationResolveResponse,
)
from app.ai.navigation.service import resolve_navigation
from app.api.deps import CurrentUser, SessionDep
from app.api.deps.rate_limit import rate_limit_by_user

router = APIRouter(prefix="/ai/navigation", tags=["ai-navigation"])

# /resolve 會打模型，/intake 不會；兩支都節流，避免單一帳號整頁連打
_NAVIGATION_RATE_LIMIT = Depends(
    rate_limit_by_user(scope="ai-navigation", limit=30, window_seconds=60)
)


@router.post(
    "/resolve",
    response_model=NavigationResolveResponse,
    dependencies=[_NAVIGATION_RATE_LIMIT],
)
async def resolve_navigation_route(
    request: NavigationResolveRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> NavigationResolveResponse:
    return await resolve_navigation(
        request.query,
        current_user,
        session=session,
        history=request.history,
        current_path=request.current_path,
        surface_id=request.surface_id,
        screen_state=request.screen_state,
        active_flow_id=request.active_flow_id,
        pending_flow_ids=request.pending_flow_ids,
    )


@router.post(
    "/intake",
    response_model=IntakeState,
    dependencies=[_NAVIGATION_RATE_LIMIT],
)
def navigation_intake(request: IntakeRequest, _current_user: CurrentUser) -> IntakeState:
    """配置模式的下一個問題；需求問齊了就回 ready，交給推薦規劃。

    純本地判斷，不打模型——問問題不該花一次推論，也不該因為模型慢而卡住對話。
    """
    history = request.history
    if request.goal:
        history = [NavigationMessage(role="user", content=request.goal), *history]
    return read_intake(history, facts=request.facts, pending_key=request.pending_key)
