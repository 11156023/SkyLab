"""Rubric API routes for AI Teacher Judge integration."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.ai.teacher_judge.config import settings
from app.ai.teacher_judge.export import export_to_excel
from app.ai.teacher_judge.schemas import RubricChatRequest, RubricExportRequest
from app.ai.teacher_judge.service import (
    analyze_rubric,
    chat_with_rubric,
    normalize_items_for_export,
)
from app.ai.teacher_judge.template_command_service import (
    SUPPORTED_TEMPLATE_KEYS,
    get_enabled_template_commands,
)
from app.api.deps import SessionDep
from app.api.deps.auth import InstructorUser
from app.services.rubric_parser import parse_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rubric", tags=["rubric"])


@router.post("/upload")
async def upload_rubric(
    current_user: InstructorUser,
    session: SessionDep,
    file: UploadFile = File(...),
    template_key: str = Form(default="linux"),
):
    """
    上傳評分表文件（.docx / .pdf），AI 解析並回傳結構化評分分析。

    限制：Teacher / Admin 角色可使用。
    """
    filename = file.filename or "unknown"
    allowed = {".docx", ".pdf"}
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"不支援的格式 '{suffix}'，目前接受：{', '.join(allowed)}",
        )

    template_key = template_key.strip().lower() or "linux"
    if template_key not in SUPPORTED_TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail="未知的評分環境 template。")

    file_bytes = await file.read()

    # 檔案大小檢查
    max_upload_size_bytes = settings.VLLM_MAX_UPLOAD_SIZE_MB * 1024 * 1024
    file_size_mb = len(file_bytes) / (1024 * 1024)
    if len(file_bytes) > max_upload_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"檔案大小 {file_size_mb:.1f}MB 超過限制（最大 {settings.VLLM_MAX_UPLOAD_SIZE_MB}MB）",
        )

    if not file_bytes:
        raise HTTPException(status_code=400, detail="上傳的檔案是空的。")

    try:
        raw_text = parse_document(filename, file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    if not raw_text.strip():
        raise HTTPException(
            status_code=422,
            detail="無法從文件中提取任何文字，請確認文件不是掃描版 PDF。",
        )

    template_commands = get_enabled_template_commands(session, template_key)

    logger.info(f"User {current_user.email} uploaded rubric file: {filename}")

    analysis, metrics = await analyze_rubric(
        raw_text,
        template_key=template_key,
        template_commands=template_commands,
    )
    return {
        "analysis": analysis.model_dump(),
        "ai_metrics": metrics,
        "template_key": template_key,
    }


@router.post("/chat")
async def chat(
    current_user: InstructorUser,
    session: SessionDep,
    chat_request: RubricChatRequest,
):
    """
    與 AI 對話，精煉評分表。

    rubric_context 帶入目前評分表的 JSON 字串。
    限制：Teacher / Admin 角色可使用。
    """
    template_key = chat_request.template_key.strip().lower() or "linux"
    if template_key not in SUPPORTED_TEMPLATE_KEYS:
        raise HTTPException(status_code=400, detail="未知的評分環境 template。")
    template_commands = get_enabled_template_commands(session, template_key)

    reply, updated_items, metrics = await chat_with_rubric(
        chat_request.messages,
        chat_request.rubric_context,
        is_refine=chat_request.is_refine,
        template_key=template_key,
        template_commands=template_commands,
    )
    return {
        "reply": reply,
        "updated_items": updated_items,  # None 或更新後的完整 item 列表
        "prompt_tokens": metrics["prompt_tokens"],
        "completion_tokens": metrics["completion_tokens"],
        "total_tokens": metrics["total_tokens"],
        "elapsed_seconds": metrics["elapsed_seconds"],
        "tokens_per_second": metrics["tokens_per_second"],
    }


@router.post("/download-excel")
async def download_excel(
    current_user: InstructorUser,
    payload: RubricExportRequest,
):
    """
    接收評分項目列表，產出並回傳 .xlsx 檔案。

    限制：Teacher / Admin 角色可使用。
    """
    items = normalize_items_for_export(payload.items)
    summary = payload.summary

    if not items:
        raise HTTPException(status_code=400, detail="沒有可匯出的評分項目。")

    logger.info(
        f"User {current_user.email} downloaded rubric excel with {len(items)} items"
    )

    excel_bytes = export_to_excel(items, summary=summary)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=rubric.xlsx"},
    )


@router.get("/health")
async def health_check():
    """健康檢查端點。"""
    return {
        "status": "ok",
        "vllm_configured": bool(settings.VLLM_MODEL_NAME),
    }
