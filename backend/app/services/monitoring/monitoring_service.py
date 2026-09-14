"""資源監控 service：全域 overview 與節點/VM RRD 趨勢。

即時數據來自每個 PVE connection 的同輪 ``cluster/resources`` 與 ``/nodes``
取樣；monitoring overview 另有短 TTL read-model cache。歷史趨勢直接代理
PVE 內建 RRD，後端不自存時序資料。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Literal

from sqlmodel import Session

from app.core.authorizers import require_resource_access
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError
from app.models import User
from app.repositories import governance as governance_repo
from app.repositories import proxmox_node as proxmox_node_repo
from app.repositories import resource as resource_repo
from app.schemas.monitoring import (
    MonitoringIssue,
    MonitoringOverview,
    MonitoringSignal,
    MonitoringThresholds,
    NodeMetrics,
    VMTopEntry,
)
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

TOP_N = 5

RRD_TIMEFRAMES = {"hour", "day", "week"}

# 首頁與完整監控頁共用此 read-model cache；不快取 proxmox_service 的一般操作，
# 避免把舊資料帶進 provisioning / placement 等需要即時驗證的流程。
OVERVIEW_CACHE_KEY = "monitoring:pve-overview:v1"
OVERVIEW_FRESH_TTL_SECONDS = 10
OVERVIEW_STALE_TTL_SECONDS = 60
OVERVIEW_LOCK_TTL_SECONDS = 20
OVERVIEW_REFRESH_WAIT_SECONDS = 2

_OVERVIEW_FRESH_KEY = f"{OVERVIEW_CACHE_KEY}:fresh"
_OVERVIEW_STALE_KEY = f"{OVERVIEW_CACHE_KEY}:stale"
_OVERVIEW_LOCK_KEY = f"{OVERVIEW_CACHE_KEY}:lock"
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class _LocalOverviewEntry:
    def __init__(self, stored_at: float, overview: MonitoringOverview) -> None:
        self.stored_at = stored_at
        self.overview = overview


_local_overview_entry: _LocalOverviewEntry | None = None
_local_overview_condition = threading.Condition()
_local_overview_refreshing = False


def _is_template(raw: dict[str, Any]) -> bool:
    value = raw.get("template")
    return value in (1, True, "1", "true", "True")


def _guest_resources(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留 resource-mgmt 使用的非範本 VM/LXC。"""
    return [
        resource
        for resource in resources
        if not _is_template(resource)
        and str(resource.get("type") or "").lower() in {"qemu", "lxc"}
    ]


def _percent(value: Any, total: Any) -> float | None:
    try:
        numerator = float(value or 0)
        denominator = float(total or 0)
    except (TypeError, ValueError):
        return None
    if denominator <= 0:
        return None
    return numerator / denominator * 100.0


def _signal(
    metric: Literal["cpu", "memory"],
    value: float | None,
    threshold: float,
) -> MonitoringSignal | None:
    if value is None or value < threshold:
        return None
    return MonitoringSignal(
        metric=metric,
        value=round(value, 1),
        threshold=round(threshold, 1),
    )


def _node_signals(
    raw: dict[str, Any], thresholds: MonitoringThresholds
) -> list[MonitoringSignal]:
    if str(raw.get("status") or "unknown") != "online":
        return []
    signals: list[MonitoringSignal] = []
    cpu = None
    if raw.get("cpu") is not None and (raw.get("maxcpu") or 0):
        try:
            cpu = float(raw.get("cpu") or 0) * 100.0
        except (TypeError, ValueError):
            cpu = None
    for candidate in (
        _signal("cpu", cpu, thresholds.cpu),
        _signal("memory", _percent(raw.get("mem"), raw.get("maxmem")), thresholds.memory),
    ):
        if candidate is not None:
            signals.append(candidate)
    return signals


