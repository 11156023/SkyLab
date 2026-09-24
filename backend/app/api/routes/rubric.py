"""Rubric API routes for AI Teacher Judge integration."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.ai.teacher_judge.export import export_to_excel
from app.ai.teacher_judge.schemas import TeacherJudgeRubricExportRequest
from app.ai.teacher_judge.service import normalize_items_for_export
from app.api.deps.auth import InstructorUser
from app.core.i18n import t

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rubric", tags=["rubric"])


@router.post("/download-excel")
async def download_excel(
    current_user: InstructorUser,
    payload: TeacherJudgeRubricExportRequest,
) -> Response:
    """
    接收評分項目列表，產出並回傳 .xlsx 檔案。

    限制：Teacher / Admin 角色可使用。
    """
    items = normalize_items_for_export(payload.items)
    summary = payload.summary

    if not items:
        raise HTTPException(status_code=400, detail=t("rubric.noItemsToExport"))

    logger.info(
        f"User {current_user.email} downloaded rubric excel with {len(items)} items"
    )

    excel_bytes = export_to_excel(items, summary=summary)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=rubric.xlsx"},
    )

