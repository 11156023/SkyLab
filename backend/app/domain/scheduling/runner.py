from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol

from sqlalchemy.exc import OperationalError

from app.domain.scheduling.models import ScheduledTask
from app.domain.scheduling.tasks import run_sync_task

logger = logging.getLogger(__name__)

# 每輪 tick 先問一次「這個行程是不是 leader」；多 worker 部署時只有拿到鎖的
# 那個跑任務，其餘略過本輪。回傳 True 的 context 表示已取得。
LeaderGate = Callable[[], AbstractContextManager[bool]]


class LoopObserver(Protocol):
    """排程迴圈的觀察者（心跳／指標）。

    domain 層只定義介面，實作在 ``services/monitoring/heartbeat_service``；
    observer 自己出錯不能讓排程停下來，runner 會吞掉並記 warning。
    """

    async def on_start(self, task_names: list[str]) -> None: ...

    async def on_tick(self, *, is_leader: bool) -> None: ...

    async def on_task(
        self,
        task_name: str,
        *,
        ok: bool,
        duration_seconds: float,
        error: BaseException | None,
    ) -> None: ...


async def _notify(
    observer: LoopObserver | None, method: str, *args: Any, **kwargs: Any
) -> None:
    if observer is None:
        return
    try:
        await getattr(observer, method)(*args, **kwargs)
    except Exception:
        logger.warning("Scheduler observer %s failed", method, exc_info=True)


async def run_polling_scheduler(
    *,
    stop_event: asyncio.Event,
    interval_seconds: int,
    tasks: list[ScheduledTask],
    leader_gate: LeaderGate | None = None,
    observer: LoopObserver | None = None,
) -> None:
    database_unavailable = False
    was_leader: bool | None = None
    await _notify(observer, "on_start", [task.name for task in tasks])

    while not stop_event.is_set():
        gate = leader_gate() if leader_gate is not None else nullcontext(True)
        try:
            # leader 鎖是同步的 DB I/O（engine.connect + advisory lock）：丟到
            # 執行緒，DB 慢或掛掉時才不會把整個 event loop 凍住
            is_leader = await asyncio.to_thread(gate.__enter__)
            try:
                if is_leader != was_leader:
                    logger.info(
                        "Scheduler leadership: %s",
                        "acquired" if is_leader else "held by another worker",
                    )
                    was_leader = is_leader
                await _notify(observer, "on_tick", is_leader=bool(is_leader))
                if is_leader:
                    for task in tasks:
                        started = time.perf_counter()
                        try:
                            await run_sync_task(task)
                            await _notify(
                                observer,
                                "on_task",
                                task.name,
                                ok=True,
                                duration_seconds=time.perf_counter() - started,
                                error=None,
                            )
                            if database_unavailable:
                                logger.info(
                                    "Scheduler database connection recovered; "
                                    "resuming scheduled tasks"
                                )
                                database_unavailable = False
                        except OperationalError as exc:
                            await _notify(
                                observer,
                                "on_task",
                                task.name,
                                ok=False,
                                duration_seconds=time.perf_counter() - started,
                                error=exc,
                            )
                            if not database_unavailable:
                                logger.warning(
                                    "Scheduler paused because the database is "
                                    "unavailable: %s",
                                    exc,
                                )
                                database_unavailable = True
                            break
                        except Exception as exc:
                            logger.exception("Scheduled task '%s' failed", task.name)
                            await _notify(
                                observer,
                                "on_task",
                                task.name,
                                ok=False,
                                duration_seconds=time.perf_counter() - started,
                                error=exc,
                            )
            finally:
                await asyncio.to_thread(gate.__exit__, None, None, None)
        except OperationalError as exc:

            if not database_unavailable:
                logger.warning(
                    "Scheduler paused because the database is unavailable: %s", exc
                )
                database_unavailable = True
        except Exception:
            logger.exception("Scheduler leader gate failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue
