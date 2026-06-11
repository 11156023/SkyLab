from __future__ import annotations

from fastapi import APIRouter

from app.ai.navigation.schemas import (
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
    return await resolve_navigation(request.query, current_user, session=session)

