"""反挖礦偵測掃描與兩段式處置的 I/O 協調層。

決策由 ``mining_policy`` 純函式產生；本模組負責 RRD 抽樣、快照存證、
暫停 VM、告警與通知、以及管理員的 ban/dismiss 審核動作。

處置順序鐵律：快照存證為 best-effort（60 秒逾時、失敗只記 log），
**絕不阻塞暫停** — 暫停才是止血動作。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlmodel import Session, select

from app.core.db import engine
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    AlertEvent,
    AlertMetric,
    AlertScope,
    Group,
    GroupMember,
    MiningIncident,
    MiningIncidentStatus,
    Resource,
    User,
)
from app.repositories import governance as governance_repo
from app.repositories import mining as mining_repo
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service
from app.services.security.mining_policy import (
    MiningAction,
    cpu_stats,
    decide_mining_action,
)
from app.services.user import audit_service
from app.utils import send_email

logger = logging.getLogger(__name__)

# 每台資源最短重掃間隔 — 限制 RRD 呼叫頻率（挖礦特徵以小時計）。
MINING_RESCAN_MINUTES = 30

# 存證快照最長等待；逾時視同失敗，不阻塞暫停。
SNAPSHOT_WAIT_TIMEOUT_SECONDS = 60.0

_OPEN_STATUSES = (MiningIncidentStatus.detected, MiningIncidentStatus.suspended)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pve_resource_map() -> dict[int, dict[str, Any]]:
    """vmid → cluster/resources 條目（單次 PVE 呼叫）。"""
    return {
        int(r["vmid"]): r
        for r in proxmox_service.list_all_resources()
        if r.get("vmid") is not None
    }


def _resource_type(pve_type: str) -> Literal["qemu", "lxc"]:
    return "lxc" if pve_type == "lxc" else "qemu"


def _get_user(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


# ── 偵測掃描 ──────────────────────────────────────────────────────────────


def _fetch_cpu_stats(
    resource: Resource,
    pve_info: dict[str, Any],
    *,
    window_hours: int,
    now: datetime,
) -> tuple[float, float] | None:
    node = str(pve_info.get("node") or "")
    rtype = _resource_type(str(pve_info.get("type") or ""))
    if not node:
        return None
    rrd = proxmox_service.get_rrd_data(node, resource.vmid, rtype, "day")
    return cpu_stats(rrd, window_hours=window_hours, now=now)


def _scan_one(
    session: Session,
    resource: Resource,
    pve_info: dict[str, Any],
    config: Any,
    *,
    now: datetime,
) -> bool:
    """掃描單台資源；命中則建事件並處置。回傳是否命中。

    無論命中/未命中/失敗，一律推進 ``mining_checked_at`` —
    否則低 CPU 的 VM 會永遠佔住最舊清單，其他 VM 輪不到掃描。
    """
    flagged = False
    try:
        stats = _fetch_cpu_stats(
            resource,
            pve_info,
            window_hours=config.mining_window_hours,
            now=now,
        )
        action = decide_mining_action(
            avg_cpu=stats[0] if stats else None,
            coverage=stats[1] if stats else 0.0,
            exempt=bool(resource.mining_exempt),
            has_open_incident=mining_repo.has_open_incident(
                session=session, vmid=resource.vmid
            ),
            threshold_percent=config.mining_cpu_threshold_percent,
        )
        if action is MiningAction.flag and stats is not None:
            incident = mining_repo.create_incident(
                session=session,
                vmid=resource.vmid,
                user_id=resource.user_id,
                node=str(pve_info.get("node") or ""),
                resource_type=_resource_type(str(pve_info.get("type") or "")),
                avg_cpu=stats[0],
                window_hours=config.mining_window_hours,
                now=now,
            )
            audit_service.log_action(
                session=session,
                user_id=None,
                vmid=resource.vmid,
                action="mining_detected",
                details=(
                    f"Sustained high CPU {stats[0]:.1f}% over "
                    f"{config.mining_window_hours}h (threshold "
                    f"{config.mining_cpu_threshold_percent:.0f}%)"
                ),
                commit=False,
            )
            respond_to_incident(session, incident, resource, config, now=now)
            flagged = True
            logger.warning(
                "Mining suspected: vmid=%s avg_cpu=%.1f%% window=%dh",
                resource.vmid, stats[0], config.mining_window_hours,
            )
    except Exception:
        session.rollback()
        logger.exception("Mining scan failed for vmid=%s", resource.vmid)
    finally:
        resource.mining_checked_at = now
        session.add(resource)
        session.commit()
    return flagged


def process_mining_detection() -> int:
    """Scheduler tick：挖礦偵測（每 tick 至多掃 mining_scan_batch_size 台）。"""
    try:
        flagged = 0
        now = _utc_now()
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.mining_detection_enabled:
                return 0

            pve_map = _pve_resource_map()
            running_vmids = [
                vmid
                for vmid, info in pve_map.items()
                if str(info.get("status") or "") == "running"
            ]
            candidates = resource_repo.list_mining_scan_candidates(
                session=session,
                vmids=running_vmids,
                checked_before=now - timedelta(minutes=MINING_RESCAN_MINUTES),
                limit=config.mining_scan_batch_size,
            )
            for resource in candidates:
                pve_info = pve_map.get(resource.vmid)
                if pve_info is None:
                    continue
                if _scan_one(session, resource, pve_info, config, now=now):
                    flagged += 1
        return flagged
    except Exception:
        logger.exception("process_mining_detection failed")
        return 0


# ── 自動處置（偵測後立即執行）────────────────────────────────────────────


def _snapshot_evidence(incident: MiningIncident, *, now: datetime) -> str | None:
    """存證快照 — best-effort：逾時/失敗回 None，絕不拋出。"""
    snapname = f"mining-{now:%Y%m%d%H%M}"
    try:
        proxmox_service.create_snapshot(
            incident.node,
            incident.vmid,
            _resource_type(incident.resource_type),
            wait_timeout_seconds=SNAPSHOT_WAIT_TIMEOUT_SECONDS,
            snapname=snapname,
            description=(
                f"Mining evidence (auto) — avg CPU {incident.avg_cpu:.1f}% "
                f"over {incident.window_hours}h"
            ),
        )
        return snapname
    except Exception:
        logger.warning(
            "Evidence snapshot failed for vmid=%s (continuing to suspend)",
            incident.vmid,
            exc_info=True,
        )
        return None


def _create_alert_event(
    session: Session, incident: MiningIncident, config: Any
) -> None:
    event = AlertEvent(
        scope=AlertScope.vm,
        target=str(incident.vmid),
        metric=AlertMetric.cpu,
        value=incident.avg_cpu,
        threshold=config.mining_cpu_threshold_percent,
        message=(
            f"疑似挖礦：VMID {incident.vmid} 過去 {incident.window_hours} 小時"
            f"平均 CPU {incident.avg_cpu:.1f}%，已觸發自動處置"
        ),
        created_at=incident.detected_at,
    )
    session.add(event)


def _teacher_emails(session: Session, user_id: uuid.UUID) -> list[str]:
    """使用者所屬全部群組的 owner（老師）email。"""
    stmt = (
        select(User.email)
        .join(Group, Group.owner_id == User.id)  # type: ignore[arg-type]
        .join(GroupMember, GroupMember.group_id == Group.id)  # type: ignore[arg-type]
        .where(GroupMember.user_id == user_id, User.is_active == True)  # noqa: E712
    )
    return [str(e) for e in session.exec(stmt).all() if e]


def _notify_incident(
    session: Session, incident: MiningIncident, resource: Resource
) -> None:
    from app.services.monitoring.alert_service import (
        _list_admin_emails,  # noqa: PLC0415 — 複用管理員清單，避免重複實作
    )

    recipients = set(_list_admin_emails(session))
    recipients.update(_teacher_emails(session, incident.user_id))
    owner = resource.user
    owner_label = (
        f"{owner.full_name or owner.email}" if owner is not None else "未知使用者"
    )
    subject = f"[SkyLab 安全] VMID {incident.vmid} 疑似挖礦，已自動處置"
    html = (
        f"<p>系統偵測到 VMID {incident.vmid}（擁有者：{owner_label}）"
        f"過去 {incident.window_hours} 小時平均 CPU "
        f"{incident.avg_cpu:.1f}%，疑似挖礦行為。</p>"
        f"<p>已執行：存證快照（{incident.snapshot_name or '失敗'}）、"
        f"{'暫停 VM' if incident.status is MiningIncidentStatus.suspended else '（未暫停）'}。</p>"
        "<p>請管理員至「資源監控 → 挖礦事件」確認後決定停權或解除。</p>"
    )
    for email in recipients:
        try:
            send_email(email_to=email, subject=subject, html_content=html)
        except Exception:
            logger.warning(
                "Failed to send mining notification for vmid=%s to %s",
                incident.vmid, email,
            )


def respond_to_incident(
    session: Session,
    incident: MiningIncident,
    resource: Resource,
    config: Any,
    *,
    now: datetime,
) -> None:
    """自動段處置：存證 → 暫停 → 告警 → 通知。

    ``mining_auto_suspend=False`` 時跳過存證與暫停（事件停留 detected，
    仍發告警與通知，處置全人工）。
    """
    if config.mining_auto_suspend:
        incident.snapshot_name = _snapshot_evidence(incident, now=now)
        try:
            action = "suspend" if incident.resource_type == "qemu" else "stop"
            proxmox_service.control(
                incident.node,
                incident.vmid,
                _resource_type(incident.resource_type),
                action,
            )
            incident.status = MiningIncidentStatus.suspended
            incident.suspended_at = now
            audit_service.log_action(
                session=session,
                user_id=None,
                vmid=incident.vmid,
                action="mining_suspend",
                details=f"Auto-{action} on mining suspicion",
                commit=False,
            )
        except Exception:
            logger.exception(
                "Failed to suspend vmid=%s on mining suspicion (stays detected)",
                incident.vmid,
            )
    session.add(incident)
    _create_alert_event(session, incident, config)
    _notify_incident(session, incident, resource)


# ── 人工段（管理員審核）──────────────────────────────────────────────────


def _get_open_incident_for_review(
    session: Session, incident_id: uuid.UUID
) -> MiningIncident:
    incident = mining_repo.get_incident(session=session, incident_id=incident_id)
    if incident.status not in _OPEN_STATUSES:
        raise BadRequestError("Incident is already closed")
    return incident


def ban_incident(
    *, session: Session, incident_id: uuid.UUID, admin: User
) -> MiningIncident:
    """管理員確認挖礦 → 帳號停權（VM 維持暫停狀態，留存證據）。"""
    incident = _get_open_incident_for_review(session, incident_id)
    owner = _get_user(session, incident.user_id)
    if owner is None:
        raise NotFoundError("Incident owner no longer exists")
    owner.is_active = False
    session.add(owner)
    incident.status = MiningIncidentStatus.banned
    incident.reviewed_by = admin.id
    incident.reviewed_at = _utc_now()
    session.add(incident)
    audit_service.log_action(
        session=session,
        user_id=admin.id,
        vmid=incident.vmid,
        action="mining_ban",
        details=f"Account {owner.id} deactivated for mining incident {incident.id}",
        commit=False,
    )
    session.commit()
    logger.warning(
        "Mining ban: user=%s vmid=%s incident=%s by admin=%s",
        owner.id, incident.vmid, incident.id, admin.id,
    )
    return incident


def dismiss_incident(
    *,
    session: Session,
    incident_id: uuid.UUID,
    admin: User,
    exempt: bool,
    note: str | None,
) -> MiningIncident:
    """管理員判定誤判 → 恢復 VM（best-effort），可一併加入豁免。"""
    incident = _get_open_incident_for_review(session, incident_id)
    if incident.status is MiningIncidentStatus.suspended:
        try:
            action = "resume" if incident.resource_type == "qemu" else "start"
            proxmox_service.control(
                incident.node,
                incident.vmid,
                _resource_type(incident.resource_type),
                action,
            )
        except Exception:
            logger.warning(
                "Failed to resume vmid=%s on dismiss (manual start may be needed)",
                incident.vmid,
                exc_info=True,
            )
    if exempt:
        resource = resource_repo.get_resource_by_vmid(
            session=session, vmid=incident.vmid
        )
        if resource is not None:
            resource.mining_exempt = True
            session.add(resource)
    incident.status = MiningIncidentStatus.dismissed
    incident.reviewed_by = admin.id
    incident.reviewed_at = _utc_now()
    incident.review_note = note
    session.add(incident)
    audit_service.log_action(
        session=session,
        user_id=admin.id,
        vmid=incident.vmid,
        action="mining_dismiss",
        details=(
            f"Incident {incident.id} dismissed"
            f"{' with exemption' if exempt else ''}"
        ),
        commit=False,
    )
    session.commit()
    logger.info(
        "Mining incident dismissed: vmid=%s incident=%s exempt=%s",
        incident.vmid, incident.id, exempt,
    )
    return incident
