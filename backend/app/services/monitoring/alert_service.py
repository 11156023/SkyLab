"""資源閾值警告：抽樣 → 評估（純函式）→ 落 DB + Email 通知。

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
from app.core.permissions import is_admin
from app.models import AlertEvent, AlertMetric, AlertScope, User
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


def _enum_str(value: Any) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def inactive_vm_targets(
    resources: list[dict[str, Any]],
    open_vm_targets: set[str],
    *,
    complete: bool,
    exclude: set[str] | None = None,
) -> set[str]:
    """純函式：已開啟 VM 告警中，VM 已不在執行的目標（vmid 字串）。

    - PVE 回報存在但 status 不是 running（已關機）→ 算
    - 完全查不到（已刪除）→ 只有這一輪所有 PVE 連線都查成功（``complete``）才算，
      避免某個連線暫時斷線時把整個叢集的告警誤判為恢復
    - ``exclude``：不自動收掉的目標（例如反挖礦暫停中的 VM，告警要留給管理員審核）
    """
    status_by_target = {
        str(r.get("vmid")): str(r.get("status") or "")
        for r in resources
        if r.get("vmid") is not None
    }
    skip = exclude or set()
    inactive: set[str] = set()
    for target in open_vm_targets:
        if target in skip:
            continue
        status = status_by_target.get(target)
        if status is None:
            if complete:
                inactive.add(target)
        elif status != "running":
            inactive.add(target)
    return inactive


def evaluate(
    samples: list[MetricSample],
    alerts: Sequence[_AlertLike],
    config: _ConfigLike,
    now: datetime,
    *,
    inactive_targets: set[str] | None = None,
) -> AlertDecision:
    """純函式：比對樣本與現有警告，決定新事件與待 resolve 事件。

    ``alerts`` 需包含 open 事件與（供冷卻期判斷的）近期已 resolve 事件。
    樣本缺漏（節點暫時查不到）不觸發 resolve — 避免 PVE 抖動誤報恢復。
    例外是 ``inactive_targets``：確定已關機／已刪除的 VM 不會再有樣本，
    它們的 VM 告警直接 resolve，否則會永遠掛在進行中清單。
    """
    def _metric_str(alert: _AlertLike) -> str:
        return _enum_str(alert.metric)

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

    if inactive_targets:
        for a in alerts:
            if a.resolved_at is not None or a.target not in inactive_targets:
                continue
            if _enum_str(getattr(a, "scope", "vm")) != "vm":
                continue
            key = (a.target, _metric_str(a))
            if key not in resolved:
                resolved.append(key)

    return AlertDecision(new_alerts=new_alerts, resolved_targets=resolved)


# ── I/O 協調 ──────────────────────────────────────────────────────────────

class _AlertTickState:
    """上次抽樣的 monotonic 時間，供 tick 節流（集中在物件上，避免 global 重新指派）。"""

    last_run_monotonic: float | None = None


_tick_state = _AlertTickState()


def list_active_admin_emails(session: Session) -> list[str]:
    """所有啟用中管理員的 email，供各模組寄管理員通知。"""
    stmt = select(User).where(User.is_active == True)  # noqa: E712
    return [str(u.email) for u in session.exec(stmt).all() if is_admin(u)]


def _notify_admins(session: Session, created: list[AlertEvent]) -> None:
    emails = list_active_admin_emails(session)
    for alert in created:
        subject = (
            f"[SkyLab 警告] {alert.target} {alert.metric.value} "
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


def _alerts_for_evaluation(session: Session, *, since: datetime) -> list[AlertEvent]:
    """評估需要的警告：所有 open 事件＋冷卻期內建立的事件（含已 resolve）。

    不能只取「最新 N 筆」：事件表不會清理，長期未恢復的警告一旦被擠出視窗，
    就會被當成沒開過而重複建立、重複寄信。
    """
    open_alerts = governance_repo.get_open_alerts(session=session)
    recent = session.exec(
        select(AlertEvent).where(AlertEvent.created_at >= since)
    ).all()
    merged: dict[Any, AlertEvent] = {a.id: a for a in open_alerts}
    for alert in recent:
        merged.setdefault(alert.id, alert)
    return list(merged.values())


def _open_mining_targets(session: Session) -> set[str]:
    """未結案挖礦事件的 vmid：VM 被暫停／關機是處置的一部分，告警留給管理員審核。"""
    from app.services.security import mining_service

    return {str(vmid) for vmid in mining_service.open_incident_vmids(session)}


def process_resource_alerts() -> int:
    """Scheduler tick：依設定間隔抽樣並評估警告。回傳新建事件數。"""
    try:
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.alerts_enabled:
                return 0
            now_mono = time.monotonic()
            last_run = _tick_state.last_run_monotonic
            if (
                last_run is not None
                and now_mono - last_run < config.alert_check_interval_seconds
            ):
                return 0
            _tick_state.last_run_monotonic = now_mono

            # 同一輪取回 nodes/resources 並知道有沒有連線失敗（判斷「VM 已刪除」要用）
            snapshot = proxmox_service.collect_monitoring_snapshot()
            samples = collect_samples(snapshot.nodes, snapshot.resources)
            now = datetime.now(timezone.utc)
            alerts = _alerts_for_evaluation(
                session,
                since=now - timedelta(minutes=int(config.alert_cooldown_minutes)),
            )
            open_vm_targets = {
                a.target
                for a in alerts
                if a.resolved_at is None and a.scope == AlertScope.vm
            }
            inactive = (
                inactive_vm_targets(
                    snapshot.resources,
                    open_vm_targets,
                    complete=snapshot.failed_connections == 0,
                    exclude=_open_mining_targets(session),
                )
                if open_vm_targets
                else set()
            )
            decision = evaluate(
                samples, alerts, config, now, inactive_targets=inactive
            )

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
