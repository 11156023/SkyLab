from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

from sqlalchemy.exc import OperationalError

from app.domain.scheduling.models import ScheduledTask
from app.domain.scheduling.tasks import run_sync_task

logger = logging.getLogger(__name__)

# 每輪 tick 先問一次「這個行程是不是 leader」；多 worker 部署時只有拿到鎖的
# 那個跑任務，其餘略過本輪。回傳 True 的 context 表示已取得。
LeaderGate = Callable[[], AbstractContextManager[bool]]


async def run_polling_scheduler(
    *,
    stop_event: asyncio.Event,
    interval_seconds: int,
    tasks: list[ScheduledTask],
    leader_gate: LeaderGate | None = None,
) -> None:
    database_unavailable = False
    was_leader: bool | None = None

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
                if is_leader:
                    for task in tasks:
                        try:
                            await run_sync_task(task)
                            if database_unavailable:
                                logger.info(
                                    "Scheduler database connection recovered; "
                                    "resuming scheduled tasks"
                                )
                                database_unavailable = False
                        except OperationalError as exc:
                            if not database_unavailable:
                                logger.warning(
                                    "Scheduler paused because the database is "
                                    "unavailable: %s",
                                    exc,
                                )
                                database_unavailable = True
                            break
                        except Exception:
                            logger.exception("Scheduled task '%s' failed", task.name)
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
