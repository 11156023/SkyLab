"""排程器／背景迴圈心跳：每個任務最後一次執行、成功、連續失敗次數。

寫入端是 ``HeartbeatObserver``（掛在 ``run_polling_scheduler`` 上，跑在 event
loop 裡，用 async Redis）；讀取端 ``snapshot()`` 給系統健康 API 與告警 tick 用
（跑在執行緒裡，用同步 Redis）。

資料同時留一份在行程記憶體：Redis 停用或暫時連不上時仍看得到本行程的狀態；
Redis 正常時以 Redis 為準，多開 backend 也能看到 leader 那台寫的資料。

Redis key（TTL 7 天，停用的任務自然消失）：
- ``skylab:hb:loops``／``skylab:hb:tasks``：已知迴圈與「迴圈:任務」清單（set）
- ``skylab:hb:loop:<loop>``：last_tick_at、leader_last_tick_at、is_leader、instance…
- ``skylab:hb:task:<loop>:<task>``：last_run_at、last_success_at、consecutive_failures…
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core import metrics
from app.infrastructure.redis import get_redis
from app.infrastructure.redis.sync_client import get_sync_redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "skylab:hb"
LOOPS_SET = f"{KEY_PREFIX}:loops"
TASKS_SET = f"{KEY_PREFIX}:tasks"
KEY_TTL_SECONDS = 7 * 24 * 3600
MAX_ERROR_LENGTH = 300
# Redis 寫入失敗的 warning 節流：Redis 掛掉時每個任務每輪都失敗，不要洗版
_WARN_INTERVAL_SECONDS = 300.0

_INSTANCE = f"{socket.gethostname()}:{os.getpid()}"


def _loop_key(loop: str) -> str:
    return f"{KEY_PREFIX}:loop:{loop}"


def _task_key(loop: str, task: str) -> str:
    return f"{KEY_PREFIX}:task:{loop}:{task}"


@dataclass
class _MemoryStore:
    lock: threading.Lock = field(default_factory=threading.Lock)
    loops: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    last_redis_warning: float = 0.0


_memory = _MemoryStore()


def reset_memory() -> None:
    """測試用：清空行程內的心跳資料。"""
    with _memory.lock:
        _memory.loops.clear()
        _memory.tasks.clear()


def _format_error(error: BaseException | None) -> str | None:
    if error is None:
        return None
    text = f"{type(error).__name__}: {error}".strip()
    return text[:MAX_ERROR_LENGTH]


def _warn_redis_failure() -> None:
    now = time.monotonic()
    if now - _memory.last_redis_warning >= _WARN_INTERVAL_SECONDS:
        _memory.last_redis_warning = now
        logger.warning("Heartbeat write to Redis failed; keeping in-memory copy", exc_info=True)


class HeartbeatObserver:
    """``run_polling_scheduler`` 的 observer：記錄心跳並更新 Prometheus 指標。"""

    def __init__(self, loop: str, *, interval_seconds: int) -> None:
        self.loop = loop
        self.interval_seconds = int(interval_seconds)

    async def on_start(self, task_names: list[str]) -> None:
        with _memory.lock:
            _memory.loops.setdefault(self.loop, {}).update(
                loop=self.loop, interval_seconds=self.interval_seconds
            )
            for name in task_names:
                _memory.tasks.setdefault((self.loop, name), {"loop": self.loop, "task": name})
        redis = await get_redis()
        if redis is None:
            return
        try:
            pipe = redis.pipeline(transaction=False)
            pipe.sadd(LOOPS_SET, self.loop)
            pipe.hset(
                _loop_key(self.loop),
                mapping={"loop": self.loop, "interval_seconds": self.interval_seconds},
            )
            pipe.expire(_loop_key(self.loop), KEY_TTL_SECONDS)
            if task_names:
                pipe.sadd(TASKS_SET, *[f"{self.loop}:{name}" for name in task_names])
            pipe.expire(LOOPS_SET, KEY_TTL_SECONDS)
            pipe.expire(TASKS_SET, KEY_TTL_SECONDS)
            await pipe.execute()
        except Exception:
            _warn_redis_failure()

    async def on_tick(self, *, is_leader: bool) -> None:
        now = time.time()
        metrics.SCHEDULER_LOOP_LAST_TICK.labels(loop=self.loop).set(now)
        metrics.SCHEDULER_LOOP_IS_LEADER.labels(loop=self.loop).set(1 if is_leader else 0)
        fields: dict[str, Any] = {
            "loop": self.loop,
            "interval_seconds": self.interval_seconds,
            "last_tick_at": now,
        }
        if is_leader:
            # 只有 leader 會真的跑任務；「多久沒有任何人跑」看這個欄位
            fields["leader_last_tick_at"] = now
            fields["leader_instance"] = _INSTANCE
        with _memory.lock:
            _memory.loops.setdefault(self.loop, {}).update(fields)
        redis = await get_redis()
        if redis is None:
            return
        try:
            pipe = redis.pipeline(transaction=False)
            pipe.hset(_loop_key(self.loop), mapping=fields)
            pipe.expire(_loop_key(self.loop), KEY_TTL_SECONDS)
            pipe.sadd(LOOPS_SET, self.loop)
            await pipe.execute()
        except Exception:
            _warn_redis_failure()

    async def on_task(
        self,
        task_name: str,
        *,
        ok: bool,
        duration_seconds: float,
        error: BaseException | None,
    ) -> None:
        now = time.time()
        labels = {"loop": self.loop, "task": task_name}
        metrics.SCHEDULER_TASK_RUNS.labels(**labels, result="success" if ok else "failure").inc()
        metrics.SCHEDULER_TASK_DURATION.labels(**labels).observe(duration_seconds)

        with _memory.lock:
            entry = _memory.tasks.setdefault(
                (self.loop, task_name), {"loop": self.loop, "task": task_name}
            )
            entry["last_run_at"] = now
            entry["last_duration_ms"] = round(duration_seconds * 1000, 1)
            entry["total_runs"] = int(entry.get("total_runs", 0)) + 1
            if ok:
                entry["last_success_at"] = now
                entry["consecutive_failures"] = 0
            else:
                entry["last_failure_at"] = now
                entry["last_error"] = _format_error(error)
                entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
                entry["total_failures"] = int(entry.get("total_failures", 0)) + 1
            consecutive = entry["consecutive_failures"]

        metrics.SCHEDULER_TASK_CONSECUTIVE_FAILURES.labels(**labels).set(consecutive)
        if ok:
            metrics.SCHEDULER_TASK_LAST_SUCCESS.labels(**labels).set(now)

        redis = await get_redis()
        if redis is None:
            return
        key = _task_key(self.loop, task_name)
        try:
            pipe = redis.pipeline(transaction=False)
            base: dict[str, Any] = {
                "loop": self.loop,
                "task": task_name,
                "last_run_at": now,
                "last_duration_ms": round(duration_seconds * 1000, 1),
                "instance": _INSTANCE,
            }
            if ok:
                base["last_success_at"] = now
                base["consecutive_failures"] = 0
                pipe.hset(key, mapping=base)
            else:
                base["last_failure_at"] = now
                base["last_error"] = _format_error(error) or ""
                pipe.hset(key, mapping=base)
                pipe.hincrby(key, "consecutive_failures", 1)
                pipe.hincrby(key, "total_failures", 1)
            pipe.hincrby(key, "total_runs", 1)
            pipe.expire(key, KEY_TTL_SECONDS)
            pipe.sadd(TASKS_SET, f"{self.loop}:{task_name}")
            await pipe.execute()
        except Exception:
            _warn_redis_failure()


# ─── 讀取端（同步，給執行緒呼叫） ─────────────────────────────────────────

_FLOAT_FIELDS = (
    "interval_seconds",
    "last_tick_at",
    "leader_last_tick_at",
    "last_run_at",
    "last_success_at",
    "last_failure_at",
    "last_duration_ms",
)
_INT_FIELDS = ("consecutive_failures", "total_runs", "total_failures")


def _coerce(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(raw)
    for name in _FLOAT_FIELDS:
        if name in out and out[name] not in (None, ""):
            try:
                out[name] = float(out[name])
            except (TypeError, ValueError):
                out[name] = None
    for name in _INT_FIELDS:
        if name in out and out[name] not in (None, ""):
            try:
                out[name] = int(out[name])
            except (TypeError, ValueError):
                out[name] = 0
    if out.get("last_error") == "":
        out["last_error"] = None
    return out


@dataclass(frozen=True)
class HeartbeatSnapshot:
    loops: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    source: str  # "redis" | "memory"


def _memory_snapshot() -> HeartbeatSnapshot:
    with _memory.lock:
        loops = [_coerce(v) for v in _memory.loops.values()]
        tasks = [_coerce(v) for v in _memory.tasks.values()]
    return HeartbeatSnapshot(loops=loops, tasks=tasks, source="memory")


def snapshot() -> HeartbeatSnapshot:
    """讀出所有迴圈與任務的心跳；Redis 可用時以 Redis 為準，否則用本行程記憶體。"""
    client = get_sync_redis()
    if client is None:
        return _memory_snapshot()
    try:
        loop_names = sorted(client.smembers(LOOPS_SET) or [])
        task_ids = sorted(client.smembers(TASKS_SET) or [])
        pipe = client.pipeline(transaction=False)
        for loop in loop_names:
            pipe.hgetall(_loop_key(loop))
        for task_id in task_ids:
            loop, _, task = task_id.partition(":")
            pipe.hgetall(_task_key(loop, task))
        results = pipe.execute()
    except Exception:
        logger.debug("Heartbeat snapshot from Redis failed; using memory", exc_info=True)
        return _memory_snapshot()

    loops_data = results[: len(loop_names)]
    tasks_data = results[len(loop_names) :]
    loops = [
        _coerce({"loop": name, **(data or {})})
        for name, data in zip(loop_names, loops_data, strict=True)
    ]
    tasks: list[dict[str, Any]] = []
    for task_id, data in zip(task_ids, tasks_data, strict=True):
        loop, _, task = task_id.partition(":")
        tasks.append(_coerce({"loop": loop, "task": task, **(data or {})}))

    # 本行程剛註冊、Redis 還沒寫進去的迴圈／任務也列出來
    known_loops = {item["loop"] for item in loops}
    known_tasks = {(item["loop"], item["task"]) for item in tasks}
    memory = _memory_snapshot()
    loops.extend(item for item in memory.loops if item["loop"] not in known_loops)
    tasks.extend(
        item for item in memory.tasks if (item["loop"], item["task"]) not in known_tasks
    )
    return HeartbeatSnapshot(loops=loops, tasks=tasks, source="redis")


__all__ = [
    "HeartbeatObserver",
    "HeartbeatSnapshot",
    "reset_memory",
    "snapshot",
]
