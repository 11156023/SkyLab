"""任務註冊表：把業務 handler 包上 TaskRecord 狀態機後交給 arq。

Handler 簽名：``async def handler(task_id: uuid.UUID, payload: dict) -> dict | None``
回傳 dict 會存入 TaskRecord.result；含 ``vmid`` 鍵時同步寫入 resource_vmid。

失敗語意：handler 拋出例外 → TaskRecord 標記 failed 並重新拋出讓 arq 記錄。
worker 設定 max_tries=1 —— 克隆/轉範本非冪等，重試交由使用者重新發起。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from arq.typing import WorkerCoroutine
from arq.worker import Function
from arq.worker import func as arq_func
from sqlmodel import Session

from app.core.db import engine
from app.models import TaskRecordStatus
from app.repositories import task_record as task_record_repo

logger = logging.getLogger(__name__)

TaskHandler = Callable[[uuid.UUID, dict[str, Any]], Awaitable[dict[str, Any] | None]]

_registry: dict[str, tuple[TaskHandler, int]] = {}


def queue_task(
    name: str, *, timeout_seconds: int = 1800
) -> Callable[[TaskHandler], TaskHandler]:
    """註冊一個隊列任務 handler（以 name 作為 arq function 名）。"""

    def decorator(handler: TaskHandler) -> TaskHandler:
        if name in _registry:
            raise ValueError(f"queue task '{name}' already registered")
        _registry[name] = (handler, timeout_seconds)
        return handler

    return decorator


def _mark_running(task_id: uuid.UUID) -> None:
    with Session(engine) as session:
        task_record_repo.mark_task_running(session=session, task_id=task_id)


def _mark_finished(
    task_id: uuid.UUID,
    status: TaskRecordStatus,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    resource_vmid: int | None = None
    if result is not None:
        raw_vmid = result.get("vmid")
        if isinstance(raw_vmid, int):
            resource_vmid = raw_vmid
    with Session(engine) as session:
        task_record_repo.mark_task_finished(
            session=session,
            task_id=task_id,
            status=status,
            result=result,
            error=error,
            resource_vmid=resource_vmid,
        )


def report_progress(task_id: uuid.UUID, progress: int) -> None:
    """供 handler 在執行中回報進度（0-100）。同步版，可在 to_thread 內呼叫。"""
    with Session(engine) as session:
        task_record_repo.set_task_progress(
            session=session, task_id=task_id, progress=progress
        )


async def report_progress_async(task_id: uuid.UUID, progress: int) -> None:
    await asyncio.to_thread(report_progress, task_id, progress)


def _wrap(name: str, handler: TaskHandler) -> WorkerCoroutine:
    async def runner(
        ctx: dict[str, Any],  # noqa: ARG001 - arq 固定簽名
        record_id: str,
        payload: dict[str, Any],
    ) -> None:
        task_id = uuid.UUID(record_id)
        await asyncio.to_thread(_mark_running, task_id)
        try:
            result = await handler(task_id, payload)
        except Exception as exc:
            logger.exception("queue task '%s' (%s) failed", name, record_id)
            await asyncio.to_thread(
                _mark_finished,
                task_id,
                TaskRecordStatus.failed,
                error=str(exc),
            )
            raise
        await asyncio.to_thread(
            _mark_finished,
            task_id,
            TaskRecordStatus.succeeded,
            result=result,
        )

    runner.__qualname__ = f"queue_task[{name}]"
    return cast(WorkerCoroutine, runner)


def registered_functions() -> list[Function]:
    """把所有已註冊 handler 轉為 arq Function 清單（worker 啟動時呼叫）。"""
    return [
        arq_func(_wrap(name, handler), name=name, timeout=timeout)
        for name, (handler, timeout) in _registry.items()
    ]
