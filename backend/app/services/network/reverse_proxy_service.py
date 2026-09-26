"""反向代理服務 — 透過 Gateway 主機上的 nginx 管理 domain → VM 映射。

設計原則：
- DB 為 source of truth
- 每次新增 / 刪除後，從 DB 完整重建 ``/etc/nginx/skylab/http.conf``、驗證並 reload
- HTTPS 憑證由 certbot 以 Cloudflare DNS-01 簽發（同一 zone 共用萬用憑證），
  簽不下來時先掛自簽憑證讓站台可用，下次同步再補簽
- dns_provider 欄位預留給 Cloudflare 等 DNS API 對接
"""

import logging
import re

from sqlalchemy.exc import IntegrityError

from app.core.i18n import t
from app.exceptions import BadRequestError, ProxmoxError
from app.schemas.reverse_proxy import (
    DomainAvailability,
    ReverseProxySetupContext,
    ReverseProxyZoneOption,
)
from app.services.network.publish_target_policy import assert_publishable_vm_ip

logger = logging.getLogger(__name__)
_HOSTNAME_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


# ─── 名稱與網域 ──────────────────────────────────────────────────────────────


def build_runtime_name(vmid: int, domain: str) -> str:
    """nginx http.conf 裡每個網域區塊的識別名（與執行期快照對得上）。"""
    from app.services.network import nginx_gateway_service as nginx

    return nginx.http_server_name(vmid, domain)


