from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.ai.pve_log.chat import chat as pve_chat
from app.ai.pve_log.history import PveHistoryValidationError
from app.ai.pve_log.schemas import (
    ChatRequest,
    ChatResponse,
    SSHConfirmRequest,
    SSHExecRequest,
    SSHExecResult,
)
from app.api.deps import AdminUser, SessionDep
from app.core.i18n import t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/pve-log", tags=["ai-pve-log"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: AdminUser,
    session: SessionDep,
) -> ChatResponse:
    try:
        return await pve_chat(
            message=request.message,
            history=request.messages,
            session=session,
            requester_id=current_user.id,
        )
    except PveHistoryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("AI-PVE 對話失敗")
        raise HTTPException(status_code=500, detail=t("aiPveLog.chatFailed"))


@router.post("/ssh/exec", response_model=SSHExecResult, tags=["ai-pve-log-ssh"])
async def post_ssh_exec(
    request: SSHExecRequest,
    current_user: AdminUser,
    session: SessionDep,
) -> SSHExecResult:
    from app.ai.pve_log.ssh_exec import ssh_exec as _ssh_exec

    try:
        return await _ssh_exec(
            request,
            session=session,
            requester_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("SSH 執行失敗")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ssh/confirm", response_model=SSHExecResult, tags=["ai-pve-log-ssh"])
async def post_ssh_confirm(
    request: SSHConfirmRequest,
    current_user: AdminUser,
    session: SessionDep,
) -> SSHExecResult:
    from app.ai.pve_log.ssh_exec import confirm_exec as _confirm_exec

    try:
        return await _confirm_exec(
            request,
            session=session,
            requester_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("SSH 確認失敗")
        raise HTTPException(status_code=500, detail=str(exc))
