"""獨立反向代理管理 API 路由。"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import AdminUser, CurrentUser, SessionDep, check_firewall_access
from app.core.authorizers import can_bypass_resource_ownership
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import AuditAction
from app.repositories import resource as resource_repo
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
    reverse_proxy_service,
    traefik_runtime_service,
)
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reverse-proxy", tags=["reverse-proxy"])


def _get_visible_rules(session: SessionDep, current_user: CurrentUser):
    rules = rp_repo.list_rules(session)
    if can_bypass_resource_ownership(current_user):
        return rules

    own_resources = resource_repo.get_resources_by_user(
        session=session,
        user_id=current_user.id,
    )
    own_vmids = {resource.vmid for resource in own_resources}
    return [rule for rule in rules if rule.vmid in own_vmids]


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


def _extract_string_list(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [str(item) for item in payload if isinstance(item, str)]
    return []


def _filter_runtime_snapshot(
    snapshot: ReverseProxyRuntimeSnapshot,
    session: SessionDep,
    current_user: CurrentUser,
) -> ReverseProxyRuntimeSnapshot:
    if can_bypass_resource_ownership(current_user):
        return snapshot

    visible_rules = _get_visible_rules(session, current_user)
    router_names = {
        reverse_proxy_service.build_runtime_name(rule.vmid, rule.domain)
        for rule in visible_rules
    }
    service_names = {f"{router_name}-svc" for router_name in router_names}

    filtered_http_routers = [
        router
        for router in snapshot.http.routers
        if str(router.get("name", "")) in router_names
    ]
    filtered_http_services = [
        service
        for service in snapshot.http.services
        if str(service.get("name", "")) in service_names
    ]

    referenced_entrypoints: set[str] = set()
    referenced_middlewares: set[str] = set()
    for router in filtered_http_routers:
        referenced_entrypoints.update(
            _extract_string_list(
                router.get("entryPoints") or router.get("entrypoints") or []
            )
        )
        referenced_middlewares.update(
            _extract_string_list(router.get("middlewares") or [])
        )

    filtered_entrypoints = [
        entrypoint
        for entrypoint in snapshot.entrypoints
        if str(entrypoint.get("name", "")) in referenced_entrypoints
    ]
    filtered_http_middlewares = [
        middleware
        for middleware in snapshot.http.middlewares
        if str(middleware.get("name", "")) in referenced_middlewares
    ]

    return ReverseProxyRuntimeSnapshot(
        runtime_error=snapshot.runtime_error,
        version=snapshot.version,
        overview=None,
        entrypoints=filtered_entrypoints,
        http={
            "routers": filtered_http_routers,
            "services": filtered_http_services,
            "middlewares": filtered_http_middlewares,
        },
        tcp={"routers": [], "services": [], "middlewares": []},
        udp={"routers": [], "services": [], "middlewares": []},
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
    """對外網址一律走 publish_vm_service：Traefik、DNS 與防火牆入站規則一起建立。

    這個路由早期直接寫 Traefik 與 Cloudflare，機器上卻沒有對應的入站規則，
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


@router.get("/runtime", response_model=ReverseProxyRuntimeSnapshot)
def get_runtime_snapshot(session: SessionDep, current_user: CurrentUser):
    try:
        snapshot = traefik_runtime_service.get_runtime_snapshot(session=session)
    except (BadRequestError, ProxmoxError) as exc:
        logger.warning("Unable to fetch Traefik runtime: %s", exc)
        return ReverseProxyRuntimeSnapshot(runtime_error=str(exc))
    except Exception:
        logger.exception("Failed to fetch Traefik runtime snapshot")
        return ReverseProxyRuntimeSnapshot(
            runtime_error=t("reverseProxy.runtimeFetchFailed")
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
    try:
        reverse_proxy_service.sync_to_gateway(session=session)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.reverse_proxy_rule_sync,
            details="Manually synced reverse proxy rules to Gateway VM",
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
