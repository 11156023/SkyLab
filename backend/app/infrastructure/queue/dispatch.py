"""任務入列：建立 TaskRecord 並送進 arq 隊列。

``enqueue_task`` 給 async 路由／服務用；``enqueue_task_sync`` 給跑在
threadpool 的 sync 路由用（把 arq 的 async enqueue 丟回主 event loop 等結果）。

分工原則：``_dispatch`` 這個 coroutine 只碰 Redis／本機 runner，不碰 DB
session。TaskRecord 的建立、去重時的刪除、入列失敗時標 failed，一律在
呼叫端自己的執行緒做，session 不會跨執行緒使用。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlmodel import Session

from app.features.ai.config import settings
from app.models import TaskRecord, TaskRecordStatus
from app.repositories import task_record as task_record_repo

from .arq_client import QUEUE_NAME, get_arq_pool

logger = logging.getLogger(__name__)

# sync 路由等 enqueue 完成的上限：Redis 正常時是毫秒級，逾時代表 Redis 有問題
ENQUEUE_SYNC_TIMEOUT_SECONDS = 10.0


class DuplicateJobError(Exception):
    """同 ``job_id`` 的 job 還在排隊或執行中（呼叫端據此刪掉多建的 TaskRecord）。"""


async def enqueue_task(
    *,
    session: Session,
    task_type: str,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    template_id: uuid.UUID | None = None,
    job_id: str | None = None,
) -> TaskRecord | None:
    """建立 TaskRecord 並入列；入列失敗時記錄 failed 後拋出。

    ``job_id`` 指定 arq job id 以做跨行程去重：同 id 的 job 還在排隊或執行中
    時不會再入列，剛建立的 TaskRecord 會被刪掉並回傳 None。未指定時以
    TaskRecord id 當 job id（永不重複）。
    """
    if job_id is not None and await _job_exists(job_id):
        # 先問再建：排程每 tick 都會對排隊中的申請單重送，這裡擋掉就不用
        # 每輪都 INSERT 再 DELETE 一筆 TaskRecord
        return None
    record = task_record_repo.create_task_record(
        session=session,
        task_type=task_type,
        user_id=user_id,
        payload=payload,
        template_id=template_id,
    )
    try:
        await _dispatch(
            record_id=record.id, task_type=task_type, payload=payload, job_id=job_id
        )
    except DuplicateJobError:
        _discard_duplicate_record(session=session, record=record, job_id=job_id)
        return None
    except Exception as exc:
        _mark_enqueue_failed(session=session, record=record, task_type=task_type, exc=exc)
        raise
    return record


def enqueue_task_sync(
    *,
    session: Session,
    task_type: str,
    user_id: uuid.UUID,
    payload: dict[str, Any],
    template_id: uuid.UUID | None = None,
    job_id: str | None = None,
) -> TaskRecord | None:
    """``enqueue_task`` 的 sync 版：從 threadpool（sync 路由）呼叫。

    TaskRecord 在呼叫端執行緒寫入；只有 arq 的 enqueue 需要 event loop，
    透過 ``run_coroutine_threadsafe`` 丟回 lifespan 綁定的主 loop 等待完成。
    在 event loop 執行緒上呼叫會直接拋錯（那裡應該 await ``enqueue_task``）。
    """
    from app.infrastructure.worker import (
        get_runner,  # noqa: PLC0415 — 避免 import cycle
    )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "enqueue_task_sync called on the event loop thread; await enqueue_task instead"
        )

    runner = get_runner()
    loop = runner.bound_loop() if runner is not None else None

    def run_on_loop(coro: Any) -> Any:
        if loop is None or loop.is_closed():
            # 沒有 lifespan（單元測試、CLI 腳本）：就地跑一個 loop 完成入列，
            # 結束時關掉 arq pool，免得它綁在已關閉的 loop 上被下一次重用
            return asyncio.run(_run_then_close_pool(coro))
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=ENQUEUE_SYNC_TIMEOUT_SECONDS)
        except TimeoutError:
            # 逾時後 coroutine 可能還會跑完並真的入列；取消它，避免 API 已回
            # 500、job 卻在背景默默送出
            future.cancel()
            raise

    if job_id is not None and run_on_loop(_job_exists(job_id)):
        return None
    record = task_record_repo.create_task_record(
        session=session,
        task_type=task_type,
        user_id=user_id,
        payload=payload,
        template_id=template_id,
    )
    try:
        run_on_loop(
            _dispatch(
                record_id=record.id, task_type=task_type, payload=payload, job_id=job_id
            )
        )
    except DuplicateJobError:
        _discard_duplicate_record(session=session, record=record, job_id=job_id)
        return None
    except Exception as exc:
        _mark_enqueue_failed(session=session, record=record, task_type=task_type, exc=exc)
        raise
    return record


def _discard_duplicate_record(
    *, session: Session, record: TaskRecord, job_id: str | None
) -> None:
    """同 job id 已在隊列中：這筆 TaskRecord 不會有人執行，直接刪掉。"""
    logger.info(
        "queue task '%s' job_id=%s already queued; discarding duplicate record %s",
        record.task_type, job_id, record.id,
    )
    session.delete(record)
    session.commit()


def _mark_enqueue_failed(
    *, session: Session, record: TaskRecord, task_type: str, exc: BaseException
) -> None:
    logger.exception("enqueue task '%s' failed", task_type)
    task_record_repo.mark_task_finished(
        session=session,
        task_id=record.id,
        status=TaskRecordStatus.failed,
        error=f"入列失敗: {exc}",
    )


async def _run_then_close_pool(coro: Any) -> Any:
    from .arq_client import close_arq_pool  # noqa: PLC0415

    try:
        return await coro
    finally:
        if settings.redis_enabled:
            await close_arq_pool()


async def _job_exists(job_id: str) -> bool:
    """同 job id 的任務是否已在排隊／執行中（arq 或本機 runner）。"""
    if not settings.redis_enabled:
        from app.infrastructure.worker import is_active  # noqa: PLC0415

        return is_active(job_id)
    from arq.jobs import Job, JobStatus  # noqa: PLC0415

    pool = await get_arq_pool()
    status = await Job(job_id, redis=pool, _queue_name=QUEUE_NAME).status()
    return status not in (JobStatus.not_found, JobStatus.complete)


async def _dispatch(
    *,
    record_id: uuid.UUID,

    task_type: str,
    payload: dict[str, Any],
    job_id: str | None = None,
) -> None:
    """把任務送進 arq（或 REDIS_ENABLED=false 時的本機 runner）。

    只做入列，不碰 DB；被 ``job_id`` 去重擋下時拋 ``DuplicateJobError``。
    """
    if not settings.redis_enabled:
        # Import task modules lazily so their decorators populate the
        # registry without creating an import cycle during app startup.
        from app.infrastructure.worker import is_active, submit  # noqa: PLC0415

        from .modules import import_task_modules  # noqa: PLC0415
        from .registry import run_registered_task_locally  # noqa: PLC0415

        import_task_modules()
        local_task_id = job_id or str(record_id)
        if job_id is not None and is_active(local_task_id):
            raise DuplicateJobError(job_id)
        submitted_id = submit(
            run_registered_task_locally(task_type, str(record_id), payload),
            name=task_type,
            task_id=local_task_id,
        )
        if not submitted_id:
            raise RuntimeError("local background runner is not available")
        return

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        task_type,
        str(record_id),
        payload,
        _job_id=job_id or str(record_id),
        _queue_name=QUEUE_NAME,
    )
    if job is None:
        if job_id is not None:
            raise DuplicateJobError(job_id)
        raise RuntimeError(f"duplicate job id {record_id}")


__all__ = [
    "DuplicateJobError",
    "ENQUEUE_SYNC_TIMEOUT_SECONDS",
    "enqueue_task",
    "enqueue_task_sync",
]
