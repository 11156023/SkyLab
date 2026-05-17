import csv
import io
import uuid
from datetime import datetime

from sqlmodel import Session, col, select

from app.core.request_context import get_request_context
from app.models import AuditAction, AuditLog, User
from app.repositories import audit_log as audit_repo
from app.schemas import (
    AuditActionMeta,
    AuditLogPublic,
    AuditLogsPublic,
    AuditLogStats,
    AuditUserOption,
)

# Categorisation used by the admin UI to group actions in dropdowns and badges.
ACTION_CATEGORY: dict[AuditAction, str] = {
    # 認證
    AuditAction.login_success: "auth",
    AuditAction.login_failed: "auth",
    AuditAction.login_google_success: "auth",
    AuditAction.login_google_failed: "auth",
    AuditAction.password_change: "auth",
    AuditAction.password_recovery_request: "auth",
    AuditAction.password_reset: "auth",
    # 資源
    AuditAction.vm_create: "resource",
    AuditAction.lxc_create: "resource",
    AuditAction.resource_start: "resource",
    AuditAction.resource_stop: "resource",
    AuditAction.resource_reboot: "resource",
    AuditAction.resource_shutdown: "resource",
    AuditAction.resource_reset: "resource",
    AuditAction.resource_delete: "resource",
    AuditAction.snapshot_create: "resource",
    AuditAction.snapshot_delete: "resource",
    AuditAction.snapshot_rollback: "resource",
    AuditAction.spec_change_request: "resource",
    AuditAction.spec_change_apply: "resource",
    AuditAction.spec_direct_update: "resource",
    AuditAction.config_update: "resource",
    AuditAction.script_deploy: "resource",
    # 申請
    AuditAction.vm_request_submit: "request",
    AuditAction.vm_request_submit_auto_approved: "request",
    AuditAction.vm_request_review: "request",
    AuditAction.ai_api_request_submit: "request",
    AuditAction.ai_api_request_review: "request",
    # 使用者 / 群組
    AuditAction.user_create: "user",
    AuditAction.user_update: "user",
    AuditAction.user_delete: "user",
    AuditAction.group_create: "user",
    AuditAction.group_delete: "user",
    AuditAction.group_member_add: "user",
    AuditAction.group_member_remove: "user",
    AuditAction.batch_provision_vm: "user",
    AuditAction.batch_provision_lxc: "user",
    # 防火牆
    AuditAction.firewall_layout_update: "firewall",
    AuditAction.firewall_connection_create: "firewall",
    AuditAction.firewall_connection_delete: "firewall",
    AuditAction.firewall_rule_create: "firewall",
    AuditAction.firewall_rule_update: "firewall",
    AuditAction.firewall_rule_delete: "firewall",
    AuditAction.nat_rule_delete: "firewall",
    AuditAction.nat_rule_sync: "firewall",
    AuditAction.reverse_proxy_rule_delete: "firewall",
    AuditAction.reverse_proxy_rule_sync: "firewall",
    # Gateway
    AuditAction.gateway_config_update: "gateway",
    AuditAction.gateway_keypair_generate: "gateway",
    AuditAction.gateway_config_write: "gateway",
    AuditAction.gateway_service_control: "gateway",
    # Cloudflare
    AuditAction.cloudflare_config_update: "system",
    AuditAction.cloudflare_zone_create: "system",
    AuditAction.cloudflare_dns_record_create: "system",
    AuditAction.cloudflare_dns_record_update: "system",
    AuditAction.cloudflare_dns_record_delete: "system",
    # Proxmox / Migration
    AuditAction.proxmox_config_update: "system",
    AuditAction.proxmox_node_update: "system",
    AuditAction.proxmox_storage_update: "system",
    AuditAction.proxmox_sync_nodes: "system",
    AuditAction.proxmox_sync_now: "system",
    AuditAction.migration_job_retry: "system",
    AuditAction.migration_job_cancel: "system",
    # AI API credential
    AuditAction.ai_api_credential_rotate: "ai",
    AuditAction.ai_api_credential_delete: "ai",
    AuditAction.ai_api_credential_update: "ai",
}


DANGER_ACTIONS: set[AuditAction] = {
    AuditAction.resource_delete,
    AuditAction.resource_reset,
    AuditAction.snapshot_delete,
    AuditAction.snapshot_rollback,
    AuditAction.user_delete,
    AuditAction.group_delete,
    AuditAction.firewall_rule_delete,
    AuditAction.firewall_connection_delete,
    AuditAction.nat_rule_delete,
    AuditAction.reverse_proxy_rule_delete,
    AuditAction.proxmox_config_update,
    AuditAction.cloudflare_config_update,
    AuditAction.cloudflare_dns_record_delete,
    AuditAction.gateway_keypair_generate,
    AuditAction.login_failed,
    AuditAction.login_google_failed,
}


