from __future__ import annotations

from fastapi import APIRouter

from app.ai.contextual_help.schemas import (
    ExplainRequest,
    ExplainResponse,
    SurfacePublic,
)
from app.ai.contextual_help.service import explain
from app.ai.contextual_help.surfaces import get_surfaces_for_user
from app.api.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/ai/contextual-help", tags=["ai-contextual-help"])


@router.post("/explain", response_model=ExplainResponse)
async def explain_screen(
    request: ExplainRequest,
    session: SessionDep,
    current_user: CurrentUser,
) -> ExplainResponse:
    """解釋使用者目前看到的畫面：欄位用途、被擋的原因，或這一頁在做什麼。

    只回答畫面相關的問題，不導覽、不產生步驟、不回傳路徑。
    """
    return await explain(request, current_user, session=session)


@router.get("/surfaces", response_model=list[SurfacePublic])
def list_surfaces(current_user: CurrentUser) -> list[SurfacePublic]:
    """使用者看得到的畫面清單，讓前端把目前路徑對應成 surface_id。

    只回身分允許的畫面，所以這支同時也是權限過濾後的結果。
    """
    return [
        SurfacePublic(
            id=surface.id,
            path=surface.path,
            title=surface.title,
            purpose=surface.purpose,
            has_fields=bool(surface.elements),
        )
        for surface in get_surfaces_for_user(current_user)
    ]
