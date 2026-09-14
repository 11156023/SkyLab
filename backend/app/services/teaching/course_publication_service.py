"""把課程環境的「外網 → 機器」宣告，逐位學生實體化成對外服務。

課程模板是一份規格、每位學生各拿一份，但網域是全域唯一的資源——模板上
不可能填一個所有人共用的網址。所以老師只宣告主機名樣板（含 ``{student}``），
這裡負責把它組成每位學生自己的網域，再交給統一的發布路徑
（``firewall_service.publish_vm_service``）建立 Traefik、DNS 與入站規則。
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid

from sqlmodel import Session, col, select

from app.models import CourseEnvironmentPublication, User
from app.schemas.firewall import PublishedServiceCreate
from app.services.network import (
    cloudflare_service,
    firewall_service,
    reverse_proxy_service,
)

logger = logging.getLogger(__name__)

STUDENT_PLACEHOLDER = "{student}"
CLASS_PLACEHOLDER = "{class}"
_MAX_TOKEN_LENGTH = 20
_UNSAFE = re.compile(r"[^a-z0-9]+")


def list_for_version(
    session: Session, *, version_id: uuid.UUID
) -> list[CourseEnvironmentPublication]:
    return list(
        session.exec(
            select(CourseEnvironmentPublication)
            .where(CourseEnvironmentPublication.version_id == version_id)
            .order_by(col(CourseEnvironmentPublication.sort_order))
        ).all()
    )


def student_token(user: User, *, scope: str = "") -> str:
    """Return a class-scoped pseudonym without exposing account identifiers."""
    source = f"{uuid.UUID(str(user.id))}:{scope}".encode()
    return f"s{hashlib.sha256(source).hexdigest()[:10]}"


def context_token(value: str | uuid.UUID, *, fallback: str = "class") -> str:
    token = _UNSAFE.sub("-", str(value).lower()).strip("-")
    return token[:_MAX_TOKEN_LENGTH].strip("-") or fallback


def _hostname_for(
    publication: CourseEnvironmentPublication,
    student: str,
    class_scope: str,
) -> str:
    template = publication.hostname_prefix or STUDENT_PLACEHOLDER
    return (
        template.replace(STUDENT_PLACEHOLDER, student)
        .replace(CLASS_PLACEHOLDER, class_scope)
        .strip(".")
        .lower()
    )


def resolve_domain(
    session: Session,
    *,
    publication: CourseEnvironmentPublication,
    user: User,
    vmid: int,
    scope: str = "class",
) -> str:
    """組出這位學生的完整網域；撞名時補上使用者短碼再試一次。"""
    zone = cloudflare_service.get_zone(
        session=session, zone_id=str(publication.zone_id or "")
    )
    class_scope = context_token(scope)
    token = student_token(user, scope=class_scope)
    domain = reverse_proxy_service.build_full_domain(
        zone_name=zone.name,
        hostname_prefix=_hostname_for(publication, token, class_scope),
    )
    availability = reverse_proxy_service.check_domain_availability(
        session, domain, zone_id=publication.zone_id
    )
    if availability.available:
        return domain

    # 兩位學生的帳號清完可能撞在一起（alice.wang 與 alice_wang），補短碼區分
    suffix = uuid.UUID(str(user.id)).hex[:4]
    logger.info(
        "Domain %s unavailable for vmid %s (%s); retrying with suffix",
        domain,
        vmid,
        availability.reason,
    )
    return reverse_proxy_service.build_full_domain(
        zone_name=zone.name,
        hostname_prefix=_hostname_for(
            publication, f"{token}-{suffix}", class_scope
        ),
    )


def apply_for_machines(
    session: Session,
    *,
    version_id: uuid.UUID,
    vmid_by_key: dict[str, int],
    owner: User,
    scope: str = "class",
) -> list[str]:
    """把這個版本的宣告套用到一位學生的機器上；已發布的略過（可重複執行）。"""
    publications = list_for_version(session, version_id=version_id)
    if not publications:
        return []

    errors: list[str] = []
    for publication in publications:
        vmid = vmid_by_key.get(publication.node_key)
        if vmid is None:
            continue
        try:
            published = {
                (service.port, service.protocol)
                for service in firewall_service.list_vm_published_services(
                    vmid, session
                )
            }
        except Exception:
            logger.exception("Unable to read published services for vmid %s", vmid)
            errors.append(f"{vmid}: published services unreadable")
            continue
        if (publication.port, publication.protocol) in published:
            continue

        try:
            if publication.mode == "domain":
                create = PublishedServiceCreate(
                    port=publication.port,
                    protocol="tcp",
                    mode="domain",
                    domain=resolve_domain(
                        session,
                        publication=publication,
                        user=owner,
                        vmid=vmid,
                        scope=scope,
                    ),
                    enable_https=publication.enable_https,
                )
            else:
                create = PublishedServiceCreate(
                    port=publication.port,
                    protocol=publication.protocol,
                    mode="firewall_only",
                )
            firewall_service.publish_vm_service(vmid, create, session)
        except Exception:
            logger.exception(
                "Failed to publish %s:%s for vmid %s",
                publication.node_key,
                publication.port,
                vmid,
            )
            errors.append(f"{vmid}: publishing port {publication.port} failed")
    return errors


def public_urls_by_vmid(session: Session, vmids: list[int]) -> dict[int, str]:
    """每台機器的對外網址（沒有就不會出現在結果裡）。

    直接讀反向代理紀錄，不打 Proxmox——這是清單頁會用到的路徑。
    """
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    urls: dict[int, str] = {}
    for vmid in {int(vmid) for vmid in vmids if vmid is not None}:
        for rule in rp_repo.list_rules_by_vmid(session, vmid):
            scheme = "https" if rule.enable_https else "http"
            urls.setdefault(vmid, f"{scheme}://{rule.domain}")
    return urls
