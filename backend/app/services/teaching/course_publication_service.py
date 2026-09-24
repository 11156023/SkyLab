"""把課程環境的「外網 → 機器」宣告，逐位學生實體化成對外服務。

課程模板是一份規格、每位學生各拿一份，但網域與對外 port 都是全域唯一的
資源——模板上不可能填一個所有人共用的網址或 port。所以老師只宣告主機名
樣板（含 ``{student}``）或「要一個對外 port」，這裡負責逐人組網域、逐人從
配號池挑 port，再交給統一的發布路徑（``firewall_service.publish_vm_service``）
建立 Traefik / haproxy、DNS 與入站規則。
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid

from sqlmodel import Session, col, select

from app.exceptions import BadRequestError
from app.models import CourseEnvironmentPublication, User
from app.schemas.firewall import PublishedServiceCreate
from app.services.network import (
    cloudflare_service,
    firewall_service,
    ip_management_service,
    nat_service,
    reverse_proxy_service,
)

logger = logging.getLogger(__name__)
# 兩個班同時開課可能挑到同一個 port：唯一約束會擋下第二個，換號再試
_FORWARD_ATTEMPTS = 3

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


def publish_forward(
    session: Session, *, vmid: int, publication: CourseEnvironmentPublication
) -> int:
    """配一個對外 port 發布；撞號就排除它再挑一次。回傳配到的 port。"""
    tried: set[int] = set()
    last_error: BadRequestError | None = None
    for _ in range(_FORWARD_ATTEMPTS):
        external_port = nat_service.allocate_external_port(
            session, publication.protocol, exclude=frozenset(tried)
        )
        create = PublishedServiceCreate(
            port=publication.port,
            protocol=publication.protocol,
            mode="port_forward",
            external_port=external_port,
        )
        try:
            firewall_service.publish_vm_service(vmid, create, session)
        except BadRequestError as exc:
            tried.add(external_port)
            last_error = exc
            logger.info(
                "External port %s rejected while publishing vmid %s, retrying: %s",
                external_port,
                vmid,
                exc,
            )
            continue
        return external_port
    assert last_error is not None
    raise last_error


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
                firewall_service.publish_vm_service(vmid, create, session)
            else:
                publish_forward(session, vmid=vmid, publication=publication)
        except Exception:
            logger.exception(
                "Failed to publish %s:%s for vmid %s",
                publication.node_key,
                publication.port,
                vmid,
            )
            errors.append(f"{vmid}: publishing port {publication.port} failed")
    return errors


# ── 一次性維護：舊的「只開防火牆」規則 ────────────────────────────────────


def forward_endpoints_by_vmid(
    session: Session, vmids: list[int]
) -> dict[int, list[dict[str, object]]]:
    """每台機器配到的對外 port（沒有就不會出現）。

    host 是管理員設定的入口主機，沒設就是 None，前端只顯示 port。
    跟 ``public_urls_by_vmid`` 一樣只讀 DB，不打 Proxmox。
    """
    from app.repositories import nat_rule as nat_repo  # noqa: PLC0415

    wanted = sorted({int(vmid) for vmid in vmids if vmid is not None})
    if not wanted:
        return {}
    config = ip_management_service.get_subnet_config(session)
    host = (getattr(config, "forward_public_host", None) or "").strip() or None
    endpoints: dict[int, list[dict[str, object]]] = {}
    for rule in sorted(
        nat_repo.list_rules_by_vmids(session, wanted),
        key=lambda r: (r.vmid, r.internal_port, r.protocol),
    ):
        endpoints.setdefault(rule.vmid, []).append(
            {
                "host": host,
                "external_port": rule.external_port,
                "internal_port": rule.internal_port,
                "protocol": rule.protocol,
            }
        )
    return endpoints


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