def build_full_domain(*, zone_name: str, hostname_prefix: str) -> str:
    clean_zone_name = zone_name.strip().lower().rstrip(".")
    if not _is_valid_hostname(clean_zone_name):
        raise BadRequestError(t("reverseProxy.zoneNameInvalid"))

    clean_hostname_prefix = hostname_prefix.strip().lower().strip(".")
    if not clean_hostname_prefix:
        return clean_zone_name

    labels = clean_hostname_prefix.split(".")
    if not all(_HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise BadRequestError(t("reverseProxy.subdomainInvalid"))

    full_domain = f"{clean_hostname_prefix}.{clean_zone_name}"
    if len(full_domain) > 255:
        raise BadRequestError(t("reverseProxy.domainNameTooLong"))
    return full_domain


def _is_valid_hostname(value: str) -> bool:
    if not value or len(value) > 255 or "." not in value:
        return False
    labels = value.split(".")
    return all(_HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels)


def _resolve_resource_vmid(session: object, vmid: int) -> int | None:
    get = getattr(session, "get", None)
    if get is None:
        return None

    from app.models import Resource

    return vmid if get(Resource, vmid) is not None else None


def _get_gateway_ready_state(session: object) -> tuple[bool, str | None]:
    from app.repositories import gateway_config as gw_repo

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        return False, t("reverseProxy.gatewayNotConfigured")
    return True, None


def _get_cloudflare_ready_state(
    session: object,
) -> tuple[bool, str | None, list[ReverseProxyZoneOption], str | None, str | None]:
    from app.services.network import cloudflare_service

    config = cloudflare_service.get_public_config(session)  # type: ignore[arg-type]
    if not config.is_configured:
        return False, t("reverseProxy.cloudflareApiTokenNotConfigured"), [], None, None
    if not config.has_default_dns_target:
        return False, t("reverseProxy.cloudflareDefaultDnsTargetNotConfigured"), [], None, None

    try:
        zones = cloudflare_service.list_zones(  # type: ignore[arg-type]
            session=session,
            page=1,
            per_page=100,
            status="active",
        ).items
    except Exception as exc:
        return False, str(exc), [], config.default_dns_target_type, config.default_dns_target_value

    options = [ReverseProxyZoneOption(id=zone.id, name=zone.name) for zone in zones]
    if not options:
        return (
            False,
            t("reverseProxy.cloudflareNoActiveZone"),
            [],
            config.default_dns_target_type,
            config.default_dns_target_value,
        )
    return (
        True,
        None,
        options,
        config.default_dns_target_type,
        config.default_dns_target_value,
    )


def get_reverse_proxy_setup_context(session: object) -> ReverseProxySetupContext:
    gateway_ready, gateway_reason = _get_gateway_ready_state(session)
    cloudflare_state = _get_cloudflare_ready_state(session)
    if len(cloudflare_state) == 3:
        cloudflare_ready, cloudflare_reason, zones = cloudflare_state
        default_dns_target_type = None
        default_dns_target_value = None
    else:
        (
            cloudflare_ready,
            cloudflare_reason,
            zones,
            default_dns_target_type,
            default_dns_target_value,
        ) = cloudflare_state

    reasons = [reason for reason in [gateway_reason, cloudflare_reason] if reason]
    return ReverseProxySetupContext(
        enabled=gateway_ready and cloudflare_ready,
        gateway_ready=gateway_ready,
        cloudflare_ready=cloudflare_ready,
        reasons=reasons,
        zones=zones,
        default_dns_target_type=default_dns_target_type,
        default_dns_target_value=default_dns_target_value,
    )


def ensure_reverse_proxy_ready(session: object) -> None:
    context = get_reverse_proxy_setup_context(session)
    if not context.enabled:
        raise BadRequestError("；".join(context.reasons))


# ─── nginx 同步（核心）────────────────────────────────────────────────────────


def _zone_names_by_id(session: object) -> dict[str, str]:
    """zone_id → zone 名稱，用來決定哪些網域能共用同一張萬用憑證。

    查不到（Cloudflare 暫時連不上）不擋同步，只是退回逐網域簽發。
    """
    from app.services.network import cloudflare_service

    try:
        zones = cloudflare_service.list_zones(  # type: ignore[arg-type]
            session=session,
            page=1,
            per_page=100,
            status="active",
        ).items
    except Exception as exc:
        logger.warning("查詢 Cloudflare zone 失敗，憑證改逐網域簽發: %s", exc)
        return {}
    return {zone.id: zone.name for zone in zones}


def _sync_nginx(session: object, *, renew: bool = False) -> None:
    """從 DB 重建 nginx 的 http.conf、補簽缺的憑證、驗證並 reload。

    ``renew=True`` 給管理員手動同步憑證用：多跑一次 ``certbot renew``。
    """
    from app.infrastructure.ssh import create_key_client
    from app.repositories import cloudflare_config as cf_repo
    from app.repositories import gateway_config as gw_repo
    from app.repositories import reverse_proxy as rp_repo
    from app.repositories.gateway_config import (
        get_decrypted_private_key,
    )
    from app.services.network import nginx_gateway_service as nginx

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise ProxmoxError(t("reverseProxy.gatewayNotConfiguredSyncFailed"))

    rules = rp_repo.list_rules(session)  # type: ignore[arg-type]
    https_rules = [rule for rule in rules if rule.enable_https]

    # 有 HTTPS 規則才需要 Cloudflare token（DNS-01 驗證）與憑證規劃
    cloudflare_token: str | None = None
    plans: dict[str, list[str]] = {}
    cert_name_by_domain: dict[str, str] = {}
    if https_rules:
        cloudflare_config = cf_repo.get_cloudflare_config(session)  # type: ignore[arg-type]
        if cloudflare_config is None or not cloudflare_config.encrypted_api_token:
            raise BadRequestError(t("gateway.cloudflareApiTokenNotConfigured"))
        cloudflare_token = cf_repo.get_decrypted_api_token(cloudflare_config)
        zone_names = _zone_names_by_id(session)
        for rule in https_rules:
            cert_name, domains = nginx.plan_certificate(
                rule.domain, zone_names.get(rule.zone_id or "")
            )
            plans.setdefault(cert_name, domains)
            cert_name_by_domain[rule.domain] = cert_name

    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
    logger.info(
        f"[ReverseProxy] 準備同步 {len(rules)} 條規則到 {config.host}:{config.ssh_port}"
    )

    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key_pem,
    )
    try:
        ready: set[str] = set()
        if https_rules and cloudflare_token is not None:
            nginx.write_certbot_credentials(client, cloudflare_token)
            if renew:
                nginx.renew_certificates(client)
            ready = nginx.ensure_certificates(
                client, plans, acme_email=nginx.get_acme_email()
            )

        cert_names = {
            domain: (name if name in ready else None)
            for domain, name in cert_name_by_domain.items()
        }
        nginx.write_validated_config(
            client, nginx.NGINX_HTTP_CONF_PATH, nginx.build_http_config(rules, cert_names)
        )

        missing = sorted(set(plans) - ready)
        if missing:
            logger.warning(
                "[ReverseProxy] 有 %d 張憑證尚未簽發（%s），對應網域暫用自簽憑證",
                len(missing),
                ", ".join(missing),
            )
        logger.info(f"[ReverseProxy] nginx 已同步 {len(rules)} 條 domain 規則並 reload")
    except (ProxmoxError, BadRequestError):
        raise
    except Exception as e:
        raise ProxmoxError(t("reverseProxy.nginxSyncFailed", error=e))
    finally:
        client.close()


# ─── 公開操作 ──────────────────────────────────────────────────────────────────


