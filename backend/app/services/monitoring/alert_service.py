"""資源閾值警告：抽樣 → 評估（純函式）→ 落 DB + Email 通知。

評估邏輯（collect_samples / evaluate）為純函式，不碰 DB/PVE/SMTP，
方便單元測試；I/O 由 process_resource_alerts 協調。
"""

from __future__ import annotations

import html as html_lib
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlmodel import Session, select

from app.core.db import engine
from app.core.permissions import is_admin
from app.models import AlertEvent, AlertMetric, AlertScope, User
from app.repositories import governance as governance_repo
from app.repositories import proxmox_connection as proxmox_connection_repo
from app.repositories import proxmox_node as proxmox_node_repo
from app.schemas.monitoring import AlertEventPublic
from app.services.proxmox import proxmox_service
from app.utils import send_email

logger = logging.getLogger(__name__)

# 遲滯：量測值須回落到「閾值 − HYSTERESIS」以下才自動 resolve
HYSTERESIS = 5.0

# 告警鍵 (connection_id, target, metric)：多叢集下 vmid 只有搭配連線才唯一
AlertKey = tuple[int | None, str, str]
# VM 告警的目標鍵 (connection_id, vmid 字串)
VmTargetKey = tuple[int | None, str]


@dataclass(frozen=True)
class MetricSample:
    scope: str   # cluster|node|vm
    target: str  # 節點名或 vmid 字串
    metric: str  # cpu|memory|disk
    value: float  # percent 0..100
    # 樣本所在的 PVE 連線；節點未歸屬任何連線時為 None
    connection_id: int | None = None


@dataclass(frozen=True)
class AlertDecision:
    new_alerts: list[MetricSample]
    resolved_targets: list[AlertKey]


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


def _node_connection(
    node_connections: Mapping[str, int | None] | None, node: Any
) -> int | None:
    if not node_connections:
        return None
    return node_connections.get(str(node or ""))


