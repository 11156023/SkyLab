"""範本系統的隊列任務註冊。

所有 @queue_task handler 集中在此模組，worker.py 以 import 副作用完成註冊。
實際業務邏輯放在 template_service / clone_service，handler 僅做參數解包與委派。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.infrastructure.queue import queue_task
from app.services.template import clone_service, template_service


@queue_task("queue.ping", timeout_seconds=30)
async def ping(
    task_id: uuid.UUID,  # noqa: ARG001 - handler 固定簽名
    payload: dict[str, Any],
) -> dict[str, Any]:
    """隊列健康檢查任務：原樣回傳 payload。"""
    return {"pong": True, "echo": payload}


@queue_task(template_service.TASK_CONVERT, timeout_seconds=1800)
async def convert_template(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """VM → 範本（關機 + convert-to-template）。"""
    return await asyncio.to_thread(
        template_service.run_convert_task, task_id, payload
    )


@queue_task(template_service.TASK_DELETE, timeout_seconds=1800)
async def delete_template(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """刪除範本（PVE 端刪除 + DB 軟刪除）。"""
    return await asyncio.to_thread(
        template_service.run_delete_task, task_id, payload
    )


@queue_task(template_service.TASK_UPDATE_CLONE, timeout_seconds=3600)
async def update_template_clone(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """更新循環：full clone 出暫存母機。"""
    return await asyncio.to_thread(
        template_service.run_update_clone_task, task_id, payload
    )


@queue_task(template_service.TASK_UPDATE_CONVERT, timeout_seconds=3600)
async def update_template_convert(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """更新循環：暫存機轉新版範本並汰換舊版。"""
    return await asyncio.to_thread(
        template_service.run_update_convert_task, task_id, payload
    )


@queue_task(template_service.TASK_UPDATE_CANCEL, timeout_seconds=1800)
async def update_template_cancel(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """更新循環：取消並銷毀暫存母機。"""
    return await asyncio.to_thread(
        template_service.run_update_cancel_task, task_id, payload
    )


@queue_task(clone_service.TASK_CLONE, timeout_seconds=3600)
async def clone_from_template(
    task_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """從範本克隆一台（linked 優先退 full，克隆後重配置）。"""
    return await asyncio.to_thread(
        clone_service.run_clone_task, task_id, payload
    )