def apply_reverse_proxy_rule(
    session: object,
    vmid: int,
    vm_ip: str,
    zone_id: str,
    hostname_prefix: str,
    internal_port: int,
    enable_https: bool = True,
) -> None:
    """建立反向代理規則：寫入 DB + 同步 nginx。"""
    from app.models import Resource
    from app.models.reverse_proxy_rule import ReverseProxyRule
    from app.repositories import reverse_proxy as rp_repo
    from app.services.network import cloudflare_service

    ensure_reverse_proxy_ready(session)
    # vm_ip 來自 guest agent 回報，VM 擁有者可偽造：必須確認它真的是
    # 平台配發的 VM 位址，而不是 Gateway / PVE 節點等內部主機
    assert_publishable_vm_ip(session, vm_ip, vmid=vmid)

    if getattr(session, "get", lambda *_: None)(Resource, vmid) is None:
        raise BadRequestError(t("reverseProxy.vmidNotInResourceList", vmid=vmid))

    zone = cloudflare_service.get_zone(session=session, zone_id=zone_id)  # type: ignore[arg-type]
    domain = build_full_domain(zone_name=zone.name, hostname_prefix=hostname_prefix)

    # 不管是本系統建的還是使用者在 Cloudflare 上自己建的，同名紀錄一律視為衝突
    assert_domain_available(session, domain, zone_id=zone_id)

    # 先寫 DB 再動 DNS：上面的檢查與建立之間有時間差，兩個人同時送同一個
    # 網域時只有 domain 的 UNIQUE 約束擋得住。反過來先建 DNS 的話，慢的那個
    # 會先把對方的紀錄覆蓋掉，才在寫 DB 時失敗。
    rule = ReverseProxyRule(
        vmid=vmid,
        resource_vmid=_resolve_resource_vmid(session, vmid),
        vm_ip=vm_ip,
        domain=domain,
        zone_id=zone_id,
        cloudflare_record_id=None,
        internal_port=internal_port,
        enable_https=enable_https,
        dns_provider="cloudflare",
    )
    try:
        created = rp_repo.create_rule(session, rule)  # type: ignore[arg-type]
    except IntegrityError as exc:
        rollback = getattr(session, "rollback", None)
        if rollback is not None:
            rollback()
        raise BadRequestError(
            t("reverseProxy.domainAlreadyTaken", domain=domain)
        ) from exc

    try:
        record = cloudflare_service.upsert_reverse_proxy_dns_record(  # type: ignore[arg-type]
            session=session,
            zone_id=zone_id,
            domain=domain,
            vmid=vmid,
        )
    except Exception:
        # DNS 建不起來就把剛剛佔位的規則收回，否則這個網域會被一條
        # 永遠不會生效的紀錄卡住
        try:
            rp_repo.delete_rule(session, created)  # type: ignore[arg-type]
        except Exception:
            logger.exception(
                "反向代理規則 %s 建立 DNS 失敗後的回滾刪除也失敗，DB 可能殘留無效規則",
                created.id,
            )
        raise

    created.cloudflare_record_id = record.id
    rp_repo.update_rule(session, created)  # type: ignore[arg-type]
    _sync_nginx(session)


def resolve_zone_for_domain(session: object, domain: str) -> tuple[str, str]:
    """從完整網域反查 Cloudflare Zone，回傳 (zone_id, hostname_prefix)。

    取 zone name 為網域字尾中最長的那個（例如 a.b.example.com 同時符合
    example.com 與 b.example.com 兩個 zone 時，取 b.example.com）。
    """
    from app.services.network import cloudflare_service

    clean = domain.strip().lower().rstrip(".")
    if not _is_valid_hostname(clean):
        raise BadRequestError(t("reverseProxy.domainInvalid", domain=domain))

    zones = cloudflare_service.list_zones(  # type: ignore[arg-type]
        session=session,
        page=1,
        per_page=100,
        status="active",
    ).items

    best: tuple[str, str] | None = None
    for zone in zones:
        zone_name = zone.name.strip().lower().rstrip(".")
        if clean == zone_name or clean.endswith(f".{zone_name}"):
            if best is None or len(zone_name) > len(best[1]):
                best = (zone.id, zone_name)
    if best is None:
        raise BadRequestError(
            t("reverseProxy.domainZoneNotFound", domain=clean)
        )

    zone_id, zone_name = best
    prefix = "" if clean == zone_name else clean[: -(len(zone_name) + 1)]
    return zone_id, prefix


_CONFLICTING_RECORD_TYPES = frozenset({"A", "AAAA", "CNAME"})