def _guest_signals(
    raw: dict[str, Any], thresholds: MonitoringThresholds
) -> list[MonitoringSignal]:
    if str(raw.get("status") or "") != "running":
        return []
    signals: list[MonitoringSignal] = []
    cpu = None
    if raw.get("cpu") is not None:
        try:
            cpu = float(raw.get("cpu") or 0) * 100.0
        except (TypeError, ValueError):
            cpu = None
    for candidate in (
        _signal("cpu", cpu, thresholds.cpu),
        _signal("memory", _percent(raw.get("mem"), raw.get("maxmem")), thresholds.memory),
    ):
        if candidate is not None:
            signals.append(candidate)
    return signals


def _issue_target(raw: dict[str, Any]) -> tuple[str, int | None]:
    vmid_raw = raw.get("vmid")
    try:
        vmid = int(vmid_raw) if vmid_raw is not None else None
    except (TypeError, ValueError):
        vmid = None
    target = str(raw.get("name") or "").strip()
    if not target:
        target = f"VMID {vmid}" if vmid is not None else "未知資源"
    return target, vmid


def _build_issues(
    nodes: list[dict[str, Any]],
    node_metrics: list[NodeMetrics],
    resources: list[dict[str, Any]],
    thresholds: MonitoringThresholds,
) -> tuple[list[MonitoringIssue], int, int, int]:
    issues: list[MonitoringIssue] = []
    nodes_saturated = 0
    vms_saturated = 0
    lxc_saturated = 0

    for raw, node in zip(nodes, node_metrics, strict=False):
        if node.status != "online":
            issues.append(
                MonitoringIssue(
                    kind="node_offline",
                    severity="critical",
                    scope="node",
                    target=node.node or "未知節點",
                    node=node.node or None,
                )
            )
            continue
        signals = _node_signals(raw, thresholds)
        if signals:
            nodes_saturated += 1
            issues.append(
                MonitoringIssue(
                    kind="node_overloaded",
                    severity="warning",
                    scope="node",
                    target=node.node or "未知節點",
                    node=node.node or None,
                    signals=signals,
                )
            )

    for raw in resources:
        resource_type = str(raw.get("type") or "").lower()
        signals = _guest_signals(raw, thresholds)
        if not signals or resource_type not in {"qemu", "lxc"}:
            continue
        scope: Literal["qemu", "lxc"] = "qemu" if resource_type == "qemu" else "lxc"
        target, vmid = _issue_target(raw)
        if scope == "qemu":
            vms_saturated += 1
        else:
            lxc_saturated += 1
        issues.append(
            MonitoringIssue(
                kind="guest_overloaded",
                severity="warning",
                scope=scope,
                target=target,
                node=str(raw.get("node") or "") or None,
                vmid=vmid,
                signals=signals,
            )
        )

    severity_order = {"critical": 0, "warning": 1}
    issues.sort(key=lambda issue: (severity_order[issue.severity], issue.target.lower()))
    return issues, nodes_saturated, vms_saturated, lxc_saturated


def _validate_timeframe(timeframe: str) -> str:
    if timeframe not in RRD_TIMEFRAMES:
        raise BadRequestError(
            t("monitoring.invalid_timeframe", allowed=sorted(RRD_TIMEFRAMES))
        )
    return timeframe


def _node_metrics(
    raw: dict[str, Any],
    vm_counts: dict[str, int],
    connection_names: dict[str, str],
) -> NodeMetrics:
    name = str(raw.get("node") or "")
    return NodeMetrics(
        node=name,
        status=str(raw.get("status") or "unknown"),
        cpu=float(raw.get("cpu") or 0.0),
        maxcpu=int(raw.get("maxcpu") or 0),
        mem=int(raw.get("mem") or 0),
        maxmem=int(raw.get("maxmem") or 0),
        disk=int(raw.get("disk") or 0),
        maxdisk=int(raw.get("maxdisk") or 0),
        uptime=int(raw.get("uptime") or 0),
        vm_count=vm_counts.get(name, 0),
        connection_name=connection_names.get(name),
    )


def _vm_counts_by_node(resources: list[dict[str, Any]]) -> dict[str, int]:
    """每個節點上的 VM/LXC 台數（含已停止，排除範本）。"""
    counts: dict[str, int] = {}
    for r in resources:
        if _is_template(r):
            continue
        if str(r.get("type") or "").lower() not in {"lxc", "qemu"}:
            continue
        node = str(r.get("node") or "")
        counts[node] = counts.get(node, 0) + 1
    return counts


