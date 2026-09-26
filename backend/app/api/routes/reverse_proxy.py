"""獨立反向代理管理 API 路由。"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser, SessionDep, check_firewall_access
from app.core.authorizers import can_bypass_resource_ownership
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import AuditAction
from app.repositories import reverse_proxy as rp_repo
from app.schemas import Message
from app.schemas.firewall import (
    PublishedServiceCreate,
    PublishedServiceRef,
    ReverseProxyRulePublic,
)
from app.schemas.reverse_proxy import (
    DomainAvailability,
    ReverseProxyRuleCreate,
    ReverseProxyRuleUpdate,
    ReverseProxyRuntimeSnapshot,
    ReverseProxySetupContext,
)
from app.services.network import (
    cloudflare_service,
    firewall_service,
    nat_service,
    nginx_runtime_service,
    reverse_proxy_service,
)
from app.services.resource import access as resource_access
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reverse-proxy", tags=["reverse-proxy"])


def _get_visible_rules(session: SessionDep, current_user: CurrentUser):
    """可見範圍與防火牆拓撲一致：老師看得到自己班級學生機器上的規則。"""
    rules = rp_repo.list_rules(session)
    if can_bypass_resource_ownership(current_user):
        return rules

    visible_vmids = resource_access.list_reachable_vmids(
        session=session, user=current_user
    )
    return [rule for rule in rules if rule.vmid in visible_vmids]


def _serialize_rule(rule) -> ReverseProxyRulePublic:
    return ReverseProxyRulePublic(
        id=rule.id,
        vmid=rule.vmid,
        vm_ip=rule.vm_ip,
        domain=rule.domain,
        zone_id=rule.zone_id,
        internal_port=rule.internal_port,
        enable_https=rule.enable_https,
        dns_provider=rule.dns_provider,
        created_at=rule.created_at,
    )


def _filter_runtime_snapshot(
    snapshot: ReverseProxyRuntimeSnapshot,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReverseProxyRuntimeSnapshot:
    """非管理員只看得到自己可見機器的網域區塊；Port 轉發與憑證清單只給管理員。"""
    if can_bypass_resource_ownership(current_user):
        return snapshot

    visible_rules = _get_visible_rules(session, current_user)
    server_names = {
        reverse_proxy_service.build_runtime_name(rule.vmid, rule.domain)
        for rule in visible_rules
    }

    return ReverseProxyRuntimeSnapshot(
        runtime_error=snapshot.runtime_error,
        version=snapshot.version,
        active=snapshot.active,
        config_valid=snapshot.config_valid,
        http_servers=[
            server for server in snapshot.http_servers if server.name in server_names
        ],
        stream_servers=[],
        certificates=[],
    )



def _full_domain(session: SessionDep, *, zone_id: str, hostname_prefix: str) -> str:
    """把表單的 zone + 主機名組回完整網域，交給統一的發布路徑。"""
    zone = cloudflare_service.get_zone(session=session, zone_id=zone_id)
    return reverse_proxy_service.build_full_domain(
        zone_name=zone.name, hostname_prefix=hostname_prefix
    )


def _publish_domain_service(
    session: SessionDep, *, vmid: int, domain: str, internal_port: int, enable_https: bool
) -> None:
    """對外網址一律走 publish_vm_service：nginx、DNS 與防火牆入站規則一起建立。

    這個路由早期直接寫反向代理與 Cloudflare，機器上卻沒有對應的入站規則，
    造成「DB 有紀錄、Proxmox 沒有」的半套狀態（list_vm_published_services
    至今仍要標記 firewall_rule_present=False 來容忍這批資料）。
    """
    firewall_service.publish_vm_service(
        vmid,
        PublishedServiceCreate(
            port=internal_port,
            protocol="tcp",
            mode="domain",
            domain=domain,
            enable_https=enable_https,
        ),
        session,
    )


# nginx 的執行期狀態得 SSH 進 Gateway 才拿得到，而這份快照對所有人都一樣
# （可見範圍是拿到之後才濾的），拓撲頁多開幾個分頁就重複連線一次。
# 用模組層短快取擋掉這些重複，失敗結果也一起快取，免得 Gateway 掛掉時
# 每次請求都卡在 SSH timeout。
_RUNTIME_CACHE_TTL_SECONDS = 15.0


class _RuntimeCache:
    """模組層快取狀態；用屬性而非 global 重新綁定，測試可直接重置 ``entry``。"""

    entry: tuple[float, ReverseProxyRuntimeSnapshot] | None = None


_runtime_cache = _RuntimeCache()
_runtime_cache_lock = threading.Lock()


def _load_runtime_snapshot(session: SessionDep) -> ReverseProxyRuntimeSnapshot:
    """取回（或沿用快取的）nginx 執行期快照；錯誤訊息是給管理員看的詳細版。"""
    now = time.monotonic()
    with _runtime_cache_lock:
        cached = _runtime_cache.entry
    if cached is not None and now - cached[0] < _RUNTIME_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        snapshot = nginx_runtime_service.get_runtime_snapshot(session=session)
    except (BadRequestError, ProxmoxError) as exc:
        logger.warning("Unable to fetch nginx runtime: %s", exc)
        snapshot = ReverseProxyRuntimeSnapshot(runtime_error=str(exc))
    except Exception:
        logger.exception("Failed to fetch nginx runtime snapshot")
        snapshot = ReverseProxyRuntimeSnapshot(
            runtime_error=t("reverseProxy.runtimeFetchFailed")
        )

    with _runtime_cache_lock:
        _runtime_cache.entry = (time.monotonic(), snapshot)
    return snapshot


@router.get("/runtime", response_model=ReverseProxyRuntimeSnapshot)
def get_runtime_snapshot(session: SessionDep, current_user: CurrentUser):
    snapshot = _load_runtime_snapshot(session)
    # 詳細的失敗原因會帶到 Gateway 主機與 SSH 細節，只給管理員看
    if snapshot.runtime_error and not getattr(current_user, "is_superuser", False):
        snapshot = snapshot.model_copy(
            update={"runtime_error": t("reverseProxy.runtimeFetchFailed")}
        )
    return _filter_runtime_snapshot(snapshot, session, current_user)


@router.get("/rules", response_model=list[ReverseProxyRulePublic])
def list_reverse_proxy_rules(session: SessionDep, current_user: CurrentUser):
    return [_serialize_rule(rule) for rule in _get_visible_rules(session, current_user)]


@router.get("/setup-context", response_model=ReverseProxySetupContext)
def get_setup_context(session: SessionDep, _: CurrentUser):
    return reverse_proxy_service.get_reverse_proxy_setup_context(session=session)


@router.get("/domain-availability", response_model=DomainAvailability)
def get_domain_availability(
    domain: str,
    session: SessionDep,
    _current_user: CurrentUser,
    exclude_rule_id: str | None = None,
) -> DomainAvailability:
    """表單即時檢查：這個網域是不是已經被用掉了（不管是不是本系統建的）。"""
    exclude: uuid.UUID | None = None
    if exclude_rule_id:
        try:
            exclude = uuid.UUID(exclude_rule_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=t("reverseProxy.invalidRuleId"))
    return reverse_proxy_service.check_domain_availability(
        session, domain, exclude_rule_id=exclude
    )


@router.post("/rules", response_model=Message)
def create_reverse_proxy_rule(
    body: ReverseProxyRuleCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    check_firewall_access(vmid=body.vmid, current_user=current_user, session=session)

    try:
        _publish_domain_service(
            session,
            vmid=body.vmid,
            domain=_full_domain(
                session, zone_id=body.zone_id, hostname_prefix=body.hostname_prefix
            ),
            internal_port=body.internal_port,
            enable_https=body.enable_https,
        )
        return Message(message=t("reverseProxy.ruleCreated"))
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except ProxmoxError as exc:
        logger.error("Proxmox error creating reverse proxy rule: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        logger.exception("Failed to create reverse proxy rule")
        raise HTTPException(status_code=500, detail=t("reverseProxy.createFailed"))


@router.put("/rules/{rule_id}", response_model=Message)
def update_reverse_proxy_rule(
    rule_id: str,
    body: ReverseProxyRuleUpdate,
    session: SessionDep,
    current_user: CurrentUser,
):
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=t("reverseProxy.invalidRuleId"))

    existing_rule = rp_repo.get_rule(session, rule_uuid)
    if existing_rule is None:
        raise HTTPException(status_code=404, detail=t("reverseProxy.ruleNotFound"))

    check_firewall_access(vmid=existing_rule.vmid, current_user=current_user, session=session)
    if body.vmid != existing_rule.vmid:
        check_firewall_access(vmid=body.vmid, current_user=current_user, session=session)

    try:
        # 先撤下舊的（連同它的入站規則）再重新發布，換機器時也不會留下孤兒規則。
        firewall_service.unpublish_vm_service(
            existing_rule.vmid,
            PublishedServiceRef(port=existing_rule.internal_port, protocol="tcp"),
            session,
        )
        _publish_domain_service(
            session,
            vmid=body.vmid,
            domain=_full_domain(
                session, zone_id=body.zone_id, hostname_prefix=body.hostname_prefix
            ),
            internal_port=body.internal_port,
            enable_https=body.enable_https,
        )
        return Message(message=t("reverseProxy.ruleUpdated"))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    except ProxmoxError as exc:
        logger.error("Proxmox error updating reverse proxy rule %s: %s", rule_id, exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        logger.exception("Failed to update reverse proxy rule %s", rule_id)
        raise HTTPException(status_code=500, detail=t("reverseProxy.updateFailed"))


@router.delete("/rules/{rule_id}", response_model=Message)
def delete_reverse_proxy_rule(
    rule_id: str,
    session: SessionDep,
    current_user: CurrentUser,
):
    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=t("reverseProxy.invalidRuleId"))

    rule = rp_repo.get_rule(session, rule_uuid)
    if rule is None:
        raise HTTPException(status_code=404, detail=t("reverseProxy.ruleNotFound"))

    check_firewall_access(vmid=rule.vmid, current_user=current_user, session=session)

    try:
        # 撤下服務會一併刪掉機器上的入站規則；反向代理紀錄由它連帶清除。
        firewall_service.unpublish_vm_service(
            rule.vmid,
            PublishedServiceRef(port=rule.internal_port, protocol="tcp"),
            session,
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=rule.vmid,
            action=AuditAction.reverse_proxy_rule_delete,
            details=(
                f"Deleted reverse proxy rule {rule_id} "
                f"(vmid={rule.vmid} domain={rule.domain})"
            ),
        )
        return Message(message=t("reverseProxy.ruleDeleted"))
    except ProxmoxError as exc:
        logger.error("Proxmox error removing reverse proxy rule %s: %s", rule_id, exc)
        raise HTTPException(
            status_code=502, detail=t("reverseProxy.proxmoxOperationFailed")
        )
    except Exception:
        logger.exception("Failed to remove reverse proxy rule %s", rule_id)
        raise HTTPException(status_code=500, detail=t("reverseProxy.deleteFailed"))


@router.post("/rules/sync", response_model=Message)
def sync_reverse_proxy_rules(session: SessionDep, current_user: AdminUser):
    """把 Gateway nginx 的兩份自動設定都依 DB 重建：網域反向代理與 Port 轉發。"""
    try:
        reverse_proxy_service.sync_to_gateway(session=session)
        nat_service.sync_to_gateway(session=session)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.reverse_proxy_rule_sync,
            details="Manually synced reverse proxy and port forwarding rules to the gateway nginx",
        )
        return Message(message=t("reverseProxy.rulesSynced"))
    except ProxmoxError as exc:
        logger.error("Proxmox error syncing reverse proxy rules: %s", exc)
        raise HTTPException(
            status_code=502, detail=t("reverseProxy.proxmoxOperationFailed")
        )
    except Exception:
        logger.exception("Failed to sync reverse proxy rules")
        raise HTTPException(status_code=500, detail=t("reverseProxy.syncFailed"))
