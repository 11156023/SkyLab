"""克隆請求 fan-out 併發池。

clone 是 PVE 磁碟 I/O 重活 — 以獨立的 ``asyncio.Semaphore`` 限制同時
在跑的 provision 數（``GovernanceConfig.provision_max_concurrency``），
並以 ``bypass_semaphore=True`` 略過 runner 全域信號量，避免排隊等待的
clone 任務佔滿 runner slot、餓死發信/狀態同步等輕量任務。

防重複三層：runner ``task_id=provision-{request_id}`` 去重（本模組）→
DB ``SELECT FOR UPDATE SKIP LOCKED``（coordinator 既有）→
``migration_status``/vmid 再檢查（coordinator 既有）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from app.infrastructure.worker import background_tasks

logger = logging.getLogger(__name__)

_semaphore: asyncio.Semaphore | None = None
_semaphore_size: int = 0


def get_provision_semaphore(size: int) -> asyncio.Semaphore:
    """取得 provision 專用信號量；size 變更時重建。

    重建後，仍在舊 semaphore 上等待的任務會以舊上限跑完；
    新提交的任務立即採用新上限（下個 scheduler tick 生效）。
    """
    global _semaphore, _semaphore_size
    if _semaphore is None or _semaphore_size != size:
        _semaphore = asyncio.Semaphore(size)
        _semaphore_size = size
    return _semaphore


def reset_provision_semaphore() -> None:
    """測試用：清除全域信號量狀態。"""
    global _semaphore, _semaphore_size
    _semaphore = None
    _semaphore_size = 0


async def _execute_provision(request_id: uuid.UUID) -> None:
    from app.services.scheduling import (
        coordinator,  # noqa: PLC0415 — 避免 import cycle
    )

    await asyncio.to_thread(coordinator.process_single_request_start, request_id)


async def _provision_with_semaphore(
    request_id: uuid.UUID, concurrency: int
) -> None:
    async with get_provision_semaphore(concurrency):
        await _execute_provision(request_id)


def submit_provision(request_id: uuid.UUID, *, concurrency: int) -> str:
    """把單一 request 的 provision 丟進背景並行執行。

    同一 request 已在跑時（runner task_id 去重）為 no-op。
    """
    return background_tasks.submit_factory(
        lambda: _provision_with_semaphore(request_id, concurrency),
        name="provision",
        task_id=f"provision-{request_id}",
        bypass_semaphore=True,
    )
