"""Business logic for Cloudflare-backed domain management."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any, cast

from sqlmodel import Session

from app.exceptions import BadRequestError, NotFoundError, UpstreamServiceError
from app.infrastructure.cloudflare import CloudflareAPIClient
from app.models.cloudflare_config import CloudflareConfig
from app.repositories import cloudflare_config as config_repo
from app.schemas.cloudflare import (
    CloudflareConfigPublic,
    CloudflareConfigUpdate,
    CloudflareConnectionTestResult,
    CloudflareDNSRecordCreate,
    CloudflareDNSRecordPublic,
    CloudflareDNSRecordsPublic,
    CloudflareDNSRecordUpdate,
    CloudflarePageInfoPublic,
    CloudflareZoneCreate,
    CloudflareZonePublic,
    CloudflareZonesPublic,
)

_DEFAULT_PAGE = 1
_DEFAULT_PER_PAGE = 50
_PROXIABLE_RECORD_TYPES = {"A", "AAAA", "CNAME", "HTTPS", "SVCB"}
_PRIORITY_RECORD_TYPES = {"MX", "SRV", "URI"}
_DEFAULT_REVERSE_PROXY_TARGET_TYPES = {"A", "CNAME"}
_HOSTNAME_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


def _to_public_config(config: CloudflareConfig | None) -> CloudflareConfigPublic:
    if config is None:
        return CloudflareConfigPublic(
            account_id=None,
            is_configured=False,
            has_api_token=False,
            has_default_dns_target=False,
            default_dns_target_type=None,
            default_dns_target_value=None,
            updated_at=None,
            last_verified_at=None,
        )

    has_api_token = bool(config.encrypted_api_token)
    default_dns_target_type = _normalize_record_type(config.default_dns_target_type)
    default_dns_target_value = _normalize_optional_text(config.default_dns_target_value)
    return CloudflareConfigPublic(
        account_id=config.account_id or None,
        is_configured=has_api_token,
        has_api_token=has_api_token,
        has_default_dns_target=bool(default_dns_target_type and default_dns_target_value),
        default_dns_target_type=default_dns_target_type,
        default_dns_target_value=default_dns_target_value,
        updated_at=config.updated_at,
        last_verified_at=config.last_verified_at,
    )


def get_public_config(session: Session) -> CloudflareConfigPublic:
    return _to_public_config(config_repo.get_cloudflare_config(session))


def update_config(
    *,
    session: Session,
    data: CloudflareConfigUpdate,
) -> CloudflareConfigPublic:
    existing = config_repo.get_cloudflare_config(session)
    api_token = _resolve_api_token(existing=existing, submitted_api_token=data.api_token)
    default_dns_target_type, default_dns_target_value = _resolve_default_dns_target(
        existing=existing,
        data=data,
    )
    config = config_repo.upsert_cloudflare_config(
        session,
        account_id=_normalize_optional_text(data.account_id),
        api_token=api_token,
        default_dns_target_type=default_dns_target_type,
        default_dns_target_value=default_dns_target_value,
    )
    return _to_public_config(config)


def test_connection(session: Session) -> CloudflareConnectionTestResult:
    client, config = _build_client_from_session(session)
    verification = client.verify_token()
    token_status = str(verification.get("status") or "unknown")
    config_repo.mark_cloudflare_config_verified(session, config)
    return CloudflareConnectionTestResult(
        success=True,
        message=f"Cloudflare API Token 驗證成功（狀態：{token_status}）",
        token_status=token_status,
    )


def list_zones(
    *,
    session: Session,
    page: int = _DEFAULT_PAGE,
    per_page: int = _DEFAULT_PER_PAGE,
    search: str | None = None,
    status: str | None = None,
) -> CloudflareZonesPublic:
    client, _ = _build_client_from_session(session)
    payload = client.list_zones(
        page=page,
        per_page=per_page,
        search=_normalize_optional_text(search),
        status=_normalize_optional_text(status),
    )
    return CloudflareZonesPublic(
        items=[_to_zone_public(item) for item in _extract_result_list(payload)],
        page_info=_to_page_info(payload),
    )


def get_zone(*, session: Session, zone_id: str) -> CloudflareZonePublic:
    client, _ = _build_client_from_session(session)
    return _to_zone_public(client.get_zone(_require_identifier(zone_id, "zone_id")))


def create_zone(
    *,
    session: Session,
    data: CloudflareZoneCreate,
) -> CloudflareZonePublic:
    client, config = _build_client_from_session(session)
    account_id = _normalize_optional_text(data.account_id) or _normalize_optional_text(
        config.account_id
    )
    if not account_id:
        raise BadRequestError("建立 Zone 前請先提供 Cloudflare account_id")

    zone = client.create_zone(
        name=data.name.strip(),
        account_id=account_id,
        jump_start=data.jump_start,
    )
    return _to_zone_public(zone)


def list_dns_records(
    *,
    session: Session,
    zone_id: str,
    page: int = _DEFAULT_PAGE,
    per_page: int = _DEFAULT_PER_PAGE,
    search: str | None = None,
    record_type: str | None = None,
    proxied: bool | None = None,
) -> CloudflareDNSRecordsPublic:
    clean_zone_id = _require_identifier(zone_id, "zone_id")
    client, _ = _build_client_from_session(session)
    payload = client.list_dns_records(
        zone_id=clean_zone_id,
        page=page,
        per_page=per_page,
        search=_normalize_optional_text(search),
        record_type=_normalize_record_type(record_type),
        proxied=proxied,
    )
    return CloudflareDNSRecordsPublic(
        items=[_to_dns_record_public(clean_zone_id, item) for item in _extract_result_list(payload)],
        page_info=_to_page_info(payload),
    )


def create_dns_record(
    *,
    session: Session,
    zone_id: str,
    data: CloudflareDNSRecordCreate,
) -> CloudflareDNSRecordPublic:
    clean_zone_id = _require_identifier(zone_id, "zone_id")
    client, _ = _build_client_from_session(session)
    record = client.create_dns_record(
        zone_id=clean_zone_id,
        record=_build_record_payload(data),
    )
    return _to_dns_record_public(clean_zone_id, record)


def update_dns_record(
    *,
    session: Session,
    zone_id: str,
    record_id: str,
    data: CloudflareDNSRecordUpdate,
) -> CloudflareDNSRecordPublic:
    clean_zone_id = _require_identifier(zone_id, "zone_id")
    clean_record_id = _require_identifier(record_id, "record_id")
    client, _ = _build_client_from_session(session)
    record = client.update_dns_record(
        zone_id=clean_zone_id,
        record_id=clean_record_id,
        record=_build_record_payload(data),
    )
    return _to_dns_record_public(clean_zone_id, record)


def delete_dns_record(*, session: Session, zone_id: str, record_id: str) -> None:
    client, _ = _build_client_from_session(session)
    client.delete_dns_record(
        zone_id=_require_identifier(zone_id, "zone_id"),
        record_id=_require_identifier(record_id, "record_id"),
    )


def upsert_reverse_proxy_dns_record(
    *,
    session: Session,
    zone_id: str,
    domain: str,
    vmid: int,
    existing_zone_id: str | None = None,
    existing_record_id: str | None = None,
) -> CloudflareDNSRecordPublic:
    clean_zone_id = _require_identifier(zone_id, "zone_id")
    clean_domain = _require_identifier(domain, "domain").lower()
    client, config = _build_client_from_session(session)
    target_type, target_value = _get_default_dns_target(config)
    record_payload = CloudflareDNSRecordCreate(
        type=target_type,
        name=clean_domain,
        content=target_value,
        ttl=1,
        proxied=True,
        comment=f"SkyLab reverse proxy vmid={vmid}",
    )
    payload = _build_record_payload(record_payload)

    if existing_record_id and existing_zone_id == clean_zone_id:
        try:
            record = client.update_dns_record(
                zone_id=clean_zone_id,
                record_id=_require_identifier(existing_record_id, "record_id"),
                record=payload,
            )
            return _to_dns_record_public(clean_zone_id, record)
        except NotFoundError:
            # Record already deleted or does not exist
            pass

    if existing_record_id and existing_zone_id and existing_zone_id != clean_zone_id:
        try:
            client.delete_dns_record(
                zone_id=_require_identifier(existing_zone_id, "existing_zone_id"),
                record_id=_require_identifier(existing_record_id, "existing_record_id"),
            )
        except NotFoundError:
            # Record already deleted or does not exist
            pass

    existing_records = list_dns_records(
        session=session,
        zone_id=clean_zone_id,
        page=1,
        per_page=200,
        search=clean_domain,
        record_type=target_type,
        proxied=None,
    ).items
    matched_record = next(
        (
            record
            for record in existing_records
            if record.name.lower() == clean_domain and record.type == target_type
        ),
        None,
    )

    if matched_record is not None:
        record = client.update_dns_record(
            zone_id=clean_zone_id,
            record_id=matched_record.id,
            record=payload,
        )
        return _to_dns_record_public(clean_zone_id, record)

    record = client.create_dns_record(zone_id=clean_zone_id, record=payload)
    return _to_dns_record_public(clean_zone_id, record)


def delete_reverse_proxy_dns_record(
    *,
    session: Session,
    zone_id: str,
    record_id: str,
) -> None:
    try:
        delete_dns_record(session=session, zone_id=zone_id, record_id=record_id)
    except NotFoundError:
        return


def _resolve_api_token(
    *,
    existing: CloudflareConfig | None,
    submitted_api_token: str | None,
) -> str:
    token = _normalize_optional_text(submitted_api_token)
    if token:
        return token
    if existing is not None and existing.encrypted_api_token:
        return config_repo.get_decrypted_api_token(existing)
    raise BadRequestError("初次設定必須提供 Cloudflare API Token")


def _resolve_default_dns_target(
    *,
    existing: CloudflareConfig | None,
    data: CloudflareConfigUpdate,
) -> tuple[str | None, str | None]:
    fields_set = set(getattr(data, "model_fields_set", set()))
    target_fields_provided = bool(
        {"default_dns_target_type", "default_dns_target_value"} & fields_set
    )
    if not target_fields_provided:
        if existing is None:
            return None, None
        return (
            _normalize_record_type(existing.default_dns_target_type),
            _normalize_optional_text(existing.default_dns_target_value),
        )

    target_type = _normalize_record_type(data.default_dns_target_type)
    target_value = _normalize_optional_text(data.default_dns_target_value)
    if not target_type and not target_value:
        return None, None
    if not target_type or not target_value:
        raise BadRequestError("預設 DNS 指向必須同時提供類型與內容")

    if target_type not in _DEFAULT_REVERSE_PROXY_TARGET_TYPES:
        raise BadRequestError("預設 DNS 指向類型僅支援 A 或 CNAME")

    if target_type == "A":
        try:
            return target_type, str(ipaddress.IPv4Address(target_value))
        except ipaddress.AddressValueError as exc:
            raise BadRequestError("預設 DNS 指向 A record 必須是有效 IPv4 位址") from exc

    normalized_target_value = target_value.rstrip(".").lower()
    if not _is_valid_hostname(normalized_target_value):
        raise BadRequestError("預設 DNS 指向 CNAME 必須是有效網域")
    return target_type, normalized_target_value


def _build_client_from_session(session: Session) -> tuple[CloudflareAPIClient, CloudflareConfig]:
    config = config_repo.get_cloudflare_config(session)
    if config is None or not config.encrypted_api_token:
        raise BadRequestError("請先完成 Cloudflare 供應商設定")
    return CloudflareAPIClient(api_token=config_repo.get_decrypted_api_token(config)), config


def _get_default_dns_target(config: CloudflareConfig) -> tuple[str, str]:
    target_type = _normalize_record_type(config.default_dns_target_type)
    target_value = _normalize_optional_text(config.default_dns_target_value)
    if not target_type or not target_value:
        raise BadRequestError("請先在 admin/domains 設定預設 DNS 指向")
    return target_type, target_value


def _extract_result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, list):
        raise UpstreamServiceError("Cloudflare API 回傳列表格式不正確")
    return [cast(dict[str, Any], item) for item in result if isinstance(item, dict)]


def _to_page_info(payload: dict[str, Any]) -> CloudflarePageInfoPublic:
    result = payload.get("result")
    result_info = payload.get("result_info")
    if isinstance(result_info, dict):
        return CloudflarePageInfoPublic(
            page=int(result_info.get("page") or _DEFAULT_PAGE),
            per_page=int(result_info.get("per_page") or _DEFAULT_PER_PAGE),
            count=int(result_info.get("count") or (len(result) if isinstance(result, list) else 0)),
            total_count=int(result_info.get("total_count") or 0),
            total_pages=int(result_info.get("total_pages") or 1),
        )

    result_count = len(result) if isinstance(result, list) else 0
    return CloudflarePageInfoPublic(
        page=_DEFAULT_PAGE,
        per_page=result_count or _DEFAULT_PER_PAGE,
        count=result_count,
        total_count=result_count,
        total_pages=1,
    )


def _to_zone_public(item: dict[str, Any]) -> CloudflareZonePublic:
    return CloudflareZonePublic(
        id=str(item.get("id") or ""),
        name=str(item.get("name") or ""),
        status=str(item.get("status") or "unknown"),
        paused=bool(item.get("paused") or False),
        type=_string_or_none(item.get("type")),
        development_mode=_int_or_none(item.get("development_mode")),
        name_servers=_string_list(item.get("name_servers")),
        original_name_servers=_string_list(item.get("original_name_servers")),
        created_on=_datetime_or_none(item.get("created_on")),
        modified_on=_datetime_or_none(item.get("modified_on")),
        activated_on=_datetime_or_none(item.get("activated_on")),
    )


def _to_dns_record_public(
    zone_id: str,
    item: dict[str, Any],
) -> CloudflareDNSRecordPublic:
    return CloudflareDNSRecordPublic(
        id=str(item.get("id") or ""),
        zone_id=_string_or_none(item.get("zone_id")) or zone_id,
        type=str(item.get("type") or ""),
        name=str(item.get("name") or ""),
        content=str(item.get("content") or ""),
        ttl=int(item.get("ttl") or 1),
        proxied=_bool_or_none(item.get("proxied")),
        proxiable=_bool_or_none(item.get("proxiable")),
        comment=_string_or_none(item.get("comment")),
        priority=_int_or_none(item.get("priority")),
        tags=_string_list(item.get("tags")),
        created_on=_datetime_or_none(item.get("created_on")),
        modified_on=_datetime_or_none(item.get("modified_on")),
    )


def _build_record_payload(
    data: CloudflareDNSRecordCreate | CloudflareDNSRecordUpdate,
) -> dict[str, object]:
    record_type = _normalize_record_type(data.type)
    if not record_type:
        raise BadRequestError("DNS record type 不可為空")

    name = data.name.strip()
    content = data.content.strip()
    if not name:
        raise BadRequestError("DNS record name 不可為空")
    if not content:
        raise BadRequestError("DNS record content 不可為空")

    payload: dict[str, object] = {
        "type": record_type,
        "name": name,
        "content": content,
        "ttl": data.ttl,
    }

    comment = _normalize_optional_text(data.comment)
    if comment:
        payload["comment"] = comment

    if record_type in _PROXIABLE_RECORD_TYPES and data.proxied is not None:
        payload["proxied"] = data.proxied

    if record_type in _PRIORITY_RECORD_TYPES and data.priority is not None:
        payload["priority"] = data.priority

    return payload


def _normalize_record_type(record_type: str | None) -> str | None:
    value = _normalize_optional_text(record_type)
    return value.upper() if value else None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _is_valid_hostname(value: str) -> bool:
    if len(value) > 255 or "." not in value:
        return False
    labels = value.split(".")
    return all(_HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels)


def _require_identifier(value: str, field_name: str) -> str:
    identifier = value.strip()
    if not identifier:
        raise BadRequestError(f"{field_name} 不可為空")
    return identifier


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _bool_or_none(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _datetime_or_none(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


__all__ = [
    "get_public_config",
    "update_config",
    "test_connection",
    "list_zones",
    "get_zone",
    "create_zone",
    "list_dns_records",
    "create_dns_record",
    "update_dns_record",
    "delete_dns_record",
    "upsert_reverse_proxy_dns_record",
    "delete_reverse_proxy_dns_record",
    "_build_record_payload",
]
