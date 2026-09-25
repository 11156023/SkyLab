"""隊列任務模組清單。

``@queue_task`` 靠 import 副作用註冊；worker 啟動與 REDIS_ENABLED=false 的
本機 fallback 都要載入同一份清單，否則 worker 會回報「task not registered」。
新增任務模組時只改這裡。
"""

from __future__ import annotations

import importlib

TASK_MODULES: tuple[str, ...] = (
    "app.services.template.tasks",
    "app.services.resource.tasks",
    "app.services.vm.tasks",
)


def import_task_modules() -> None:
    """載入所有任務模組（重複呼叫是 no-op，模組快取在 sys.modules）。"""
    for name in TASK_MODULES:
        importlib.import_module(name)


__all__ = ["TASK_MODULES", "import_task_modules"]