def collect_samples(
    nodes: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    node_connections: Mapping[str, int | None] | None = None,
) -> list[MetricSample]:
    """純函式：由 PVE 原始回應取出待評估樣本。

    節點取 cpu/memory/disk；running VM 取 cpu/memory
    （cluster/resources 無可靠的 VM 磁碟用量）。
    ``node_connections``（節點名稱 → 連線 id）決定樣本屬於哪個叢集；
    VM 以它所在的節點判定。查不到的節點視為 None。
    """
    samples: list[MetricSample] = []
    for n in nodes:
        target = str(n.get("node") or "")
        if not target:
            continue
        # 離線／unknown 節點 PVE 不回 cpu 等欄位：補 0 會變成假的 0% 樣本，
        # 把仍未恢復的 CPU 警告誤判 resolve；當成樣本缺漏處理
        if str(n.get("status") or "online") != "online":
            continue
        connection_id = _node_connection(node_connections, target)
        cpu = float(n.get("cpu") or 0.0) * 100.0
        samples.append(
            MetricSample(
                scope="node", target=target, metric="cpu", value=cpu,
                connection_id=connection_id,
            )
        )
        mem_pct = _pct(float(n.get("mem") or 0), float(n.get("maxmem") or 0))
        if mem_pct is not None:
            samples.append(
                MetricSample(
                    scope="node", target=target, metric="memory", value=mem_pct,
                    connection_id=connection_id,
                )
            )
        disk_pct = _pct(float(n.get("disk") or 0), float(n.get("maxdisk") or 0))
        if disk_pct is not None:
            samples.append(
                MetricSample(
                    scope="node", target=target, metric="disk", value=disk_pct,
                    connection_id=connection_id,
                )
            )

    for r in resources:
        if str(r.get("status") or "") != "running":
            continue
        target = str(r.get("vmid") or "")
        if not target:
            continue
        connection_id = _node_connection(node_connections, r.get("node"))
        cpu = float(r.get("cpu") or 0.0) * 100.0
        samples.append(
            MetricSample(
                scope="vm", target=target, metric="cpu", value=cpu,
                connection_id=connection_id,
            )
        )
        mem_pct = _pct(float(r.get("mem") or 0), float(r.get("maxmem") or 0))
        if mem_pct is not None:
            samples.append(
                MetricSample(
                    scope="vm", target=target, metric="memory", value=mem_pct,
                    connection_id=connection_id,
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


def alert_key(alert: Any) -> AlertKey:
    """告警的比對鍵 (connection_id, target, metric)。"""
    return (
        getattr(alert, "connection_id", None),
        str(alert.target),
        _enum_str(alert.metric),
    )


def _matching_keys(
    connection_id: int | None, target: str, metric: str
) -> tuple[AlertKey, ...]:
    """樣本可對上的告警鍵：同連線的那一筆，加上同目標、連線為 NULL 的那一筆。

    連線為 NULL 的告警是升級前對不到連線的舊資料，或當時節點尚未歸屬連線；
    之後同一目標的樣本帶了連線，仍要能解除它、也不能因此再開一筆重複的。
    """
    exact = (connection_id, target, metric)
    if connection_id is None:
        return (exact,)
    return (exact, (None, target, metric))


def inactive_vm_targets(
    resources: list[dict[str, Any]],
    open_vm_targets: set[VmTargetKey],
    *,
    complete: bool,
    exclude: set[str] | None = None,
    node_connections: Mapping[str, int | None] | None = None,
) -> set[VmTargetKey]:
    """純函式：已開啟 VM 告警中，VM 已不在執行的目標（(connection_id, vmid 字串)）。

    - PVE 回報存在但 status 不是 running（已關機）→ 算
    - 完全查不到（已刪除）→ 只有這一輪所有 PVE 連線都查成功（``complete``）才算，
      避免某個連線暫時斷線時把整個叢集的告警誤判為恢復
    - ``exclude``：不自動收掉的 vmid（例如反挖礦暫停中的 VM，告警要留給管理員審核）
    - 只看同一連線的同號 VM；告警或 VM 的連線為 None 時無法區分叢集，同號都算
    """
    statuses: dict[str, list[tuple[int | None, str]]] = {}
    for r in resources:
        if r.get("vmid") is None:
            continue
        statuses.setdefault(str(r.get("vmid")), []).append(
            (
                _node_connection(node_connections, r.get("node")),
                str(r.get("status") or ""),
            )
        )
    skip = exclude or set()
    inactive: set[VmTargetKey] = set()
    for key in open_vm_targets:
        connection_id, target = key
        if target in skip:
            continue
        matched = [
            status
            for entry_connection, status in statuses.get(target, [])
            if connection_id is None
            or entry_connection is None
            or entry_connection == connection_id
        ]
        if not matched:
            if complete:
                inactive.add(key)
        elif all(status != "running" for status in matched):
            inactive.add(key)
    return inactive


def evaluate(
    samples: list[MetricSample],
    alerts: Sequence[_AlertLike],
    config: _ConfigLike,
    now: datetime,
    *,
    inactive_targets: set[VmTargetKey] | None = None,
) -> AlertDecision:
    """純函式：比對樣本與現有警告，決定新事件與待 resolve 事件。

    告警以 (connection_id, target, metric) 比對（見 ``_matching_keys``）。
    ``alerts`` 需包含 open 事件與（供冷卻期判斷的）近期已 resolve 事件。
    樣本缺漏（節點暫時查不到）不觸發 resolve — 避免 PVE 抖動誤報恢復。
    例外是 ``inactive_targets``：確定已關機／已刪除的 VM 不會再有樣本，
    它們的 VM 告警直接 resolve，否則會永遠掛在進行中清單。
    """
    open_keys = {alert_key(a) for a in alerts if a.resolved_at is None}
    latest_created: dict[AlertKey, datetime] = {}
    for a in alerts:
        key = alert_key(a)
        if key not in latest_created or a.created_at > latest_created[key]:
            latest_created[key] = a.created_at

    cooldown = timedelta(minutes=int(config.alert_cooldown_minutes))
    new_alerts: list[MetricSample] = []
    pending: set[AlertKey] = set()
    # 一筆告警可能對上多個樣本（連線為 NULL 的舊告警）：任何一個樣本
    # 仍在閾值或遲滯區間內就不解除
    held: set[AlertKey] = set()
    resolvable: list[AlertKey] = []

    for sample in samples:
        key = (sample.connection_id, sample.target, sample.metric)
        candidates = _matching_keys(*key)
        matched = [k for k in candidates if k in open_keys]
        threshold = _threshold_for(config, sample.metric)
        if sample.value >= threshold:
            if matched:
                held.update(matched)
                continue
            if key in pending:
                continue
            last = max(
                (latest_created[k] for k in candidates if k in latest_created),
                default=None,
            )
            if last is not None and now - last < cooldown:
                continue
            new_alerts.append(sample)
            pending.add(key)
        elif sample.value < threshold - HYSTERESIS:
            resolvable.extend(k for k in matched if k not in resolvable)
        else:
            held.update(matched)

    resolved = [k for k in resolvable if k not in held]

    if inactive_targets:
        for a in alerts:
            if a.resolved_at is not None:
                continue
            if _enum_str(getattr(a, "scope", "vm")) != "vm":
                continue
            if (getattr(a, "connection_id", None), a.target) not in inactive_targets:
                continue
            key = alert_key(a)
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


def node_connection_ids(session: Session) -> dict[str, int | None]:
    """節點名稱 → 所屬連線 id（未歸屬連線的節點為 None）。"""
    return {
        name: connection_id
        for name, (connection_id, _name) in proxmox_node_repo.get_node_connection_map(
            session
        ).items()
    }


def connection_id_for_node(session: Session, node: str) -> int | None:
    """單一節點所屬的連線 id；與資源告警評估用同一份對應（proxmox_nodes）。"""
    row = proxmox_node_repo.get_node_by_name(session, node) if node else None
    return row.connection_id if row is not None else None


def _connection_names(session: Session) -> dict[int, str]:
    return {
        conn.id: conn.name
        for conn in proxmox_connection_repo.get_all_connections(session)
        if conn.id is not None
    }


def _notify_admins(session: Session, created: list[AlertEvent]) -> None:
    emails = list_active_admin_emails(session)
    names = _connection_names(session) if created else {}
    for alert in created:
        cluster = (
            names.get(alert.connection_id) if alert.connection_id is not None else None
        )
        # 主旨開頭維持原樣，叢集名稱只附在最後，既有的信件篩選規則仍對得上
        subject = (
            f"[SkyLab 警告] {alert.target} {alert.metric.value} "
            f"{alert.value:.0f}%"
        )
        if cluster:
            subject += f"（{cluster}）"
        cluster_line = f"叢集：{html_lib.escape(cluster)}<br/>" if cluster else ""
        html = (
            f"<p>{alert.message}</p>"
            f"<p>{cluster_line}"
            f"目標：{alert.scope.value} {alert.target}<br/>"
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


def to_public(session: Session, alerts: Sequence[AlertEvent]) -> list[AlertEventPublic]:
    """告警轉 API 輸出，附上所屬叢集（連線）名稱。"""
    names = (
        _connection_names(session)
        if any(a.connection_id is not None for a in alerts)
        else {}
    )
    return [
        AlertEventPublic.model_validate(alert, from_attributes=True).model_copy(
            update={
                "connection_name": (
                    names.get(alert.connection_id)
                    if alert.connection_id is not None
                    else None
                )
            }
        )
        for alert in alerts
    ]


def list_alerts(
    session: Session, *, active_only: bool, limit: int
) -> list[AlertEventPublic]:
    alerts = governance_repo.list_alerts(
        session=session, active_only=active_only, limit=limit
    )
    return to_public(session, alerts)


def acknowledge_alert(
    session: Session, *, alert_id: uuid.UUID, user_id: uuid.UUID
) -> AlertEventPublic:
    alert = governance_repo.acknowledge_alert(
        session=session, alert_id=alert_id, user_id=user_id
    )
    return to_public(session, [alert])[0]


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
            node_connections = node_connection_ids(session)
            samples = collect_samples(
                snapshot.nodes, snapshot.resources, node_connections
            )
            now = datetime.now(timezone.utc)
            alerts = _alerts_for_evaluation(
                session,
                since=now - timedelta(minutes=int(config.alert_cooldown_minutes)),
            )
            open_vm_targets = {
                (a.connection_id, a.target)
                for a in alerts
                if a.resolved_at is None and a.scope == AlertScope.vm
            }
            inactive = (
                inactive_vm_targets(
                    snapshot.resources,
                    open_vm_targets,
                    complete=snapshot.failed_connections == 0,
                    exclude=_open_mining_targets(session),
                    node_connections=node_connections,
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
                    connection_id=sample.connection_id,
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
                    if alert_key(alert) in resolved_keys:
                        alert.resolved_at = now
                        session.add(alert)

            session.commit()
            for event in created:
                session.refresh(event)

            if created:
                logger.warning(
                    "Resource alerts created: %s",
                    [
                        (e.connection_id, e.target, e.metric.value, round(e.value, 1))
                        for e in created
                    ],
                )
                if config.alert_email_enabled:
                    _notify_admins(session, created)
            return len(created)
    except Exception:
        logger.exception("process_resource_alerts failed")
        return 0
