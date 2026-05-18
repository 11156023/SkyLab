"""反向代理服務 — 透過 Gateway VM 的 Traefik 管理 domain → VM 映射。

設計原則：
- DB 為 source of truth
- 每次新增 / 刪除後，從 DB 完整重建 Traefik dynamic config（YAML）
- Traefik 的 file provider 設定 watch: true，寫入後自動生效，無需 reload
- dns_provider 欄位預留給 Cloudflare 等 DNS API 對接
"""

import logging
import re

import yaml

from app.exceptions import BadRequestError, ProxmoxError
from app.schemas.reverse_proxy import ReverseProxySetupContext, ReverseProxyZoneOption

logger = logging.getLogger(__name__)
_HOSTNAME_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)


# ─── Traefik dynamic config 產生 ───────────────────────────────────────────────


def build_runtime_name(vmid: int, domain: str) -> str:
    return f"cc-{vmid}-{domain.replace('.', '-')}"


def build_full_domain(*, zone_name: str, hostname_prefix: str) -> str:
    clean_zone_name = zone_name.strip().lower().rstrip(".")
    if not _is_valid_hostname(clean_zone_name):
        raise BadRequestError("Zone 名稱格式不正確")

    clean_hostname_prefix = hostname_prefix.strip().lower().strip(".")
    if not clean_hostname_prefix:
        return clean_zone_name

    labels = clean_hostname_prefix.split(".")
    if not all(_HOSTNAME_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise BadRequestError("子網域格式不正確")

    full_domain = f"{clean_hostname_prefix}.{clean_zone_name}"
    if len(full_domain) > 255:
        raise BadRequestError("網域名稱過長")
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

    from app.models import Resource  # noqa: PLC0415

    return vmid if get(Resource, vmid) is not None else None


def _get_gateway_ready_state(session: object) -> tuple[bool, str | None]:
    from app.repositories import gateway_config as gw_repo  # noqa: PLC0415

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        return False, "Gateway VM 尚未設定"
    return True, None


def _get_cloudflare_ready_state(
    session: object,
) -> tuple[bool, str | None, list[ReverseProxyZoneOption], str | None, str | None]:
    from app.services.network import cloudflare_service  # noqa: PLC0415

    config = cloudflare_service.get_public_config(session)  # type: ignore[arg-type]
    if not config.is_configured:
        return False, "Cloudflare API Token 尚未設定", [], None, None
    if not config.has_default_dns_target:
        return False, "Cloudflare 預設 DNS 指向尚未設定", [], None, None

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
            "Cloudflare 沒有可用的 active Zone",
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


def resolve_vmid_ip(*, vmid: int, session: object | None = None) -> str | None:
    """取得 VM 的 IP 位址，優先即時查詢，失敗時回退 DB 快取。"""
    from app.repositories import resource as resource_repo  # noqa: PLC0415
    from app.services.proxmox import proxmox_service  # noqa: PLC0415

    ip: str | None = None
    try:
        resource = proxmox_service.find_resource(vmid)
        node = resource["node"]
        resource_type = resource["type"]
        ip = proxmox_service.get_ip_address(node, vmid, resource_type)
    except Exception:
        # Fallback to DB cache if PVE API fails
        pass

    if ip and session is not None:
        try:
            resource_repo.update_ip_address(
                session=session, vmid=vmid, ip_address=ip
            )  # type: ignore[arg-type]
        except Exception as exc:
            logger.debug("VM %s IP 快取寫入失敗: %s", vmid, exc)
        return ip

    if ip:
        return ip

    if session is not None:
        try:
            cached_ip = resource_repo.get_cached_ip_address(  # type: ignore[arg-type]
                session=session, vmid=vmid
            )
            if cached_ip:
                return cached_ip
        except Exception as exc:
            logger.debug("VM %s DB 快取讀取失敗: %s", vmid, exc)

    return None


def _build_traefik_dynamic_config(rules: list) -> str:
    """從 DB 規則列表產生 Traefik dynamic config YAML。"""
    routers: dict = {}
    services: dict = {}

    for r in rules:
        safe_name = build_runtime_name(r.vmid, r.domain)

        router: dict = {
            "rule": f"Host(`{r.domain}`)",
            "service": f"{safe_name}-svc",
            "entryPoints": ["websecure"] if r.enable_https else ["web"],
        }
        if r.enable_https:
            router["tls"] = {"certResolver": "letsencrypt"}

        routers[safe_name] = router

        services[f"{safe_name}-svc"] = {
            "loadBalancer": {
                "servers": [{"url": f"http://{r.vm_ip}:{r.internal_port}"}],
            }
        }

    config: dict = {
        "http": {
            "routers": routers if routers else {},
            "services": services if services else {},
        }
    }

    header = (
        "# Campus Cloud 自動管理的反向代理設定\n"
        "# 此檔案由 Campus Cloud 自動維護，請勿手動修改\n\n"
    )
    return header + yaml.dump(config, default_flow_style=False, allow_unicode=True)


# ─── Traefik 同步（核心）──────────────────────────────────────────────────────


def _sync_traefik(session: object) -> None:
    """從 DB 重建 Traefik dynamic config 並寫入 Gateway VM。
    Traefik file provider 設定 watch: true，寫入即生效。
    """
    from app.infrastructure.ssh import create_key_client, exec_command  # noqa: PLC0415
    from app.repositories import gateway_config as gw_repo  # noqa: PLC0415
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )
    from app.services.network import gateway_service  # noqa: PLC0415
    from app.services.network.gateway_service import (
        TRAEFIK_DYNAMIC_PATH,  # noqa: PLC0415
    )

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise ProxmoxError("Gateway VM 尚未設定，無法同步 Traefik 規則")

    rules = rp_repo.list_rules(session)  # type: ignore[arg-type]
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    if any(rule.enable_https for rule in rules):
        gateway_service.sync_traefik_dns_challenge(session)

    new_cfg = _build_traefik_dynamic_config(rules)
    tmp_path = TRAEFIK_DYNAMIC_PATH + ".tmp"

    logger.info(
        f"[ReverseProxy] 準備同步 {len(rules)} 條規則到 {config.host}:{config.ssh_port}"
    )
    logger.debug(f"[ReverseProxy] 生成的 Traefik config:\n{new_cfg}")

    client = create_key_client(
        config.host,
        config.ssh_port,
        config.ssh_user,
        private_key_pem,
    )
    try:
        # 確保目錄存在
        code, out, err = exec_command(
            client, f"mkdir -p $(dirname {TRAEFIK_DYNAMIC_PATH})"
        )
        if code != 0:
            raise ProxmoxError(f"建立目錄失敗：{out}{err}")

        # 原子性寫入
        content_bytes = new_cfg.encode("utf-8")
        sftp = client.open_sftp()
        try:
            with sftp.open(tmp_path, "wb") as f:
                f.write(content_bytes)
        finally:
            sftp.close()

        code, out, err = exec_command(client, f"mv {tmp_path} {TRAEFIK_DYNAMIC_PATH}")
        if code != 0:
            raise ProxmoxError(f"Traefik 設定寫入失敗：{out}{err}")

        # 驗證寫入結果
        code, verify_out, _ = exec_command(
            client, f"wc -c < {TRAEFIK_DYNAMIC_PATH}"
        )
        written_size = verify_out.strip() if code == 0 else "unknown"
        logger.info(
            f"[ReverseProxy] Traefik 已同步 {len(rules)} 條 domain 規則 "
            f"(檔案大小: {written_size} bytes, 預期: {len(content_bytes)} bytes)"
        )

        # 檢查 Traefik 服務狀態
        code, _, _ = exec_command(client, "systemctl is-active traefik")
        if code != 0:
            logger.warning(
                "[ReverseProxy] Traefik 服務未在運行，設定已寫入但可能不會立即生效"
            )
    except ProxmoxError:
        raise
    except Exception as e:
        raise ProxmoxError(f"Traefik 同步失敗：{e}")
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
    """建立反向代理規則：寫入 DB + 同步 Traefik。"""
    from app.models.reverse_proxy_rule import ReverseProxyRule  # noqa: PLC0415
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415
    from app.services.network import cloudflare_service  # noqa: PLC0415

    ensure_reverse_proxy_ready(session)
    zone = cloudflare_service.get_zone(session=session, zone_id=zone_id)  # type: ignore[arg-type]
    domain = build_full_domain(zone_name=zone.name, hostname_prefix=hostname_prefix)

    if rp_repo.is_domain_taken(session, domain):  # type: ignore[arg-type]
        raise BadRequestError(f"網域 {domain} 已被其他 VM 佔用")

    record = cloudflare_service.upsert_reverse_proxy_dns_record(  # type: ignore[arg-type]
        session=session,
        zone_id=zone_id,
        domain=domain,
        vmid=vmid,
    )

    rule = ReverseProxyRule(
        vmid=vmid,
        resource_vmid=_resolve_resource_vmid(session, vmid),
        vm_ip=vm_ip,
        domain=domain,
        zone_id=zone_id,
        cloudflare_record_id=record.id,
        internal_port=internal_port,
        enable_https=enable_https,
        dns_provider="cloudflare",
    )
    rp_repo.create_rule(session, rule)  # type: ignore[arg-type]
    _sync_traefik(session)


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
    import uuid as _uuid  # noqa: PLC0415

    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415
    from app.services.network import cloudflare_service  # noqa: PLC0415

    ensure_reverse_proxy_ready(session)
    rule = rp_repo.get_rule(session, _uuid.UUID(rule_id))  # type: ignore[arg-type]
    if rule is None:
        raise BadRequestError(f"反向代理規則 {rule_id} 不存在")

    zone = cloudflare_service.get_zone(session=session, zone_id=zone_id)  # type: ignore[arg-type]
    domain = build_full_domain(zone_name=zone.name, hostname_prefix=hostname_prefix)
    if rp_repo.is_domain_taken(session, domain, exclude_rule_id=rule.id):  # type: ignore[arg-type]
        raise BadRequestError(f"網域 {domain} 已被其他 VM 佔用")

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
    _sync_traefik(session)


