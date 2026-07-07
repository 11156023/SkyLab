"""TTL 漸進回收與閒置偵測的 I/O 協調層。

決策由 ``lifecycle_policy`` 純函式產生；本模組負責 PVE 查詢、DB 更新、
排程既有 auto-stop 管線、進刪除佇列與 Email 通知。
Email 失敗一律吞掉（log warning），不得使排程 task 崩潰。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlmodel import Session

from app.core.db import engine
from app.models import Resource
from app.repositories import governance as governance_repo
from app.repositories import resource as resource_repo
from app.services.governance.lifecycle_policy import (
    IdleAction,
    TtlAction,
    average_cpu_percent,
    decide_idle_action,
    decide_ttl_action,
)
from app.services.proxmox import proxmox_service
from app.utils import send_email

logger = logging.getLogger(__name__)

# 每台資源最短重掃間隔 — 限制 RRD 呼叫頻率（閒置狀態變化以小時計）。
IDLE_RESCAN_MINUTES = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pve_resource_map() -> dict[int, dict[str, Any]]:
    """vmid → cluster/resources 條目（單次 PVE 呼叫）。"""
    return {
        int(r["vmid"]): r
        for r in proxmox_service.list_all_resources()
        if r.get("vmid") is not None
    }


def _owner_email(resource: Resource) -> str | None:
    user = resource.user
    if user is None or not user.email:
        return None
    return str(user.email)


def _send_owner_email(resource: Resource, subject: str, html: str) -> None:
    email = _owner_email(resource)
    if email is None:
        return
    try:
        send_email(email_to=email, subject=subject, html_content=html)
    except Exception:
        logger.warning(
            "Failed to send lifecycle email for vmid=%s to %s",
            resource.vmid, email,
        )


# ── TTL 漸進回收 ──────────────────────────────────────────────────────────


def _apply_ttl_warn(session: Session, resource: Resource, now: datetime) -> None:
    _send_owner_email(
        resource,
        f"[SkyLab] 您的資源 VMID {resource.vmid} 即將到期",
        (
            f"<p>您的資源（VMID {resource.vmid}）將於 "
            f"<b>{resource.expiry_date}</b> 到期。</p>"
            "<p>到期後系統會自動關機，寬限期過後將自動刪除。"
            "如需延長，請聯絡管理員或提出規格調整申請。</p>"
        ),
    )
    resource.expiry_notified_at = now
    session.add(resource)


def _apply_ttl_stop(session: Session, resource: Resource, now: datetime) -> None:
    # 已排程過同因關機就不重發（等待 process_auto_stops 執行）
    if resource.auto_stop_reason == "ttl_expired" and resource.auto_stop_at is not None:
        return
    resource_repo.set_auto_stop(
        session=session,
        vmid=resource.vmid,
        auto_stop_at=now,
        auto_stop_reason="ttl_expired",
        commit=False,
    )
    _send_owner_email(
        resource,
        f"[SkyLab] 資源 VMID {resource.vmid} 已到期，將自動關機",
        (
            f"<p>您的資源（VMID {resource.vmid}）已於 "
            f"{resource.expiry_date} 到期，系統即將自動關機。</p>"
            "<p>寬限期過後資源將被刪除，請儘速備份需要的資料。</p>"
        ),
    )
    logger.info("TTL expiry auto-stop scheduled for vmid=%s", resource.vmid)


def _apply_ttl_delete(
    session: Session,
    resource: Resource,
    now: datetime,
    pve_info: dict[str, Any] | None,
) -> None:
    from app.services.resource import (
        deletion_service,  # noqa: PLC0415 — 避免 import cycle
    )

    if pve_info is None:
        # PVE 上已不存在 — 不入刪除佇列，只記錄避免每 tick 重試。
        logger.warning(
            "TTL delete skipped: vmid=%s not found on Proxmox (stale DB row?)",
            resource.vmid,
        )
        resource.scheduled_deletion_at = now
        session.add(resource)
        return

    deletion_service.create_deletion_request(
        session=session,
        user_id=resource.user_id,
        vmid=resource.vmid,
        resource_info={
            "name": pve_info.get("name"),
            "node": pve_info.get("node"),
            "type": pve_info.get("type"),
        },
        purge=True,
        force=True,
    )
    resource.scheduled_deletion_at = now
    session.add(resource)
    _send_owner_email(
        resource,
        f"[SkyLab] 資源 VMID {resource.vmid} 已進入刪除佇列",
        (
            f"<p>您的資源（VMID {resource.vmid}）到期寬限期已滿，"
            "系統已將其加入自動刪除佇列。</p>"
        ),
    )
    logger.warning("TTL grace elapsed: vmid=%s queued for deletion", resource.vmid)


def process_ttl_lifecycle() -> int:
    """Scheduler tick：TTL 漸進回收（通知 → 關機 → 寬限期 → 刪除佇列）。"""
    try:
        actions = 0
        now = _utc_now()
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.ttl_enabled:
                return 0
            resources = resource_repo.list_resources_with_expiry(session=session)
            if not resources:
                return 0
            pve_map = _pve_resource_map()

            for resource in resources:
                pve_info = pve_map.get(resource.vmid)
                is_running = (
                    pve_info is not None
                    and str(pve_info.get("status") or "") == "running"
                )
                action = decide_ttl_action(
                    expiry_date=resource.expiry_date,
                    expiry_notified_at=resource.expiry_notified_at,
                    scheduled_deletion_at=resource.scheduled_deletion_at,
                    is_running=is_running,
                    now=now,
                    warn_days=config.expiry_warn_days,
                    grace_delete_days=config.expiry_grace_delete_days,
                )
                if action is TtlAction.none:
                    continue
                try:
                    if action is TtlAction.warn:
                        _apply_ttl_warn(session, resource, now)
                    elif action is TtlAction.stop:
                        _apply_ttl_stop(session, resource, now)
                    elif action is TtlAction.delete:
                        _apply_ttl_delete(session, resource, now, pve_info)
                    actions += 1
                    session.commit()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "TTL action %s failed for vmid=%s",
                        action.value, resource.vmid,
                    )
        return actions
    except Exception:
        logger.exception("process_ttl_lifecycle failed")
        return 0


# ── 閒置偵測 ──────────────────────────────────────────────────────────────


def _fetch_avg_cpu(
    resource: Resource,
    pve_info: dict[str, Any],
    *,
    window_hours: int,
    now: datetime,
) -> float | None:
    node = str(pve_info.get("node") or "")
    rtype: Literal["qemu", "lxc"] = (
        "lxc" if str(pve_info.get("type") or "") == "lxc" else "qemu"
    )
    if not node:
        return None
    try:
        rrd = proxmox_service.get_rrd_data(node, resource.vmid, rtype, "day")
    except Exception:
        logger.warning("Failed to fetch RRD for vmid=%s", resource.vmid)
        return None
    return average_cpu_percent(rrd, window_hours=window_hours, now=now)


def _apply_idle_action(
    session: Session,
    resource: Resource,
    action: IdleAction,
    now: datetime,
) -> None:
    if action is IdleAction.mark:
        resource.idle_since = now
        resource.idle_notified_at = now
        session.add(resource)
        _send_owner_email(
            resource,
            f"[SkyLab] 資源 VMID {resource.vmid} 疑似閒置",
            (
                f"<p>您的資源（VMID {resource.vmid}）CPU 使用率已長時間低於閾值，"
                "被判定為閒置。</p>"
                "<p>若持續閒置，系統將自動關機（資料保留，可隨時重新開機）。"
                "若您仍在使用，請忽略此信 — 有實際負載後標記會自動解除。</p>"
            ),
        )
        logger.info("Idle detected: vmid=%s marked", resource.vmid)
    elif action is IdleAction.stop:
        resource_repo.set_auto_stop(
            session=session,
            vmid=resource.vmid,
            auto_stop_at=now,
            auto_stop_reason="idle",
            commit=False,
        )
        _send_owner_email(
            resource,
            f"[SkyLab] 閒置資源 VMID {resource.vmid} 將自動關機",
            (
                f"<p>您的資源（VMID {resource.vmid}）閒置寬限期已滿，"
                "系統即將自動關機。資料保留，可隨時重新開機。</p>"
            ),
        )
        logger.info("Idle grace elapsed: vmid=%s auto-stop scheduled", resource.vmid)
    elif action is IdleAction.clear:
        resource.idle_since = None
        resource.idle_notified_at = None
        session.add(resource)
        logger.info("Idle cleared: vmid=%s active again", resource.vmid)


def process_idle_detection() -> int:
    """Scheduler tick：閒置偵測（每 tick 至多掃 idle_scan_batch_size 台）。"""
    try:
        actions = 0
        now = _utc_now()
        with Session(engine) as session:
            config = governance_repo.get_governance_config(session=session)
            if not config.idle_detection_enabled:
                return 0

            pve_map = _pve_resource_map()
            running_vmids = [
                vmid
                for vmid, info in pve_map.items()
                if str(info.get("status") or "") == "running"
            ]
            candidates = resource_repo.list_idle_scan_candidates(
                session=session,
                vmids=running_vmids,
                checked_before=now - timedelta(minutes=IDLE_RESCAN_MINUTES),
                limit=config.idle_scan_batch_size,
            )

            for resource in candidates:
                pve_info = pve_map.get(resource.vmid)
                if pve_info is None:
                    continue
                try:
                    avg_cpu = _fetch_avg_cpu(
                        resource,
                        pve_info,
                        window_hours=config.idle_window_hours,
                        now=now,
                    )
                    action = decide_idle_action(
                        avg_cpu=avg_cpu,
                        idle_since=resource.idle_since,
                        now=now,
                        threshold_percent=config.idle_cpu_threshold_percent,
                        grace_hours=config.idle_grace_hours,
                    )
                    if action is not IdleAction.none:
                        _apply_idle_action(session, resource, action, now)
                        actions += 1
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Idle detection failed for vmid=%s", resource.vmid
                    )
                finally:
                    # 無論成敗都推進游標，避免故障資源卡死輪替。
                    resource.idle_checked_at = now
                    session.add(resource)
                    session.commit()
        return actions
    except Exception:
        logger.exception("process_idle_detection failed")
        return 0
