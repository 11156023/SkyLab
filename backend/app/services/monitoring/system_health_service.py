"""平台本身的健康狀態：DB、Redis、arq worker、PVE API 連線、Gateway、排程心跳。

- ``readiness()``：給 ``/utils/health-check/ready``（免登入、只回布林）
- ``collect_system_health()``：給管理員的 ``/monitoring/system-health``
- ``process_system_health_alerts()``：排程 tick，把問題寫成 scope=system 的
  AlertEvent 並寄信給管理員（沿用資源告警的開關、冷卻時間與 Email 設定）
- ``collect_metrics()``：``/metrics`` 抓取前更新依賴元件與佇列 gauge

全部是同步函式（DB／同步 Redis／proxmoxer 都是阻塞 I/O）；async 呼叫端用
``asyncio.to_thread`` 包。每項探測都有逾時，一個元件卡住不會拖垮整份報告。

PVE 這裡只檢查「後端連不連得到 PVE API」——這是 SkyLab 自己的依賴。
節點／VM 的資源用量由 PVE 內建 Metric Server 直接推到 InfluxDB，不經後端。

Gateway 用一條 SSH 指令看 nginx／WireGuard 是否在跑、nginx -t 與憑證效期；
主機資源與 nginx 流量由 Gateway 上的 exporter 交給 Prometheus（見
prometheus_sd_service），這裡只管「服務還在不在、要不要人處理」。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, ClassVar, TypeVar

from sqlalchemy import func, text
from sqlmodel import Session, select

from app.core import metrics
from app.core.db import engine
from app.infrastructure.redis.sync_client import get_sync_redis
from app.models import AlertEvent, AlertMetric, AlertScope, TaskRecord, TaskRecordStatus
from app.repositories import governance as governance_repo
from app.services.monitoring import health_policy, heartbeat_service

logger = logging.getLogger(__name__)

T = TypeVar("T")

PROBE_TIMEOUT_SECONDS = 3.0
PVE_PROBE_TIMEOUT_SECONDS = 5.0
# PVE 探測結果快取：管理頁每 30 秒輪詢、告警每分鐘評估，不必每次都打 PVE
PVE_CACHE_SECONDS = 20.0
# Gateway 探測要開 SSH 連線（含金鑰交換），比 PVE API 貴，快取久一點
GATEWAY_PROBE_TIMEOUT_SECONDS = 15.0
GATEWAY_CACHE_SECONDS = 60.0
# 同一個問題要連續出現幾輪評估才開告警：吸收部署時 worker 晚幾秒起來、
# PVE 瞬斷這類抖動
FINDING_CONFIRMATIONS = 2
MIN_ALERT_INTERVAL_SECONDS = 60.0

# arq 的 health check key 與佇列 key（QUEUE_NAME = "skylab:tasks"）
_ARQ_QUEUE = "skylab:tasks"
_ARQ_HEALTH_KEY = f"{_ARQ_QUEUE}:health-check"

# 探測專用的執行緒池：DB／Redis 卡住時 future 逾時就放棄等待
_probe_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health-probe")


def _run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    return _probe_pool.submit(fn).result(timeout=timeout)


def _component(
    name: str,
    label: str,
    status: str,
    *,
    latency_ms: float | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "status": status,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "detail": detail,
    }


def _short_error(exc: BaseException) -> str:
    if isinstance(exc, FutureTimeout | TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}: {exc}"[:200]


# ─── 個別探測 ─────────────────────────────────────────────────────────────


def check_database() -> dict[str, Any]:
    def probe() -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    started = time.perf_counter()
    try:
        _run_with_timeout(probe, PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        return _component("database", "PostgreSQL", "down", detail=_short_error(exc))
    return _component(
        "database", "PostgreSQL", "ok", latency_ms=(time.perf_counter() - started) * 1000
    )


def check_redis() -> dict[str, Any]:
    client = get_sync_redis()
    if client is None:
        return _component("redis", "Redis", "disabled")
    started = time.perf_counter()
    try:
        _run_with_timeout(client.ping, PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        return _component("redis", "Redis", "down", detail=_short_error(exc))
    return _component(
        "redis", "Redis", "ok", latency_ms=(time.perf_counter() - started) * 1000
    )


def check_worker(*, redis_ok: bool) -> dict[str, Any]:
    """arq worker 每 60 秒把 health check key 寫進 Redis（TTL 61 秒）。

    key 不在＝worker 沒在跑；Redis 本身掛了就無從判斷（unknown）。
    """
    client = get_sync_redis()
    if client is None:
        return _component("worker", "Worker", "disabled")
    if not redis_ok:
        return _component("worker", "Worker", "unknown", detail="redis unavailable")

    def probe() -> tuple[str | None, int]:
        pipe = client.pipeline(transaction=False)
        pipe.get(_ARQ_HEALTH_KEY)
        pipe.zcard(_ARQ_QUEUE)
        health, queued = pipe.execute()
        return health, int(queued or 0)

    try:
        health, queued = _run_with_timeout(probe, PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        return _component("worker", "Worker", "unknown", detail=_short_error(exc))
    if not health:
        return _component(
            "worker", "Worker", "down", detail=f"no heartbeat; queued={queued}"
        )
    return _component("worker", "Worker", "ok", detail=str(health)[:200])


class _PveCache:
    lock: ClassVar[threading.Lock] = threading.Lock()
    expires_at: ClassVar[float] = 0.0
    components: ClassVar[list[dict[str, Any]]] = []


def _list_pve_connections() -> list[tuple[int, str]]:
    from app.repositories import proxmox_connection as connection_repo

    with Session(engine) as session:
        return [
            (conn.id, conn.name)
            for conn in connection_repo.get_all_connections(session, enabled_only=True)
            if conn.id is not None
        ]


def _probe_pve_connection(connection_id: int) -> None:
    from app.infrastructure.proxmox.client import get_proxmox_api

    get_proxmox_api(connection_id).version.get()


def check_pve(*, use_cache: bool = True) -> list[dict[str, Any]]:
    """每個啟用中的 PVE 連線各回一個元件（name = ``pve:<id>``）。"""
    now = time.monotonic()
    with _PveCache.lock:
        if use_cache and now < _PveCache.expires_at:
            return [dict(c) for c in _PveCache.components]

    try:
        connections = _run_with_timeout(_list_pve_connections, PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.debug("Listing PVE connections failed", exc_info=True)
        return [_component("pve", "Proxmox VE", "unknown", detail=_short_error(exc))]

    components: list[dict[str, Any]] = []
    if not connections:
        components.append(_component("pve", "Proxmox VE", "disabled", detail="not configured"))
    for connection_id, name in connections:
        started = time.perf_counter()
        label = f"Proxmox VE · {name}"
        try:
            _run_with_timeout(
                partial(_probe_pve_connection, connection_id),
                PVE_PROBE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            components.append(
                _component(f"pve:{connection_id}", label, "down", detail=_short_error(exc))
            )
            continue
        components.append(
            _component(
                f"pve:{connection_id}",
                label,
                "ok",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        )

    with _PveCache.lock:
        _PveCache.components = [dict(c) for c in components]
        _PveCache.expires_at = time.monotonic() + PVE_CACHE_SECONDS
    return components


def cached_pve_components() -> list[dict[str, Any]]:
    """不觸發探測，只回最近一次結果（/metrics 用，避免 Prometheus 每 15 秒打 PVE）。"""
    with _PveCache.lock:
        return [dict(c) for c in _PveCache.components]


class _GatewayCache:
    lock: ClassVar[threading.Lock] = threading.Lock()
    expires_at: ClassVar[float] = 0.0
    components: ClassVar[list[dict[str, Any]]] = []


def _load_gateway_config() -> Any:
    from app.repositories import gateway_config as gw_repo

    with Session(engine) as session:
        config = gw_repo.get_gateway_config(session)
        if config is None or not config.host or not config.encrypted_private_key:
            return None
        session.expunge(config)
        return config


def _probe_gateway(config: Any) -> dict[str, Any]:
    from app.core.config import settings
    from app.infrastructure.ssh import create_key_client
    from app.repositories.gateway_config import get_decrypted_private_key
    from app.services.network import nginx_gateway_service

    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        get_decrypted_private_key(config),
        timeout=10,
    )
    try:
        return nginx_gateway_service.probe_health(
            client, wireguard_unit=f"wg-quick@{settings.WIREGUARD_INTERFACE}"
        )
    finally:
        client.close()


def check_gateway(*, use_cache: bool = True) -> list[dict[str, Any]]:
    """Gateway 主機：nginx／WireGuard 服務、nginx -t、憑證效期（name = ``gateway``）。"""
    now = time.monotonic()
    with _GatewayCache.lock:
        if use_cache and now < _GatewayCache.expires_at:
            return [dict(c) for c in _GatewayCache.components]

    label = "Gateway"
    try:
        config = _run_with_timeout(_load_gateway_config, PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.debug("Loading gateway config failed", exc_info=True)
        return [_component("gateway", label, "unknown", detail=_short_error(exc))]

    if config is None:
        component = _component("gateway", label, "disabled", detail="not configured")
    else:
        label = f"Gateway · {config.host}"
        started = time.perf_counter()
        try:
            probe = _run_with_timeout(
                partial(_probe_gateway, config), GATEWAY_PROBE_TIMEOUT_SECONDS
            )
        except Exception as exc:
            component = _component(
                "gateway", label, "down", detail=f"SSH 失敗：{_short_error(exc)}"
            )
            component["alert_message"] = f"{label} 無法以 SSH 連線：{_short_error(exc)}"
        else:
            status, detail, alert_message = health_policy.gateway_status(
                probe, now=datetime.now(timezone.utc)
            )
            component = _component(
                "gateway",
                label,
                status,
                latency_ms=(time.perf_counter() - started) * 1000,
                detail=detail,
            )
            if alert_message:
                component["alert_message"] = alert_message

    with _GatewayCache.lock:
        _GatewayCache.components = [dict(component)]
        _GatewayCache.expires_at = time.monotonic() + GATEWAY_CACHE_SECONDS
    return [component]


def cached_gateway_components() -> list[dict[str, Any]]:
    with _GatewayCache.lock:
        return [dict(c) for c in _GatewayCache.components]


def reset_gateway_cache() -> None:
    """測試用，也給 Gateway 設定變更後立刻重新探測。"""
    with _GatewayCache.lock:
        _GatewayCache.components = []
        _GatewayCache.expires_at = 0.0


# ─── 彙總 ─────────────────────────────────────────────────────────────────


def _heartbeat_view(now: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    snap = heartbeat_service.snapshot()
    intervals = {
        loop["loop"]: loop.get("interval_seconds") for loop in snap.loops
    }
    loops = []
    for loop in snap.loops:
        loops.append({**loop, "status": health_policy.loop_status(loop, now=now)})
    tasks = []
    for task in snap.tasks:
        interval = intervals.get(task.get("loop"))
        tasks.append(
            {
                **task,
                "interval_seconds": interval,
                "status": health_policy.task_status(task, interval_seconds=interval, now=now),
            }
        )
    loops.sort(key=lambda item: str(item.get("loop")))
    tasks.sort(key=lambda item: (str(item.get("loop")), str(item.get("task"))))
    return loops, tasks, snap.source


def readiness() -> dict[str, Any]:
    """Readiness：只看核心依賴（DB、Redis），任一掛掉就是 not ready。"""
    database = check_database()
    redis = check_redis()
    checks = {
        "database": database["status"] == "ok",
        "redis": None if redis["status"] == "disabled" else redis["status"] == "ok",
    }
    ready = checks["database"] and checks["redis"] is not False
    return {"status": "ok" if ready else "fail", "checks": checks}


def collect_components(*, use_pve_cache: bool = True) -> list[dict[str, Any]]:
    database = check_database()
    redis = check_redis()
    worker = check_worker(redis_ok=redis["status"] == "ok")
    return [
        database,
        redis,
        worker,
        *check_pve(use_cache=use_pve_cache),
        *check_gateway(use_cache=use_pve_cache),
    ]


def collect_system_health() -> dict[str, Any]:
    now = time.time()
    components = collect_components()
    loops, tasks, source = _heartbeat_view(now)
    return {
        "status": health_policy.overall_status(components, loops, tasks),
        "generated_at": datetime.now(timezone.utc),
        "components": components,
        "loops": loops,
        "tasks": tasks,
        "heartbeat_source": source,
    }


# ─── 系統告警（排程 tick） ────────────────────────────────────────────────


@dataclass
class _AlertTickState:
    last_run_monotonic: float | None = None
    # target → 連續幾輪評估都看到這個問題
    streaks: dict[str, int] = field(default_factory=dict)


_alert_state = _AlertTickState()


def reset_alert_state() -> None:
    """測試用：清掉節流時間與連續出現計數。"""
    _alert_state.last_run_monotonic = None
    _alert_state.streaks = {}


def _confirmed(findings: list[health_policy.SystemFinding]) -> list[health_policy.SystemFinding]:
    seen = {finding.target for finding in findings}
    streaks = {
        target: _alert_state.streaks.get(target, 0) + 1 for target in seen
    }
    _alert_state.streaks = streaks
    return [f for f in findings if streaks[f.target] >= FINDING_CONFIRMATIONS]


def _notify_admins(session: Session, created: list[AlertEvent]) -> None:
    from app.services.monitoring.alert_service import _list_admin_emails
    from app.utils import send_email

    emails = _list_admin_emails(session)
    for alert in created:
        subject = f"[SkyLab 系統告警] {alert.target}"
        html = (
            f"<p>{alert.message}</p>"
            f"<p>目標：{alert.target}<br/>"
            f"時間：{alert.created_at:%Y-%m-%d %H:%M:%S %Z}</p>"
            "<p>請到「系統監控 → 系統健康」查看目前狀態。</p>"
        )
        for email in emails:
            try:
                send_email(email_to=email, subject=subject, html_content=html)
            except Exception:
                logger.warning("Failed to send system alert email to %s for %s", email, alert.target)


def process_system_health_alerts() -> int:
    """Scheduler tick：平台健康問題 → AlertEvent（scope=system）。回傳新建事件數。"""
    # 例外不在這裡吞：交給 runner 記錄，心跳才會把這個任務標成失敗
    with Session(engine) as session:
        config = governance_repo.get_governance_config(session=session)
        if not config.alerts_enabled:
            return 0
        now_mono = time.monotonic()
        interval = max(MIN_ALERT_INTERVAL_SECONDS, float(config.alert_check_interval_seconds))
        last_run = _alert_state.last_run_monotonic
        if last_run is not None and now_mono - last_run < interval:
            return 0
        _alert_state.last_run_monotonic = now_mono

        now_ts = time.time()
        components = collect_components()
        loops, tasks, _source = _heartbeat_view(now_ts)
        findings = _confirmed(health_policy.build_findings(components, loops, tasks))

        now = datetime.now(timezone.utc)
        cooldown = timedelta(minutes=int(config.alert_cooldown_minutes))
        open_alerts = governance_repo.get_open_system_alerts(session=session)
        recent = governance_repo.get_system_alerts_since(session=session, since=now - cooldown)
        last_created: dict[str, float] = {}
        for alert in recent:
            ts = alert.created_at.timestamp()
            if ts > last_created.get(alert.target, 0.0):
                last_created[alert.target] = ts

        decision = health_policy.evaluate_system_alerts(
            findings,
            open_targets=[a.target for a in open_alerts],
            last_created=last_created,
            cooldown_seconds=cooldown.total_seconds(),
            now=now.timestamp(),
        )

        created: list[AlertEvent] = []
        for finding in decision.new_findings:
            event = AlertEvent(
                scope=AlertScope.system,
                target=finding.target[:255],
                metric=AlertMetric.health,
                value=finding.value,
                threshold=finding.threshold,
                message=finding.message,
                created_at=now,
            )
            session.add(event)
            created.append(event)

        resolved = set(decision.resolved_targets)
        for alert in open_alerts:
            if alert.target in resolved:
                alert.resolved_at = now
                session.add(alert)

        session.commit()
        for event in created:
            session.refresh(event)

        if created:
            logger.warning("System health alerts created: %s", [e.target for e in created])
            if config.alert_email_enabled:
                _notify_admins(session, created)
        if resolved:
            logger.info("System health alerts resolved: %s", sorted(resolved))
        return len(created)


# ─── /metrics collect hook ────────────────────────────────────────────────


def _task_record_counts() -> dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with Session(engine) as session:
        active = session.exec(
            select(TaskRecord.status, func.count())
            .where(TaskRecord.status.in_([TaskRecordStatus.queued, TaskRecordStatus.running]))  # type: ignore[attr-defined]
            .group_by(TaskRecord.status)
        ).all()
        finished = session.exec(
            select(TaskRecord.status, func.count())
            .where(
                TaskRecord.status.in_([TaskRecordStatus.failed, TaskRecordStatus.succeeded]),  # type: ignore[attr-defined]
                TaskRecord.finished_at >= since,  # type: ignore[operator]
            )
            .group_by(TaskRecord.status)
        ).all()
    counts = {"queued": 0, "running": 0, "failed_24h": 0, "succeeded_24h": 0}
    for status, count in active:
        counts[str(getattr(status, "value", status))] = int(count)
    for status, count in finished:
        counts[f"{getattr(status, 'value', status)}_24h"] = int(count)
    return counts


def collect_metrics() -> None:
    """更新依賴元件、佇列與任務紀錄 gauge（在執行緒裡跑）。"""
    database = check_database()
    redis = check_redis()
    worker = check_worker(redis_ok=redis["status"] == "ok")
    for component in (
        database,
        redis,
        worker,
        *cached_pve_components(),
        *cached_gateway_components(),
    ):
        if component["status"] in ("disabled", "unknown"):
            continue
        # attention（例如憑證快到期）服務仍然可用，不算 down
        metrics.DEPENDENCY_UP.labels(component=component["name"]).set(
            1 if component["status"] in ("ok", "attention") else 0
        )
        if component.get("latency_ms") is not None:
            metrics.DEPENDENCY_LATENCY.labels(component=component["name"]).set(
                component["latency_ms"] / 1000
            )

    client = get_sync_redis()
    if redis["status"] == "ok" and client is not None:
        try:
            queued = _run_with_timeout(partial(client.zcard, _ARQ_QUEUE), PROBE_TIMEOUT_SECONDS)
            metrics.QUEUE_JOBS.labels(queue=_ARQ_QUEUE).set(int(queued or 0))
        except Exception:
            logger.debug("Queue length probe failed", exc_info=True)

    if database["status"] == "ok":
        try:
            counts = _run_with_timeout(_task_record_counts, PROBE_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("Task record count failed", exc_info=True)
        else:
            for status, count in counts.items():
                metrics.TASK_RECORDS.labels(status=status).set(count)


async def collect_metrics_hook() -> None:
    import asyncio

    await asyncio.to_thread(collect_metrics)


__all__ = [
    "cached_gateway_components",
    "check_database",
    "check_gateway",
    "check_pve",
    "check_redis",
    "check_worker",
    "collect_components",
    "collect_metrics",
    "collect_metrics_hook",
    "collect_system_health",
    "process_system_health_alerts",
    "readiness",
    "reset_alert_state",
    "reset_gateway_cache",
]