def _vm_entry(raw: dict[str, Any]) -> VMTopEntry:
    return VMTopEntry(
        vmid=int(raw.get("vmid") or 0),
        name=str(raw.get("name") or ""),
        node=str(raw.get("node") or ""),
        type=str(raw.get("type") or ""),
        cpu=float(raw.get("cpu") or 0.0),
        mem=int(raw.get("mem") or 0),
        maxmem=int(raw.get("maxmem") or 0),
        status=str(raw.get("status") or ""),
    )


def build_overview(
    nodes: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    connection_names: dict[str, str] | None = None,
    thresholds: MonitoringThresholds | None = None,
    data_status: Literal["fresh", "stale", "partial"] = "fresh",
) -> MonitoringOverview:
    """純函式：由 PVE 原始回應聚合全域監控視圖。

    ``connection_names`` 為「節點名稱 → 所屬 PVE 連線名稱」的對應（多連線架構）；
    省略時節點不標示連線來源。
    """
    effective_thresholds = thresholds or MonitoringThresholds()
    guests = _guest_resources(resources)
    vm_counts = _vm_counts_by_node(guests)
    node_metrics = [
        _node_metrics(n, vm_counts, connection_names or {}) for n in nodes
    ]

    running = [r for r in guests if str(r.get("status") or "") == "running"]
    stopped = [r for r in guests if str(r.get("status") or "") != "running"]
    running_entries = [_vm_entry(r) for r in running]

    def _count(items: list[dict[str, Any]], rtype: str) -> int:
        return sum(
            1 for r in items if str(r.get("type") or "").lower() == rtype
        )

    issues, nodes_saturated, vms_saturated, lxc_saturated = _build_issues(
        nodes, node_metrics, guests, effective_thresholds
    )
    overall_status: Literal["healthy", "warning", "critical", "unknown"]
    if not node_metrics:
        overall_status = "unknown"
    elif any(issue.severity == "critical" for issue in issues):
        overall_status = "critical"
    elif issues or data_status == "partial":
        overall_status = "warning"
    else:
        overall_status = "healthy"

    return MonitoringOverview(
        collected_at=datetime.now(timezone.utc),
        data_status=data_status,
        overall_status=overall_status,
        thresholds=effective_thresholds,
        nodes_online=sum(1 for n in node_metrics if n.status == "online"),
        nodes_total=len(node_metrics),
        nodes_saturated=nodes_saturated,
        cpu_used=sum(n.cpu * n.maxcpu for n in node_metrics),
        cpu_total=sum(n.maxcpu for n in node_metrics),
        mem_used=sum(n.mem for n in node_metrics),
        mem_total=sum(n.maxmem for n in node_metrics),
        disk_used=sum(n.disk for n in node_metrics),
        disk_total=sum(n.maxdisk for n in node_metrics),
        vms_running=_count(running, "qemu"),
        vms_stopped=_count(stopped, "qemu"),
        vms_saturated=vms_saturated,
        lxc_running=_count(running, "lxc"),
        lxc_stopped=_count(stopped, "lxc"),
        lxc_saturated=lxc_saturated,
        nodes=node_metrics,
        top_cpu=sorted(running_entries, key=lambda e: e.cpu, reverse=True)[:TOP_N],
        top_mem=sorted(running_entries, key=lambda e: e.mem, reverse=True)[:TOP_N],
        issues=issues,
    )


def _connection_names(session: Session) -> dict[str, str]:
    """節點所屬連線名稱；查詢失敗時退回空對應（不影響監控主體）。"""
    try:
        return proxmox_node_repo.get_node_connection_names(session)
    except Exception:
        return {}