def log_action(
    *,
    session: Session,
    user_id: uuid.UUID | None,
    vmid: int | None = None,
    action: AuditAction | str,
    details: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> AuditLog:
    """Record an audit log entry.

    If ``ip_address`` / ``user_agent`` are not explicitly provided, they are
    pulled from the per-request ContextVar populated by RequestContextMiddleware.
    Background jobs (no request) simply get None values.
    """
    if ip_address is None or user_agent is None:
        ctx = get_request_context()
        if ip_address is None:
            ip_address = ctx.ip_address
        if user_agent is None:
            user_agent = ctx.user_agent

    return audit_repo.create_audit_log(
        session=session,
        user_id=user_id,
        vmid=vmid,
        action=action,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        commit=commit,
    )


def get_all(
    *,
    session: Session,
    skip: int = 0,
    limit: int = 100,
    vmid: int | None = None,
    user_id: uuid.UUID | None = None,
    action: AuditAction | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
) -> AuditLogsPublic:
    logs, count = audit_repo.get_audit_logs(
        session=session,
        skip=skip,
        limit=limit,
        vmid=vmid,
        user_id=user_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )
    return AuditLogsPublic(data=[_to_public(log) for log in logs], count=count)


def get_stats(
    *,
    session: Session,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> AuditLogStats:
    raw = audit_repo.get_audit_stats(
        session=session, start_time=start_time, end_time=end_time
    )
    return AuditLogStats(**raw)


def list_action_metas() -> list[AuditActionMeta]:
    """Return all known audit actions with their UI category."""
    return [
        AuditActionMeta(value=a.value, category=ACTION_CATEGORY.get(a, "other"))
        for a in AuditAction
    ]


def list_audit_users(*, session: Session) -> list[AuditUserOption]:
    """List users that ever appeared as audit log actors (for filter dropdown)."""
    stmt = (
        select(User)
        .where(
            col(User.id).in_(
                select(AuditLog.user_id).where(col(AuditLog.user_id).is_not(None))
            )
        )
        .order_by(User.email)
    )
    return [
        AuditUserOption(id=u.id, email=u.email, full_name=u.full_name)
        for u in session.exec(stmt).all()
    ]


def export_csv(
    *,
    session: Session,
    vmid: int | None = None,
    user_id: uuid.UUID | None = None,
    action: AuditAction | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
) -> str:
    logs = audit_repo.iter_audit_logs_for_export(
        session=session,
        vmid=vmid,
        user_id=user_id,
        action=action,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id",
        "created_at",
        "action",
        "user_email",
        "user_full_name",
        "vmid",
        "ip_address",
        "user_agent",
        "details",
    ])
    for log in logs:
        writer.writerow([
            str(log.id),
            log.created_at.isoformat(),
            log.action.value if hasattr(log.action, "value") else str(log.action),
            log.user.email if log.user else "",
            log.user.full_name if log.user else "",
            log.vmid or "",
            log.ip_address or "",
            log.user_agent or "",
            log.details,
        ])
    return buf.getvalue()


def get_by_user(
    *, session: Session, user_id: uuid.UUID, skip: int = 0, limit: int = 100
) -> AuditLogsPublic:
    logs, count = audit_repo.get_audit_logs_by_user(
        session=session, user_id=user_id, skip=skip, limit=limit
    )
    return AuditLogsPublic(data=[_to_public(log) for log in logs], count=count)


def get_by_vmid(
    *, session: Session, vmid: int, skip: int = 0, limit: int = 100
) -> AuditLogsPublic:
    logs, count = audit_repo.get_audit_logs_by_vmid(
        session=session, vmid=vmid, skip=skip, limit=limit
    )
    return AuditLogsPublic(data=[_to_public(log) for log in logs], count=count)


def _to_public(log: AuditLog) -> AuditLogPublic:
    return AuditLogPublic(
        id=log.id,
        user_id=log.user_id,
        user_email=log.user.email if log.user else None,
        user_full_name=log.user.full_name if log.user else None,
        vmid=log.vmid,
        resource_vmid=log.resource_vmid,
        action=log.action,
        details=log.details,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        created_at=log.created_at,
    )
