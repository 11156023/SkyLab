"""arq worker 進程設定。

啟動方式（docker compose worker 服務）::

    arq app.infrastructure.queue.worker.WorkerSettings

注意：所有任務模組必須在這裡 import，@queue_task 裝飾器才會註冊到 registry。
"""

from __future__ import annotations

import importlib
from typing import Any

from app.infrastructure.queue.arq_client import QUEUE_NAME, get_redis_settings
from app.infrastructure.queue.registry import registered_functions

# 任務模組副作用匯入（註冊 @queue_task handler），不直接使用模組物件
importlib.import_module("app.services.template.tasks")


class WorkerSettings:
    """arq CLI 讀取的設定類別。"""

    functions = registered_functions()
    redis_settings = get_redis_settings()
    queue_name = QUEUE_NAME
    # 克隆/轉範本非冪等，失敗不自動重試
    max_tries = 1
    job_timeout = 3600
    # 併發上限：避免同時打爆 PVE / DB 連線池
    max_jobs = 8
    health_check_interval = 60

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:  # noqa: ARG004
        pass

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:  # noqa: ARG004
        pass