def _thresholds(session: Session) -> MonitoringThresholds:
    """讀取治理門檻；設定資料不可用時維持 90% 的安全預設。"""
    try:
        config = governance_repo.get_governance_config(session=session)
        return MonitoringThresholds(
            cpu=float(config.alert_cpu_threshold),
            memory=float(config.alert_memory_threshold),
        )
    except Exception:
        return MonitoringThresholds()


def get_overview(*, session: Session) -> MonitoringOverview:
    snapshot = proxmox_service.collect_monitoring_snapshot()
    data_status: Literal["fresh", "stale", "partial"] = (
        "partial" if snapshot.failed_connections else "fresh"
    )
    return build_overview(
        snapshot.nodes,
        snapshot.resources,
        _connection_names(session),
        _thresholds(session),
        data_status,
    )


def _collect_overview_with_worker_session() -> MonitoringOverview:
    """在執行緒內建立 DB session，避免把 request session 跨 event loop 使用。"""
    from app.core.db import engine

    with Session(engine) as session:
        return get_overview(session=session)


def _cache_age_seconds(overview: MonitoringOverview) -> int:
    age = (datetime.now(timezone.utc) - overview.collected_at).total_seconds()
    return max(0, int(age))


def _with_cache_status(
    overview: MonitoringOverview,
    *,
    stale: bool,
) -> MonitoringOverview:
    status: Literal["fresh", "stale", "partial"] = (
        "stale" if stale else overview.data_status
    )
    overall_status = overview.overall_status
    if stale and overall_status == "healthy":
        overall_status = "warning"
    return overview.model_copy(
        update={
            "data_status": status,
            "cache_age_seconds": _cache_age_seconds(overview),
            "overall_status": overall_status,
        }
    )


def _read_local_overview(*, fresh_only: bool) -> MonitoringOverview | None:
    now = time.monotonic()
    with _local_overview_condition:
        entry = _local_overview_entry
        if entry is None:
            return None
        age = now - entry.stored_at
        if age <= OVERVIEW_FRESH_TTL_SECONDS:
            return _with_cache_status(entry.overview, stale=False)
        if not fresh_only and age <= OVERVIEW_STALE_TTL_SECONDS:
            return _with_cache_status(entry.overview, stale=True)
    return None


def _store_local_overview(overview: MonitoringOverview) -> None:
    global _local_overview_entry
    with _local_overview_condition:
        _local_overview_entry = _LocalOverviewEntry(time.monotonic(), overview)
        _local_overview_condition.notify_all()


def _set_local_refreshing(value: bool) -> None:
    global _local_overview_refreshing
    with _local_overview_condition:
        _local_overview_refreshing = value
        _local_overview_condition.notify_all()


def _get_or_collect_local_overview() -> MonitoringOverview:
    """Redis 不可用時的每 worker fallback，並在同一 worker 內合併併發 miss。"""
    global _local_overview_refreshing

    cached = _read_local_overview(fresh_only=True)
    if cached is not None:
        return cached

    wait_deadline = time.monotonic() + OVERVIEW_LOCK_TTL_SECONDS
    with _local_overview_condition:
        while _local_overview_refreshing:
            cached = _read_local_overview(fresh_only=False)
            if cached is not None:
                return cached
            remaining = wait_deadline - time.monotonic()
            if remaining <= 0:
                break
            _local_overview_condition.wait(min(0.1, remaining))
        cached = _read_local_overview(fresh_only=True)
        if cached is not None:
            return cached
        _local_overview_refreshing = True

    try:
        overview = _collect_overview_with_worker_session()
        _store_local_overview(overview)
        return _with_cache_status(overview, stale=False)
    except Exception:
        cached = _read_local_overview(fresh_only=False)
        if cached is not None:
            return cached
        raise
    finally:
        _set_local_refreshing(False)


async def _read_redis_overview(redis: Any, key: str, *, stale: bool) -> MonitoringOverview | None:
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        overview = MonitoringOverview.model_validate_json(raw)
    except Exception:
        logger.warning("Ignoring malformed monitoring overview cache entry key=%s", key)
        return None
    return _with_cache_status(overview, stale=stale)


