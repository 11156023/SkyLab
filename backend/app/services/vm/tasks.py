"""VM 建立相關的隊列任務註冊（worker 端執行）。

handler 只做參數解包與委派；註冊清單見 ``app.infrastructure.queue.modules``。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.infrastructure.queue import queue_task
from app.services.scheduling import provision_pool
from app.services.vm import batch_provision_service


@queue_task(
    provision_pool.TASK_PROVISION,
    timeout_seconds=3600,
    # 固定 job id 去重：完成後立刻釋放，失敗重試才入得了列
    keep_result_seconds=0,
    # 名額滿時以 Retry 重排（每次都算一次 try），上限要遠大於可能的等待輪數
    max_tries=provision_pool.PROVISION_MAX_TRIES,
)

async def provision_vm_request(
    task_id: uuid.UUID,  # noqa: ARG001 - handler 固定簽名
    payload: dict[str, Any],
) -> dict[str, Any]:
    """VM 申請佈建：clone 一台機器並掛到申請單上（worker 內限流）。"""
    return await provision_pool.run_provision_job(
        uuid.UUID(str(payload["request_id"])),
        concurrency=int(
            payload.get("concurrency") or provision_pool.DEFAULT_PROVISION_CONCURRENCY
        ),
    )


@queue_task(batch_provision_service.TASK_RUN_BATCH_JOB, timeout_seconds=6 * 3600)

async def run_batch_provision_job(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """班級／批次佈建：逐一建立 job 內每位成員的機器。"""
    return await asyncio.to_thread(
        batch_provision_service.run_batch_job_task, task_id, payload
    )
