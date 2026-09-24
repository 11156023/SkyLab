"""任務註冊表：把業務 handler 包上 TaskRecord 狀態機後交給 arq。

Handler 簽名：``async def handler(task_id: uuid.UUID, payload: dict) -> dict | None``
回傳 dict 會存入 TaskRecord.result；含 ``vmid`` 鍵時同步寫入 resource_vmid。

失敗語意：handler 拋出例外 → TaskRecord 標記 failed 並重新拋出讓 arq 記錄。
worker 設定 max_tries=1 —— 克隆/轉範本非冪等，重試交由使用者重新發起。
handler 拋 ``arq.worker.Retry`` 代表「現在不能跑、稍後重排」（例如 provision
名額已滿）：TaskRecord 退回 queued，不算失敗；這類任務要自己設較高的
``max_tries``。worker 正常關機時 arq 會取消進行中的 task（CancelledError），
這裡一樣把 TaskRecord 標 failed，避免永遠停在 running。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from arq.typing import WorkerCoroutine
from arq.worker import Function, Retry
from arq.worker import func as arq_func
from sqlmodel import Session

from app.core.db import engine
from app.models import TaskRecordStatus
from app.repositories import task_record as task_record_repo

logger = logging.getLogger(__name__)

TaskHandler = Callable[[uuid.UUID, dict[str, Any]], Awaitable[dict[str, Any] | None]]

_registry: dict[str, tuple[TaskHandler, int]] = {}
# arq 端保留結果的秒數；None 用 worker 預設。設 0 代表完成後立刻釋放 job id，
# 讓「固定 job id 去重」的任務（例如 vm_request.provision）跑完就能再入列。
_keep_result_seconds: dict[str, int | None] = {}
# 每個任務的 arq max_tries；None 用 worker 預設（1）。會用 Retry 重排的任務要設高。
_max_tries: dict[str, int | None] = {}


def queue_task(
    name: str,
    *,
    timeout_seconds: int = 1800,
    keep_result_seconds: int | None = None,
    max_tries: int | None = None,
) -> Callable[[TaskHandler], TaskHandler]:
    """註冊一個隊列任務 handler（以 name 作為 arq function 名）。"""

    def decorator(handler: TaskHandler) -> TaskHandler:
        if name in _registry:
            raise ValueError(f"queue task '{name}' already registered")
        _registry[name] = (handler, timeout_seconds)
        _keep_result_seconds[name] = keep_result_seconds
        _max_tries[name] = max_tries
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


def _mark_requeued(task_id: uuid.UUID) -> None:
    with Session(engine) as session:
        task_record_repo.mark_task_requeued(session=session, task_id=task_id)


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
        except Retry:
            # 稍後重排：不是失敗，TaskRecord 退回 queued 等下一次
            logger.info("queue task '%s' (%s) deferred", name, record_id)
            await asyncio.to_thread(_mark_requeued, task_id)
            raise
        except BaseException as exc:
            # 含 CancelledError：worker 關機時 arq 取消進行中的 task，若不在這裡
            # 收尾，TaskRecord 會永遠停在 running
            logger.exception("queue task '%s' (%s) failed", name, record_id)
            await asyncio.to_thread(
                _mark_finished,
                task_id,
                TaskRecordStatus.failed,
                error=str(exc) or exc.__class__.__name__,
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
        arq_func(
            _wrap(name, handler),
            name=name,
            timeout=timeout,
            keep_result=_keep_result_seconds.get(name),
            max_tries=_max_tries.get(name),
        )

        for name, (handler, timeout) in _registry.items()
    ]



async def run_registered_task_locally(
    name: str,
    record_id: str,
    payload: dict[str, Any],
) -> None:
    """Run a registered queue task through the same TaskRecord state machine.

    Development and small single-process installations may intentionally run
    with Redis disabled.  They still need template conversion and clone tasks
    to execute instead of leaving templates permanently in ``creating``.
    """
    registered = _registry.get(name)
    if registered is None:
        raise RuntimeError(f"queue task '{name}' is not registered")
    handler, _ = registered
    runner = _wrap(name, handler)
    while True:
        try:
            await runner({}, record_id, payload)
            return
        except Retry as retry:
            # 沒有 arq 幫忙重排：自己等 defer 後再跑一次。任務留在 runner 裡
            # 算「進行中」，同 job id 的去重在等待期間仍然有效
            await asyncio.sleep((retry.defer_score or 0) / 1000)

