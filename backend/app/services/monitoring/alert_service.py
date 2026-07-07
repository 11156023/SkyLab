"""資源閾值告警：抽樣 → 評估（純函式）→ 落 DB + Email 通知。

評估邏輯（collect_samples / evaluate）為純函式，不碰 DB/PVE/SMTP，
方便單元測試；I/O 由 process_resource_alerts 協調。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlmodel import Session, select

from app.core.db import engine
from app.models import AlertEvent, AlertMetric, AlertScope, User, UserRole
from app.repositories import governance as governance_repo
from app.services.proxmox import proxmox_service
from app.utils import send_email

logger = logging.getLogger(__name__)

# 遲滯：量測值須回落到「閾值 − HYSTERESIS」以下才自動 resolve
HYSTERESIS = 5.0


@dataclass(frozen=True)
class MetricSample:
    scope: str   # cluster|node|vm
    target: str  # 節點名或 vmid 字串
    metric: str  # cpu|memory|disk
    value: float  # percent 0..100


@dataclass(frozen=True)
class AlertDecision:
    new_alerts: list[MetricSample]
    resolved_targets: list[tuple[str, str]]  # (target, metric)


class _AlertLike(Protocol):
    target: str
    metric: Any
    created_at: datetime
    resolved_at: datetime | None


class _ConfigLike(Protocol):
    alert_cpu_threshold: float
    alert_memory_threshold: float
    alert_disk_threshold: float
    alert_cooldown_minutes: int


def _pct(used: float, total: float) -> float | None:
    if total <= 0:
        return None
    return used / total * 100.0


def collect_samples(
    nodes: list[dict[str, Any]], resources: list[dict[str, Any]]
) -> list[MetricSample]:
    """純函式：由 PVE 原始回應取出待評估樣本。

    節點取 cpu/memory/disk；running VM 取 cpu/memory
    （cluster/resources 無可靠的 VM 磁碟用量）。
    """
    samples: list[MetricSample] = []
    for n in nodes:
        target = str(n.get("node") or "")
        if not target:
            continue
        cpu = float(n.get("cpu") or 0.0) * 100.0
        samples.append(
            MetricSample(scope="node", target=target, metric="cpu", value=cpu)
        )
        mem_pct = _pct(float(n.get("mem") or 0), float(n.get("maxmem") or 0))
        if mem_pct is not None:
            samples.append(
                MetricSample(
                    scope="node", target=target, metric="memory", value=mem_pct
                )
            )
        disk_pct = _pct(float(n.get("disk") or 0), float(n.get("maxdisk") or 0))
        if disk_pct is not None:
            samples.append(
                MetricSample(
                    scope="node", target=target, metric="disk", value=disk_pct
                )
            )

    for r in resources:
        if str(r.get("status") or "") != "running":
            continue
        target = str(r.get("vmid") or "")
        if not target:
            continue
        cpu = float(r.get("cpu") or 0.0) * 100.0
        samples.append(
            MetricSample(scope="vm", target=target, metric="cpu", value=cpu)
        )
        mem_pct = _pct(float(r.get("mem") or 0), float(r.get("maxmem") or 0))
        if mem_pct is not None:
            samples.append(
                MetricSample(
                    scope="vm", target=target, metric="memory", value=mem_pct
                )
            )
    return samples


def _threshold_for(config: _ConfigLike, metric: str) -> float:
    if metric == "cpu":
        return float(config.alert_cpu_threshold)
    if metric == "memory":
        return float(config.alert_memory_threshold)
    return float(config.alert_disk_threshold)


def evaluate(
    samples: list[MetricSample],
    alerts: Sequence[_AlertLike],
    config: _ConfigLike,
    now: datetime,
) -> AlertDecision:
    """純函式：比對樣本與現有告警，決定新事件與待 resolve 事件。

    ``alerts`` 需包含 open 事件與（供冷卻期判斷的）近期已 resolve 事件。
    樣本缺漏（節點暫時查不到）不觸發 resolve — 避免 PVE 抖動誤報恢復。
    """
    def _metric_str(alert: _AlertLike) -> str:
        metric = alert.metric
        return metric.value if hasattr(metric, "value") else str(metric)

    open_keys = {
        (a.target, _metric_str(a)) for a in alerts if a.resolved_at is None
    }
    latest_created: dict[tuple[str, str], datetime] = {}
    for a in alerts:
        key = (a.target, _metric_str(a))
        if key not in latest_created or a.created_at > latest_created[key]:
            latest_created[key] = a.created_at

    cooldown = timedelta(minutes=int(config.alert_cooldown_minutes))
    new_alerts: list[MetricSample] = []
    resolved: list[tuple[str, str]] = []

    for sample in samples:
        key = (sample.target, sample.metric)
        threshold = _threshold_for(config, sample.metric)
        if sample.value >= threshold:
            if key in open_keys:
                continue
            last = latest_created.get(key)
            if last is not None and now - last < cooldown:
                continue
            new_alerts.append(sample)
        elif key in open_keys and sample.value < threshold - HYSTERESIS:
            resolved.append(key)

    return AlertDecision(new_alerts=new_alerts, resolved_targets=resolved)


# ── I/O 協調 ──────────────────────────────────────────────────────────────

_last_run_monotonic: float | None = None


def _list_admin_emails(session: Session) -> list[str]:
    stmt = select(User).where(User.is_active == True)  # noqa: E712
    admins = [
        u
        for u in session.exec(stmt).all()
        if u.is_superuser or u.role == UserRole.admin
    ]
    return [str(u.email) for u in admins]


def _notify_admins(session: Session, created: list[AlertEvent]) -> None:
    emails = _list_admin_emails(session)
    for alert in created:
        subject = (
            f"[SkyLab 告警] {alert.target} {alert.metric.value} "
            f"{alert.value:.0f}%"
        )
        html = (
            f"<p>{alert.message}</p>"
            f"<p>目標：{alert.scope.value} {alert.target}<br/>"
            f"指標：{alert.metric.value}<br/>"
            f"量測值：{alert.value:.1f}%（閾值 {alert.threshold:.0f}%）<br/>"
            f"時間：{alert.created_at:%Y-%m-%d %H:%M:%S %Z}</p>"
        )
        for email in emails:
            try:
                send_email(email_to=email, subject=subject, html_content=html)
            except Exception:
                logger.warning(
                    "Failed to send alert email to %s for %s/%s",
                    email, alert.target, alert.metric.value,
                )


def process_resource_alerts() -> int:
    """Scheduler tick：依設定間隔抽樣並評估告警。回傳新建事件數。"""
    global _last_run_monotonic
    try:
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.alerts_enabled:
                return 0
            now_mono = time.monotonic()
            if (
                _last_run_monotonic is not None
                and now_mono - _last_run_monotonic
                < config.alert_check_interval_seconds
            ):
                return 0
            _last_run_monotonic = now_mono

            nodes = proxmox_service.list_nodes()
            resources = proxmox_service.list_all_resources()
            samples = collect_samples(nodes, resources)
            alerts = governance_repo.get_latest_alerts_by_key(session=session)
            now = datetime.now(timezone.utc)
            decision = evaluate(samples, alerts, config, now)

            created: list[AlertEvent] = []
            for sample in decision.new_alerts:
                event = AlertEvent(
                    scope=AlertScope(sample.scope),
                    target=sample.target,
                    metric=AlertMetric(sample.metric),
                    value=sample.value,
                    threshold=_threshold_for(config, sample.metric),
                    message=(
                        f"{sample.scope} {sample.target} 的 {sample.metric} "
                        f"用量達 {sample.value:.1f}%，超過閾值 "
                        f"{_threshold_for(config, sample.metric):.0f}%"
                    ),
                    created_at=now,
                )
                session.add(event)
                created.append(event)

            if decision.resolved_targets:
                open_alerts = governance_repo.get_open_alerts(session=session)
                resolved_keys = set(decision.resolved_targets)
                for alert in open_alerts:
                    if (alert.target, alert.metric.value) in resolved_keys:
                        alert.resolved_at = now
                        session.add(alert)

            session.commit()
            for event in created:
                session.refresh(event)

            if created:
                logger.warning(
                    "Resource alerts created: %s",
                    [(e.target, e.metric.value, round(e.value, 1)) for e in created],
                )
                if config.alert_email_enabled:
                    _notify_admins(session, created)
            return len(created)
    except Exception:
        logger.exception("process_resource_alerts failed")
        return 0
