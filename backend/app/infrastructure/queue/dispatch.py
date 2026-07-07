"""任務入列：建立 TaskRecord 並送進 arq 隊列。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlmodel import Session

from app.models import TaskRecord, TaskRecordStatus
from app.repositories import task_record as task_record_repo

from .arq_client import QUEUE_NAME, get_arq_pool

logger = logging.getLogger(__name__)


async def enqueue_task(
    *,
    session: Session,
    task_type: str,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    template_id: uuid.UUID | None = None,
) -> TaskRecord:
    """建立 TaskRecord 並入列；入列失敗時記錄 failed 後拋出。"""
    record = task_record_repo.create_task_record(
        session=session,
        task_type=task_type,
        user_id=user_id,
        payload=payload,
        template_id=template_id,
    )
    try:
        pool = await get_arq_pool()
        job = await pool.enqueue_job(
            task_type,
            str(record.id),
            payload,
            _job_id=str(record.id),
            _queue_name=QUEUE_NAME,
        )
        if job is None:
            raise RuntimeError(f"duplicate job id {record.id}")
    except Exception as exc:
        logger.exception("enqueue task '%s' failed", task_type)
        task_record_repo.mark_task_finished(
            session=session,
            task_id=record.id,
            status=TaskRecordStatus.failed,
            error=f"入列失敗: {exc}",
        )
        raise
    return record
