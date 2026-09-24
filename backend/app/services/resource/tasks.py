"""資源操作的隊列任務註冊（worker 端執行）。

handler 只做參數解包與委派，業務邏輯在各 service；註冊清單見
``app.infrastructure.queue.modules``。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.infrastructure.queue import queue_task
from app.services.resource import deletion_service, reset_service


@queue_task(reset_service.TASK_RESET, timeout_seconds=900)
async def reset_to_init_snapshot(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """一鍵重置：停機 → rollback 到 skylab-init → 恢復原電源狀態。"""
    return await asyncio.to_thread(reset_service.run_reset_task, task_id, payload)


@queue_task(deletion_service.TASK_DELETE, timeout_seconds=1800)
async def delete_resource(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """刪除申請：含 process_one_request 內建的重試與取消檢查。"""
    return await asyncio.to_thread(
        deletion_service.run_delete_task, task_id, payload
    )

