"""Isolated AI PVE machine-template test routes."""

from fastapi import APIRouter, HTTPException

from app.ai.pve_log.history import PveHistoryValidationError
from app.ai.pve_template import service
from app.ai.pve_template.schemas import (
    AIPVETemplateChatRequest,
    AIPVETemplateChatResponse,
    AIPVETemplateRead,
    AIPVETemplateSSHConfirmRequest,
)
from app.api.deps import InstructorUser, SessionDep

router = APIRouter(prefix="/ai/pve-template", tags=["ai-pve-template"])


@router.get("/templates", response_model=list[AIPVETemplateRead])
async def get_templates(
    _current_user: InstructorUser,
    session: SessionDep,
) -> list[AIPVETemplateRead]:
    return service.list_templates(session=session)


@router.post("/chat", response_model=AIPVETemplateChatResponse)
async def chat(
    request: AIPVETemplateChatRequest,
    current_user: InstructorUser,
    session: SessionDep,
) -> AIPVETemplateChatResponse:
    try:
        return await service.chat(
            request=request,
            current_user=current_user,
            session=session,
        )
    except PveHistoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ssh/confirm", response_model=AIPVETemplateChatResponse)
async def confirm_ssh(
    request: AIPVETemplateSSHConfirmRequest,
    current_user: InstructorUser,
    session: SessionDep,
) -> AIPVETemplateChatResponse:
    try:
        return await service.confirm_ssh(
            request=request,
            current_user=current_user,
            session=session,
        )
    except PveHistoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