def _cleanup_managed_dns_record(session: object, rule) -> None:
    from app.services.network import cloudflare_service  # noqa: PLC0415

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


def remove_reverse_proxy_rule_by_id(session: object, rule_id: str) -> None:
    """刪除指定反向代理規則。"""
    import uuid as _uuid  # noqa: PLC0415

    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    rule = rp_repo.get_rule(session, _uuid.UUID(rule_id))  # type: ignore[arg-type]
    if rule is None:
        raise BadRequestError(f"反向代理規則 {rule_id} 不存在")

    _cleanup_managed_dns_record(session, rule)
    rp_repo.delete_rule(session, rule)  # type: ignore[arg-type]
    _sync_traefik(session)


def remove_reverse_proxy_rules_for_vmid(session: object, vmid: int) -> None:
    """刪除指定 VM 的所有反向代理規則。"""
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    deleted = rp_repo.delete_rules_by_vmid(session, vmid)  # type: ignore[arg-type]
    if deleted:
        for rule in deleted:
            _cleanup_managed_dns_record(session, rule)
        _sync_traefik(session)


def remove_reverse_proxy_rules_by_internal_port(
    session: object, vmid: int, internal_port: int
) -> None:
    """刪除指定 VM 特定內部 port 的反向代理規則。"""
    from app.repositories import reverse_proxy as rp_repo  # noqa: PLC0415

    deleted = rp_repo.delete_rules_by_vmid_and_port(  # type: ignore[arg-type]
        session, vmid, internal_port
    )
    if deleted:
        for rule in deleted:
            _cleanup_managed_dns_record(session, rule)
        _sync_traefik(session)


def sync_to_gateway(session: object) -> None:
    """手動觸發 Traefik 同步。"""
    _sync_traefik(session)