def check_domain_availability(
    session: object,
    domain: str,
    *,
    zone_id: str | None = None,
    exclude_rule_id: object = None,
) -> DomainAvailability:
    """判斷網域能不能拿來建對外網址：本系統紀錄與 Cloudflare 既有紀錄都要查。

    ``exclude_rule_id`` 用在更新既有規則：那條規則自己的網域與 DNS 紀錄不算衝突。
    ``zone_id`` 已知時略過 zone 反查。
    """
    import uuid as _uuid

    from app.repositories import reverse_proxy as rp_repo
    from app.services.network import cloudflare_service

    clean = (domain or "").strip().lower().rstrip(".")
    if not clean or not _is_valid_hostname(clean):
        return DomainAvailability(
            domain=clean,
            available=False,
            reason="invalid",
            message=t("reverseProxy.domainInvalid", domain=domain),
        )

    exclude_uuid: _uuid.UUID | None = None
    if exclude_rule_id is not None:
        exclude_uuid = (
            exclude_rule_id
            if isinstance(exclude_rule_id, _uuid.UUID)
            else _uuid.UUID(str(exclude_rule_id))
        )

    if rp_repo.is_domain_taken(session, clean, exclude_rule_id=exclude_uuid):  # type: ignore[arg-type]
        return DomainAvailability(
            domain=clean,
            available=False,
            reason="system",
            message=t("reverseProxy.domainAlreadyTaken", domain=clean),
        )

    if zone_id is None:
        try:
            zone_id, _prefix = resolve_zone_for_domain(session, clean)
        except BadRequestError as exc:
            return DomainAvailability(
                domain=clean,
                available=False,
                reason="no_zone",
                message=exc.message,
            )
        except Exception as exc:
            logger.warning("網域 %s 的 zone 反查失敗，無法驗證外部衝突: %s", clean, exc)
            return DomainAvailability(
                domain=clean,
                available=True,
                reason="unverified",
                message=t("reverseProxy.domainConflictUnverified", domain=clean),
            )

    excluded_record_id: str | None = None
    if exclude_uuid is not None:
        own_rule = rp_repo.get_rule(session, exclude_uuid)  # type: ignore[arg-type]
        excluded_record_id = own_rule.cloudflare_record_id if own_rule else None

    try:
        records = cloudflare_service.list_dns_records(  # type: ignore[arg-type]
            session=session,
            zone_id=zone_id,
            page=1,
            per_page=100,
            search=clean,
        ).items
    except Exception as exc:
        logger.warning("網域 %s 的 Cloudflare 紀錄查詢失敗，無法驗證外部衝突: %s", clean, exc)
        return DomainAvailability(
            domain=clean,
            available=True,
            reason="unverified",
            message=t("reverseProxy.domainConflictUnverified", domain=clean),
        )

    conflict = next(
        (
            record
            for record in records
            if record.name.lower() == clean
            and record.type.upper() in _CONFLICTING_RECORD_TYPES
            and record.id != excluded_record_id
        ),
        None,
    )
    if conflict is not None:
        return DomainAvailability(
            domain=clean,
            available=False,
            reason="external",
            message=t(
                "reverseProxy.domainInUseExternally",
                domain=clean,
                record_type=conflict.type,
            ),
        )
    return DomainAvailability(domain=clean, available=True)


def assert_domain_available(
    session: object,
    domain: str,
    *,
    zone_id: str | None = None,
    exclude_rule_id: object = None,
) -> None:
    """網域被占用（不論是誰建的）就 raise BadRequestError。

    查不到 Cloudflare（``reason == "unverified"``）在這裡一律當作不可用：
    表單即時提示放行沒關係，真的要建規則時放行卻可能覆蓋掉別人既有的
    DNS 紀錄。請管理員稍後再試，比悄悄蓋掉安全。
    """
    result = check_domain_availability(
        session, domain, zone_id=zone_id, exclude_rule_id=exclude_rule_id
    )
    if result.reason == "unverified":
        raise BadRequestError(
            t("reverseProxy.domainConflictCheckFailed", domain=result.domain or domain)
        )
    if not result.available:
        raise BadRequestError(
            result.message or t("reverseProxy.domainAlreadyTaken", domain=domain)
        )


def annotate_dns_records_with_system_rules(session: object, records: list) -> None:
    """把 Cloudflare DNS 紀錄標上「本系統建立」：對得上反向代理規則的 record id 或網域。"""
    from app.repositories import reverse_proxy as rp_repo

    rules = rp_repo.list_rules(session)  # type: ignore[arg-type]
    by_record_id = {r.cloudflare_record_id: r for r in rules if r.cloudflare_record_id}
    by_domain = {r.domain.lower(): r for r in rules}
    for record in records:
        rule = by_record_id.get(record.id) or by_domain.get(record.name.lower())
        if rule is None:
            continue
        record.managed_by_system = True
        record.managed_vmid = rule.vmid


