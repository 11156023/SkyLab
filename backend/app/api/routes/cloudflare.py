"""Cloudflare-backed domain management API (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.models import AuditAction
from app.schemas import Message
from app.schemas.cloudflare import (
    CloudflareConfigPublic,
    CloudflareConfigUpdate,
    CloudflareConnectionTestResult,
    CloudflareDNSRecordCreate,
    CloudflareDNSRecordPublic,
    CloudflareDNSRecordsPublic,
    CloudflareDNSRecordUpdate,
    CloudflareZoneCreate,
    CloudflareZonePublic,
    CloudflareZonesPublic,
)
from app.services.network import cloudflare_service
from app.services.user import audit_service

router = APIRouter(prefix="/cloudflare", tags=["cloudflare"])


@router.get("/config", response_model=CloudflareConfigPublic)
def get_config(session: SessionDep, _: AdminUser) -> CloudflareConfigPublic:
    return cloudflare_service.get_public_config(session)


@router.put("/config", response_model=CloudflareConfigPublic)
def update_config(
    data: CloudflareConfigUpdate,
    session: SessionDep,
    current_user: AdminUser,
) -> CloudflareConfigPublic:
    result = cloudflare_service.update_config(session=session, data=data)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.cloudflare_config_update,
        details=(
            "Updated Cloudflare config "
            f"(account_id={result.account_id or 'unset'}, token_rotated={'yes' if data.api_token else 'no'})"
        ),
    )
    return result


@router.post("/config/test", response_model=CloudflareConnectionTestResult)
def test_config(session: SessionDep, _: AdminUser) -> CloudflareConnectionTestResult:
    return cloudflare_service.test_connection(session)


@router.get("/zones", response_model=CloudflareZonesPublic)
def list_zones(
    session: SessionDep,
    _: AdminUser,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=100),
    search: str | None = None,
    status: str | None = None,
) -> CloudflareZonesPublic:
    return cloudflare_service.list_zones(
        session=session,
        page=page,
        per_page=per_page,
        search=search,
        status=status,
    )


@router.post("/zones", response_model=CloudflareZonePublic)
def create_zone(
    data: CloudflareZoneCreate,
    session: SessionDep,
    current_user: AdminUser,
) -> CloudflareZonePublic:
    zone = cloudflare_service.create_zone(session=session, data=data)
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.cloudflare_zone_create,
        details=f"Created Cloudflare zone {zone.name}",
    )
    return zone


@router.get("/zones/{zone_id}", response_model=CloudflareZonePublic)
def get_zone(
    zone_id: str,
    session: SessionDep,
    _: AdminUser,
) -> CloudflareZonePublic:
    return cloudflare_service.get_zone(session=session, zone_id=zone_id)


@router.get("/zones/{zone_id}/dns-records", response_model=CloudflareDNSRecordsPublic)
def list_dns_records(
    zone_id: str,
    session: SessionDep,
    _: AdminUser,
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
    search: str | None = None,
    record_type: str | None = Query(default=None, alias="type"),
    proxied: bool | None = None,
) -> CloudflareDNSRecordsPublic:
    result = cloudflare_service.list_dns_records(
        session=session,
        zone_id=zone_id,
        page=page,
        per_page=per_page,
        search=search,
        record_type=record_type,
        proxied=proxied,
    )
    # 標出哪些紀錄是本系統的對外網址建的，管理員才分得出來哪些是外部自己加的
    from app.services.network import reverse_proxy_service  # noqa: PLC0415

    reverse_proxy_service.annotate_dns_records_with_system_rules(session, result.items)
    return result


@router.post("/zones/{zone_id}/dns-records", response_model=CloudflareDNSRecordPublic)
def create_dns_record(
    zone_id: str,
    data: CloudflareDNSRecordCreate,
    session: SessionDep,
    current_user: AdminUser,
) -> CloudflareDNSRecordPublic:
    record = cloudflare_service.create_dns_record(
        session=session,
        zone_id=zone_id,
        data=data,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.cloudflare_dns_record_create,
        details=f"Created DNS record {record.type} {record.name} in zone {zone_id}",
    )
    return record


@router.patch("/zones/{zone_id}/dns-records/{record_id}", response_model=CloudflareDNSRecordPublic)
def update_dns_record(
    zone_id: str,
    record_id: str,
    data: CloudflareDNSRecordUpdate,
    session: SessionDep,
    current_user: AdminUser,
) -> CloudflareDNSRecordPublic:
    record = cloudflare_service.update_dns_record(
        session=session,
        zone_id=zone_id,
        record_id=record_id,
        data=data,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.cloudflare_dns_record_update,
        details=f"Updated DNS record {record.type} {record.name} in zone {zone_id}",
    )
    return record


@router.delete("/zones/{zone_id}/dns-records/{record_id}", response_model=Message)
def delete_dns_record(
    zone_id: str,
    record_id: str,
    session: SessionDep,
    current_user: AdminUser,
) -> Message:
    cloudflare_service.delete_dns_record(
        session=session,
        zone_id=zone_id,
        record_id=record_id,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.cloudflare_dns_record_delete,
        details=f"Deleted DNS record {record_id} in zone {zone_id}",
    )
    return Message(message="Cloudflare DNS record 已刪除")