async def _write_redis_overview(redis: Any, overview: MonitoringOverview) -> None:
    payload = overview.model_dump_json()
    await redis.set(_OVERVIEW_FRESH_KEY, payload, ex=OVERVIEW_FRESH_TTL_SECONDS)
    await redis.set(_OVERVIEW_STALE_KEY, payload, ex=OVERVIEW_STALE_TTL_SECONDS)


async def _acquire_redis_lock(redis: Any) -> str | None:
    token = secrets.token_urlsafe(16)
    acquired = await redis.set(
        _OVERVIEW_LOCK_KEY,
        token,
        nx=True,
        ex=OVERVIEW_LOCK_TTL_SECONDS,
    )
    return token if acquired else None


async def _release_redis_lock(redis: Any, token: str) -> None:
    try:
        await redis.eval(_RELEASE_LOCK_SCRIPT, 1, _OVERVIEW_LOCK_KEY, token)
    except Exception:
        logger.warning("Unable to release monitoring overview cache lock", exc_info=True)


async def _wait_for_redis_overview(redis: Any) -> MonitoringOverview | None:
    deadline = time.monotonic() + OVERVIEW_REFRESH_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        fresh = await _read_redis_overview(redis, _OVERVIEW_FRESH_KEY, stale=False)
        if fresh is not None:
            return fresh
    return await _read_redis_overview(redis, _OVERVIEW_STALE_KEY, stale=True)


async def get_overview_cached(
    *, redis: Any | None
) -> MonitoringOverview:
    """回傳 monitoring read model；Redis 可用時跨 worker 去重，失敗時安全退化。"""
    local = _read_local_overview(fresh_only=True)
    if local is not None:
        return local

    collection_failed_without_stale = False
    if redis is not None:
        try:
            fresh = await _read_redis_overview(
                redis, _OVERVIEW_FRESH_KEY, stale=False
            )
            if fresh is not None:
                _store_local_overview(fresh)
                return fresh

            token = await _acquire_redis_lock(redis)
            if token is not None:
                _set_local_refreshing(True)
                try:
                    try:
                        overview = await asyncio.to_thread(
                            _collect_overview_with_worker_session
                        )
                    except Exception:
                        stale = await _read_redis_overview(
                            redis, _OVERVIEW_STALE_KEY, stale=True
                        )
                        if stale is not None:
                            return stale
                        collection_failed_without_stale = True
                        raise
                    try:
                        await _write_redis_overview(redis, overview)
                    except Exception:
                        logger.warning(
                            "Unable to write monitoring overview cache", exc_info=True
                        )
                    _store_local_overview(overview)
                    return _with_cache_status(overview, stale=False)
                finally:
                    try:
                        await _release_redis_lock(redis, token)
                    finally:
                        _set_local_refreshing(False)

            waited = await _wait_for_redis_overview(redis)
            if waited is not None:
                if waited.data_status != "stale":
                    _store_local_overview(waited)
                return waited
        except Exception:
            if collection_failed_without_stale:
                # PVE collection errors are real upstream failures, not Redis
                # availability errors. Do not immediately collect a second
                # time through the local fallback in the same request.
                raise
            logger.warning(
                "Monitoring overview Redis cache unavailable; using local fallback",
                exc_info=True,
            )

    return await asyncio.to_thread(_get_or_collect_local_overview)


def get_node_rrd(node: str, timeframe: str) -> list[dict[str, Any]]:
    _validate_timeframe(timeframe)
    return proxmox_service.get_node_rrd_data(node, timeframe)


def get_vm_rrd(
    *, session: Session, vmid: int, timeframe: str, user: User
) -> list[dict[str, Any]]:
    _validate_timeframe(timeframe)
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if resource is None:
        raise NotFoundError(t("monitoring.resource_not_found", vmid=vmid))
    require_resource_access(user, resource.user_id)
    info = proxmox_service.find_resource(vmid)
    resource_type: Literal["qemu", "lxc"] = (
        "lxc" if str(info.get("type") or "") == "lxc" else "qemu"
    )
    return proxmox_service.get_rrd_data(
        str(info["node"]), vmid, resource_type, timeframe
    )
