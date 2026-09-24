"""克隆請求 fan-out：入列到 arq worker，並在 worker 內限制同時 clone 數。

clone 是 PVE 磁碟 I/O 重活。API 行程只負責入列（``submit_provision``），
真正的 clone 由 worker 的 ``vm_request.provision`` 任務執行。同時 clone 數以
``GovernanceConfig.provision_max_concurrency`` 限制：名額滿時 handler 拋
``arq.worker.Retry`` 把 job 重排到幾十秒後，而不是在 worker 內等 semaphore
——等待中的 job 不占 arq 的 max_jobs slot，reset／刪除／範本任務不會被
排隊的 clone 餓死。名額是每個 worker 行程各算一份；多開 worker 上限會乘倍。
job 存在 Redis，worker 重啟後續跑，API 重啟不再讓申請單卡到 30 分鐘的
stale 回收才被撿起。

防重複三層：arq job id ``vm_request:{request_id}`` 去重（排隊中／執行中的
同一單不會再入列；任務完成即釋放 id，失敗重試不受影響）→
DB ``SELECT FOR UPDATE SKIP LOCKED``（coordinator 既有）→
``provisioning_status``/vmid 再檢查（coordinator 既有）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from arq.worker import Retry
from sqlmodel import Session

from app.infrastructure.queue import enqueue_task_sync
from app.models import TaskRecord, VMProvisioningStatus

logger = logging.getLogger(__name__)

TASK_PROVISION = "vm_request.provision"
DEFAULT_PROVISION_CONCURRENCY = 2
# 名額滿時重排的間隔；每次重排算一次 arq try，PROVISION_MAX_TRIES 要撐得過
# 大班級同時開機時最長的等待（10000 × 15s ≈ 41 小時）
PROVISION_RETRY_DEFER_SECONDS = 15
PROVISION_MAX_TRIES = 10_000


class _PoolState:
    """目前 worker 行程內正在 clone 的數量（集中在物件上，避免 global 重新指派）。"""

    in_flight: int = 0


_pool = _PoolState()


def provision_task_id(request_id: uuid.UUID) -> str:
    """單一申請單的 provision job id；所有提交路徑都用這個做去重。"""
    return f"vm_request:{request_id}"


def in_flight_count() -> int:
    """本 worker 行程內正在 clone 的數量。"""
    return _pool.in_flight


def reset_in_flight() -> None:
    """測試用：清除行程內的 clone 計數。"""
    _pool.in_flight = 0


def submit_provision(
    session: Session,
    *,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    concurrency: int,
) -> TaskRecord | None:
    """把單一 request 的 provision 入列到 arq worker。

    同一 request 已在排隊／執行中時（job id 去重）回傳 None。
    """
    return enqueue_task_sync(
        session=session,
        task_type=TASK_PROVISION,
        user_id=user_id,
        payload={"request_id": str(request_id), "concurrency": int(concurrency)},
        job_id=provision_task_id(request_id),
    )


# ─── worker 端 ────────────────────────────────────────────────────────────────


async def _execute_provision(request_id: uuid.UUID) -> bool:
    from app.services.scheduling import (
        coordinator,  # noqa: PLC0415 — 避免 import cycle
    )

    return await asyncio.to_thread(coordinator.process_single_request_start, request_id)


def _provisioning_failure(request_id: uuid.UUID) -> str | None:
    """provision 後申請單若停在 failed，回傳錯誤訊息讓 TaskRecord 也標 failed。"""
    from app.core.db import engine  # noqa: PLC0415 — 避免 import cycle
    from app.repositories import vm_request as vm_request_repo  # noqa: PLC0415

    with Session(engine) as session:
        request = vm_request_repo.get_vm_request_by_id(
            session=session, request_id=request_id
        )
        if (
            request is not None
            and request.vmid is None
            and request.provisioning_status == VMProvisioningStatus.failed
        ):
            return request.provisioning_error or "provisioning failed"
    return None


async def run_provision_job(
    request_id: uuid.UUID, *, concurrency: int
) -> dict[str, Any]:
    """worker handler 本體：名額滿就 Retry 重排，否則執行 provision。"""
    if _pool.in_flight >= max(1, concurrency):
        raise Retry(defer=PROVISION_RETRY_DEFER_SECONDS)
    _pool.in_flight += 1
    try:
        started = await _execute_provision(request_id)
    finally:
        _pool.in_flight -= 1
    failure = await asyncio.to_thread(_provisioning_failure, request_id)
    if failure:
        raise RuntimeError(failure)
    return {"request_id": str(request_id), "started": bool(started)}


__all__ = [
    "DEFAULT_PROVISION_CONCURRENCY",
    "PROVISION_MAX_TRIES",
    "PROVISION_RETRY_DEFER_SECONDS",
    "TASK_PROVISION",
    "in_flight_count",
    "provision_task_id",
    "reset_in_flight",
    "run_provision_job",
    "submit_provision",
]

