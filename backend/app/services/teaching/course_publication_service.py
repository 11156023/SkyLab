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
from app.schemas.firewall import PublishedServiceCreate, PublishedServiceRef
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


def _course_machine_sets(session: Session):
    """進行中的班級與練習，每位學生一組：(version_id, {node_key: vmid})。

    只掃課程管的機器：個人機器的「僅開放防火牆」仍是拓撲頁的正式功能，不碰。
    """
    from app.models import (  # noqa: PLC0415  避免與 quick_practice 的循環匯入
        QuickPracticeSession,
        QuickPracticeSessionMachine,
        TeachingClass,
        TeachingClassMachineNode,
        TeachingClassStatus,
        TeachingClassStudent,
        TeachingClassStudentMachine,
        VMRequest,
    )

    live_classes = [
        TeachingClassStatus.provisioning,
        TeachingClassStatus.partial_failed,
        TeachingClassStatus.active,
    ]
    for teaching_class in session.exec(
        select(TeachingClass).where(col(TeachingClass.status).in_(live_classes))
    ).all():
        if not teaching_class.course_version_id:
            continue
        node_keys = {
            row.id: row.node_key
            for row in session.exec(
                select(TeachingClassMachineNode).where(
                    TeachingClassMachineNode.class_id == teaching_class.id
                )
            ).all()
        }
        for enrollment in session.exec(
            select(TeachingClassStudent).where(
                TeachingClassStudent.class_id == teaching_class.id
            )
        ).all():
            machines = session.exec(
                select(TeachingClassStudentMachine).where(
                    TeachingClassStudentMachine.class_student_id == enrollment.id
                )
            ).all()
            vmid_by_key = {
                node_keys[machine.machine_node_id]: machine.vmid
                for machine in machines
                if machine.vmid is not None and machine.machine_node_id in node_keys
            }
            if vmid_by_key:
                yield teaching_class.course_version_id, vmid_by_key

    live_practice = ["creating", "partial_failed", "ready"]
    for practice in session.exec(
        select(QuickPracticeSession).where(col(QuickPracticeSession.status).in_(live_practice))
    ).all():
        rows = session.exec(
            select(QuickPracticeSessionMachine, VMRequest)
            .join(VMRequest, QuickPracticeSessionMachine.vm_request_id == VMRequest.id)
            .where(QuickPracticeSessionMachine.session_id == practice.id)
        ).all()
        vmid_by_key = {
            machine.node_key: request.vmid for machine, request in rows if request.vmid is not None
        }
        if vmid_by_key:
            yield practice.environment_version_id, vmid_by_key


def reconcile_legacy_open_ports(session: Session) -> dict[str, object]:
    """把課程機器上舊的「只開防火牆」入站規則換掉；可重複執行。

    firewall_only 已從課程環境移除（無 source 的 ACCEPT 等於對整個子網開洞），
    migration 只轉了宣告，已經套在學生機器上的規則得另外掃。規則本身沒有
    mode，「有 SkyLab 入站規則、DB 卻沒有對應的 NAT 或反向代理」就是它
    （``list_vm_published_services`` 會標成 firewall_only）。

    版本現在宣告 port_forward 的：撤下後立刻以 port_forward 重新發布，服務不
    中斷、只是改從 Gateway 進來；版本已不再宣告的：直接撤下。
    """
    stats: dict[str, object] = {"scanned": 0, "replaced": [], "removed": [], "errors": []}
    for version_id, vmid_by_key in _course_machine_sets(session):
        declared = {
            (item.node_key, item.port, item.protocol): item
            for item in list_for_version(session, version_id=version_id)
        }
        for node_key, vmid in vmid_by_key.items():
            stats["scanned"] += 1  # type: ignore[operator]
            try:
                services = firewall_service.list_vm_published_services(vmid, session)
            except Exception:
                logger.exception("Unable to read published services for vmid %s", vmid)
                stats["errors"].append(f"{vmid}: published services unreadable")  # type: ignore[union-attr]
                continue
            for service in services:
                if service.mode != "firewall_only":
                    continue
                ref = PublishedServiceRef(port=service.port, protocol=service.protocol)
                try:
                    firewall_service.unpublish_vm_service(vmid, ref, session)
                    publication = declared.get((node_key, service.port, service.protocol))
                    if publication is not None and publication.mode == "port_forward":
                        external_port = publish_forward(session, vmid=vmid, publication=publication)
                        stats["replaced"].append(  # type: ignore[union-attr]
                            {"vmid": vmid, "port": service.port, "protocol": service.protocol, "external_port": external_port}
                        )
                    else:
                        stats["removed"].append(  # type: ignore[union-attr]
                            {"vmid": vmid, "port": service.port, "protocol": service.protocol}
                        )
                except Exception:
                    logger.exception(
                        "Failed to reconcile legacy open port %s/%s on vmid %s",
                        service.port,
                        service.protocol,
                        vmid,
                    )
                    stats["errors"].append(  # type: ignore[union-attr]
                        f"{vmid}: port {service.port}/{service.protocol} reconcile failed"
                    )
    return stats


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