def apply_reverse_proxy_rule_for_domain(
    session: object,
    *,
    vmid: int,
    vm_ip: str,
    domain: str,
    internal_port: int,
    enable_https: bool = True,
) -> None:
    """以完整網域建立反向代理規則（自動反查 Cloudflare Zone）。

    供防火牆拓撲（PortSpec.domain 為完整網域）等呼叫端使用。
    """
    zone_id, hostname_prefix = resolve_zone_for_domain(session, domain)
    apply_reverse_proxy_rule(
        session,
        vmid=vmid,
        vm_ip=vm_ip,
        zone_id=zone_id,
        hostname_prefix=hostname_prefix,
        internal_port=internal_port,
        enable_https=enable_https,
    )


def update_reverse_proxy_rule(
    session: object,
    rule_id: str,
    vmid: int,
    vm_ip: str,
    zone_id: str,
    hostname_prefix: str,
    internal_port: int,
    enable_https: bool = True,
) -> None:
    import uuid as _uuid

    from app.repositories import reverse_proxy as rp_repo
    from app.services.network import cloudflare_service

    ensure_reverse_proxy_ready(session)
    assert_publishable_vm_ip(session, vm_ip, vmid=vmid)
    rule = rp_repo.get_rule(session, _uuid.UUID(rule_id))  # type: ignore[arg-type]
    if rule is None:
        raise BadRequestError(t("reverseProxy.ruleIdNotFound", ruleId=rule_id))

    zone = cloudflare_service.get_zone(session=session, zone_id=zone_id)  # type: ignore[arg-type]
    domain = build_full_domain(zone_name=zone.name, hostname_prefix=hostname_prefix)
    assert_domain_available(
        session, domain, zone_id=zone_id, exclude_rule_id=rule.id
    )

    record = cloudflare_service.upsert_reverse_proxy_dns_record(  # type: ignore[arg-type]
        session=session,
        zone_id=zone_id,
        domain=domain,
        vmid=vmid,
        existing_zone_id=rule.zone_id,
        existing_record_id=rule.cloudflare_record_id,
    )

    rule.vmid = vmid
    rule.resource_vmid = _resolve_resource_vmid(session, vmid)
    rule.vm_ip = vm_ip
    rule.domain = domain
    rule.zone_id = zone_id
    rule.cloudflare_record_id = record.id
    rule.internal_port = internal_port
    rule.enable_https = enable_https
    rule.dns_provider = "cloudflare"
    rp_repo.update_rule(session, rule)  # type: ignore[arg-type]
    _sync_nginx(session)


def _cleanup_managed_dns_record(session: object, rule) -> None:
    from app.services.network import cloudflare_service

    if not rule.zone_id or not rule.cloudflare_record_id:
        return

    try:
        cloudflare_service.delete_reverse_proxy_dns_record(  # type: ignore[arg-type]
            session=session,
            zone_id=rule.zone_id,
            record_id=rule.cloudflare_record_id,
        )
    except Exception as exc:
        logger.warning("清理 Cloudflare DNS record 失敗 (%s): %s", rule.id, exc)


def remove_reverse_proxy_rules_for_vmid(session: object, vmid: int) -> None:
    """刪除指定 VM 的所有反向代理規則。"""
    from app.repositories import reverse_proxy as rp_repo

    deleted = rp_repo.delete_rules_by_vmid(session, vmid)  # type: ignore[arg-type]
    if deleted:
        for rule in deleted:
            _cleanup_managed_dns_record(session, rule)
        _sync_nginx(session)


def remove_reverse_proxy_rules_by_internal_port(
    session: object, vmid: int, internal_port: int
) -> None:
    """刪除指定 VM 特定內部 port 的反向代理規則。"""
    from app.repositories import reverse_proxy as rp_repo

    deleted = rp_repo.delete_rules_by_vmid_and_port(  # type: ignore[arg-type]
        session, vmid, internal_port
    )
    if deleted:
        for rule in deleted:
            _cleanup_managed_dns_record(session, rule)
        _sync_nginx(session)


def sync_to_gateway(session: object) -> None:
    """手動觸發 nginx 同步（會補簽缺的憑證）。"""
    _sync_nginx(session)


def sync_certificates(session: object) -> None:
    """管理員手動同步憑證：續期快到期的、補簽缺的，再重寫設定並 reload。"""
    _sync_nginx(session, renew=True)
