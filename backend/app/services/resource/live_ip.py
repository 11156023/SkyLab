"""開機中機器的即時 IP 查詢（資源清單、防火牆拓撲共用）。

QEMU 的即時 IP 要問 guest agent，這個查詢又慢又會卡：

- 設定了 agent 但 guest 裡沒在跑（開機中、沒裝、當掉）時，PVE 會卡滿 3 秒的
  guest-ping 逾時才回「QEMU guest agent is not running」，等到的還是 None。
- agent 有在跑的機器單獨問只要 0.1 秒上下，但查詢會佔住節點的 pvedaemon worker
  （每節點預設 3 個），平行問一批時彼此排隊，60 台開機中的機器實測要近 5 秒。

清單與拓撲每次載入都逐台問完才回應的話，開機中的機器一多就是十幾秒。所以這裡
的查詢不讓呼叫端等到底：

- 查詢丟進共用執行緒池，呼叫端最多等 ``WAIT_SECONDS``；來不及回的這次當作沒有
  即時 IP（呼叫端回退到 DB 快取／分配紀錄），查詢在背景跑完後進行程內快取。
- ``STALE_TTL`` 秒內問過的機器直接回上次的結果、不等；結果超過 ``HIT_TTL``
  （沒問到的是 ``MISS_TTL``）就順手在背景重問，下一次載入拿到新的。
- 同一台機器同時只會有一個查詢在跑。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Any

logger = logging.getLogger(__name__)

# (node, vmid, resource_type) → IP；實際上是 proxmox_service.get_ip_address
IpLookup = Callable[[str, int, str], str | None]

# 沒有可用的上次結果時，呼叫端最多等多久
WAIT_SECONDS = 1.5
# 問到 IP 後多久內不重問（吸收連續重新整理與輪詢）
HIT_TTL = 10.0
# 沒問到（agent 沒回應、還沒拿到 IPv4）後多久內不重問，免得每次載入都讓 PVE 重卡一輪
MISS_TTL = 60.0
# 上次結果多久內還能先拿來回應；更舊的就當作沒問過，等新的
STALE_TTL = 120.0
# 同時對 PVE 發出的查詢數；瓶頸在 pvedaemon，開大只會讓 PVE 的其他 API 跟著排隊
_WORKERS = 4
_CACHE_PRUNE_SIZE = 4096

_Key = tuple[str, int]

_pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="live-ip")
_lock = threading.Lock()
# (node, vmid) → (問到的時間, IP 或 None)
_cache: dict[_Key, tuple[float, str | None]] = {}
_inflight: dict[_Key, Future[str | None]] = {}
# clear_live_ip_cache() 之後才跑完的舊查詢不可以寫回快取
_generation = 0


def clear_live_ip_cache() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _inflight.clear()


def _run(
    key: _Key, resource_type: str, lookup: IpLookup, generation: int
) -> str | None:
    node, vmid = key
    ip: str | None = None
    try:
        ip = lookup(node, vmid, resource_type) or None
    except Exception as exc:
        logger.debug("VMID=%s 即時 IP 查詢失敗（改用快取）: %s", vmid, exc)
    finally:
        with _lock:
            if generation == _generation:
                _cache[key] = (time.monotonic(), ip)
                _inflight.pop(key, None)
    return ip


def _submit(key: _Key, resource_type: str, lookup: IpLookup) -> Future[str | None]:
    """送出查詢；同一台已經有查詢在跑就共用那一個。呼叫時必須持有 ``_lock``。"""
    future = _inflight.get(key)
    if future is None:
        future = _pool.submit(_run, key, resource_type, lookup, _generation)
        _inflight[key] = future
    return future


def _prune_stale(now: float) -> None:
    for key in [
        k for k, (asked_at, _ip) in _cache.items() if now - asked_at >= STALE_TTL
    ]:
        del _cache[key]


class LiveIpBatch:
    """一批已送出的即時 IP 查詢；``collect`` 只等到送出時定下的期限為止。"""

    def __init__(
        self,
        results: dict[int, str | None],
        pending: dict[int, Future[str | None]],
        deadline: float,
    ) -> None:
        self._results = results
        self._pending = pending
        self._deadline = deadline

    def collect(self) -> dict[int, str | None]:
        """vmid → 即時 IP；關機、沒問到或期限內沒回的都是 None。"""
        if self._pending:
            remaining = max(0.0, self._deadline - time.monotonic())
            done, _not_done = wait(self._pending.values(), timeout=remaining)
            for vmid, future in self._pending.items():
                if future in done:
                    self._results[vmid] = future.result()
        return dict(self._results)


def start_live_ip_probes(
    entries: Iterable[dict[str, Any]],
    lookup: IpLookup,
    *,
    wait_seconds: float | None = None,
) -> LiveIpBatch:
    """送出查詢後立刻返回，呼叫端可以先做別的事再 ``collect``。

    ``entries`` 是 /cluster/resources 的條目（node、vmid、type、status）。
    """
    results: dict[int, str | None] = {}
    pending: dict[int, Future[str | None]] = {}
    now = time.monotonic()
    with _lock:
        if len(_cache) > _CACHE_PRUNE_SIZE:
            _prune_stale(now)
        for entry in entries:
            if entry.get("vmid") is None:
                continue
            vmid = int(entry["vmid"])
            results[vmid] = None
            # 關機的機器 guest agent／interfaces 一定查不到，省一次 PVE 呼叫
            if entry.get("status") != "running":
                continue
            key = (str(entry.get("node", "")), vmid)
            resource_type = str(entry.get("type", ""))
            cached = _cache.get(key)
            if cached is not None and now - cached[0] < STALE_TTL:
                asked_at, ip = cached
                results[vmid] = ip
                if now - asked_at >= (HIT_TTL if ip else MISS_TTL):
                    _submit(key, resource_type, lookup)  # 背景重問，這次不等
                continue
            pending[vmid] = _submit(key, resource_type, lookup)
    wait_for = WAIT_SECONDS if wait_seconds is None else wait_seconds
    return LiveIpBatch(results, pending, now + wait_for)


def probe_live_ips(
    entries: Iterable[dict[str, Any]],
    lookup: IpLookup,
    *,
    wait_seconds: float | None = None,
) -> dict[int, str | None]:
    """vmid → 即時 IP；最多等 ``wait_seconds``（預設 ``WAIT_SECONDS``）。"""
    return start_live_ip_probes(entries, lookup, wait_seconds=wait_seconds).collect()
