from __future__ import annotations

from fastapi import APIRouter

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

router = APIRouter(prefix="/ai/navigation", tags=["ai-navigation"])


@router.post("/resolve", response_model=NavigationResolveResponse)
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
    )


@router.post("/intake", response_model=IntakeState)
def navigation_intake(request: IntakeRequest, _current_user: CurrentUser) -> IntakeState:
    """配置模式的下一個問題；需求問齊了就回 ready，交給推薦規劃。

    純本地判斷，不打模型——問問題不該花一次推論，也不該因為模型慢而卡住對話。
    """
    history = request.history
    if request.goal:
        history = [NavigationMessage(role="user", content=request.goal), *history]
    return read_intake(history, facts=request.facts, pending_key=request.pending_key)
