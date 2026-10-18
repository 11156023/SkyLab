"""把課程環境的「外網 → 機器」宣告，逐位學生實體化成對外服務。

課程模板是一份規格、每位學生各拿一份，但網域與對外 port 都是全域唯一的
資源——模板上不可能填一個所有人共用的網址或 port。所以老師只宣告網址前綴
或「要一個對外 port」，這裡負責逐人在前綴後面接一串亂碼組成網域、逐人從
配號池挑 port，再交給統一的發布路徑（``firewall_service.publish_vm_service``）
建立 nginx（反向代理／Port 轉發）、DNS 與入站規則。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlmodel import Session, col, select

from app.core.i18n import t
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
# 亂碼撞到既有網域（機率極低）或 Cloudflare 上剛好有同名紀錄時換一組再試
_DOMAIN_ATTEMPTS = 3

# 老師填的前綴後面接的亂碼長度：UUID 的前 16 個十六進位字元。一段網址最長 63 字，
# 連字號加亂碼佔 17 字，前綴上限 40 字（``EnvironmentPublicationIn``）留有餘裕
HOSTNAME_SUFFIX_LENGTH = 16


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


def hostname_suffix() -> str:
    """每位學生網址裡的亂碼片段：隨機 UUID 的前 16 個十六進位字元。"""
    return uuid.uuid4().hex[:HOSTNAME_SUFFIX_LENGTH]


def student_hostname(prefix: str, suffix: str) -> str:
    """``前綴-亂碼``；前綴是老師在模板上填的、已經過 schema 驗證。"""
    return f"{prefix.strip().lower()}-{suffix}"


def resolve_domain(
    session: Session,
    *,
    publication: CourseEnvironmentPublication,
    vmid: int,
) -> str:
    """替這台機器組出一個還沒被用的完整網域（前綴＋亂碼）；撞到就換一組亂碼。"""
    zone = cloudflare_service.get_zone(
        session=session, zone_id=str(publication.zone_id or "")
    )
    prefix = publication.hostname_prefix or ""
    if not prefix:
        raise BadRequestError(t("course_env.publication_prefix_invalid"))
    last_reason: str | None = None
    for _ in range(_DOMAIN_ATTEMPTS):
        domain = reverse_proxy_service.build_full_domain(
            zone_name=zone.name,
            hostname_prefix=student_hostname(prefix, hostname_suffix()),
        )
        availability = reverse_proxy_service.check_domain_availability(
            session, domain, zone_id=publication.zone_id
        )
        if availability.available:
            return domain
        last_reason = availability.reason
        logger.info(
            "Domain %s unavailable for vmid %s (%s); retrying with a new suffix",
            domain,
            vmid,
            availability.reason,
        )
    raise BadRequestError(
        t("course_env.publication_domain_unavailable", reason=last_reason or "")
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


def _published_meanwhile(
    session: Session, *, vmid: int, publication: CourseEnvironmentPublication
) -> bool:
    """發布失敗後再看一次：這個 port 是不是已經被別人發布好了。

    只有 session 卡在失敗交易裡（查詢本身報錯）才 rollback：呼叫端（班級
    拓撲）的 session 可能還有沒 commit 的變更，平白 rollback 會一起丟掉。
    """

    def _published() -> bool:
        return (publication.port, publication.protocol) in {
            (service.port, service.protocol)
            for service in firewall_service.list_vm_published_services(vmid, session)
        }

    try:
        try:
            return _published()
        except (PendingRollbackError, DBAPIError):
            session.rollback()
            return _published()
    except Exception:
        logger.debug("Re-checking publication for vmid %s failed", vmid, exc_info=True)
        return False


def apply_for_machines(
    session: Session,
    *,
    version_id: uuid.UUID,
    vmid_by_key: dict[str, int],
    owner: User,
) -> list[str]:
    """把這個版本的宣告套用到一位學生的機器上；已發布的略過（可重複執行）。

    ``owner`` 是機器的擁有者（學生）；網域裡的亂碼是隨機的，不再從帳號推導，
    保留參數只為了日誌與呼叫端的語意。
    """
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
                    domain=resolve_domain(session, publication=publication, vmid=vmid),
                    enable_https=publication.enable_https,
                )
                firewall_service.publish_vm_service(vmid, create, session)
            else:
                publish_forward(session, vmid=vmid, publication=publication)
        except Exception:
            if _published_meanwhile(session, vmid=vmid, publication=publication):
                # 同一組機器的兩個建機回呼同時發布：慢的那個撞上唯一約束或
                # 「此 port 已發布」，但服務其實已經由另一邊發布好了，不算失敗
                logger.info(
                    "Publication %s:%s for vmid %s was completed concurrently",
                    publication.node_key,
                    publication.port,
                    vmid,
                )
                continue
            logger.exception(
                "Failed to publish %s:%s for vmid %s (owner %s)",
                publication.node_key,
                publication.port,
                vmid,
                owner.id,
            )
            errors.append(f"{vmid}: publishing port {publication.port} failed")
    return errors


# ── 清單頁用的唯讀查詢：對外網址與 port 轉發 ──────────────────────────────


def forward_endpoints_by_vmid(
    session: Session, vmids: list[int]
) -> dict[int, list[dict[str, object]]]:
    """每台機器配到的對外 port（沒有就不會出現）。

    host 是管理員設定的入口主機，沒設就是 None，前端只顯示 port。
    跟 ``public_urls_by_vmid`` 一樣只讀 DB，不打 Proxmox。
    """
    from app.repositories import nat_rule as nat_repo

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
    一台機器有多個網域時取 ``resource_service.public_urls_by_vmid`` 排序後的
    第一個（依內部 port、網域排序，結果固定）。
    """
    # 函式內 import：resource_service 牽動排程／防火牆等模組，不要變成模組層依賴
    from app.services.resource import resource_service

    return {
        vmid: urls[0]
        for vmid, urls in resource_service.public_urls_by_vmid(session, vmids).items()
        if urls
    }
