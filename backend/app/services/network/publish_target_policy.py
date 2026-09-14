"""對外發布（反向代理 / NAT port 轉發）目標 IP 的白名單檢查。

VM 的 IP 是由 guest agent（VM 內部）回報的，VM 擁有者可以任意偽造。
若不檢查，使用者只要讓 VM 回報 Gateway VM、PVE 節點、資料庫主機等內部
位址，就能把公開網域或外網 port 指向那些主機，把內部服務暴露到外網。

規則：
1. 一律拒絕 loopback / link-local / multicast / unspecified / reserved。
2. 拒絕 SubnetConfig 的 gateway、gateway_vm_ip，
   以及所有 PVE 連線的 host / gateway_ip。
3. 若已設定 SubnetConfig.cidr（平台配發 VM IP 的網段），目標必須落在其中。

extra_blocked_subnets 是 VM 出站防火牆規則，不是發布目標黑名單；
管理員可封鎖整個 VM 網段以限制橫向連線，同時允許從 Gateway 發布網站。
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable

from app.core.i18n import t
from app.exceptions import BadRequestError

logger = logging.getLogger(__name__)


def _parse_ipv4(value: str | None) -> ipaddress.IPv4Address | None:
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    return addr if isinstance(addr, ipaddress.IPv4Address) else None


def _parse_networks(values: Iterable[str]) -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    for raw in values:
        if not raw:
            continue
        try:
            network = ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError:
            logger.debug("忽略無法解析的網段設定: %r", raw)
            continue
        if isinstance(network, ipaddress.IPv4Network):
            networks.append(network)
    return networks


def validate_publish_target_ip(
    ip: str,
    *,
    allowed_cidrs: Iterable[str] = (),
    blocked_ips: Iterable[str] = (),
    blocked_cidrs: Iterable[str] = (),
) -> ipaddress.IPv4Address:
    """純函式：目標 IP 不可對外發布時 raise BadRequestError，否則回傳位址。"""
    addr = _parse_ipv4(ip)
    if addr is None:
        raise BadRequestError(t("publish.targetIpInvalid", ip=repr(ip)))

    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
        or addr == ipaddress.IPv4Address("255.255.255.255")
    ):
        raise BadRequestError(t("publish.targetIpNotPublishable", ip=str(addr)))

    for blocked in blocked_ips:
        blocked_addr = _parse_ipv4(blocked)
        if blocked_addr is not None and addr == blocked_addr:
            raise BadRequestError(
                t("publish.targetIpInfrastructure", ip=str(addr))
            )

    for network in _parse_networks(blocked_cidrs):
        if addr in network:
            raise BadRequestError(
                t("publish.targetIpBlocked", ip=str(addr), network=str(network))
            )

    allowed_networks = _parse_networks(allowed_cidrs)
    if allowed_networks and not any(addr in n for n in allowed_networks):
        raise BadRequestError(
            t("publish.targetIpOutsideVmSubnet", ip=str(addr))
        )
    return addr


def assert_publishable_vm_ip(session: object, vm_ip: str) -> None:
    """依 SubnetConfig 與 PVE 連線設定組出白/黑名單後檢查 ``vm_ip``。

    ``session`` 可能是測試用的簡化物件；任何設定查詢失敗都只會縮小名單，
    不會放行格式不合法或特殊範圍的位址。
    """
    allowed_cidrs: list[str] = []
    blocked_ips: list[str] = []

    try:
        from app.services.network import ip_management_service  # noqa: PLC0415

        subnet_config = ip_management_service.get_subnet_config(session)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.debug("讀取 SubnetConfig 失敗，略過網段白名單: %s", exc)
        subnet_config = None

    if subnet_config is not None:
        if getattr(subnet_config, "cidr", None):
            allowed_cidrs.append(subnet_config.cidr)
        for attr in ("gateway", "gateway_vm_ip"):
            value = getattr(subnet_config, attr, None)
            if value:
                blocked_ips.append(value)

    try:
        from sqlmodel import select  # noqa: PLC0415

        from app.models import ProxmoxConnection  # noqa: PLC0415

        for conn in session.exec(select(ProxmoxConnection)).all():  # type: ignore[attr-defined]
            for attr in ("host", "gateway_ip"):
                value = getattr(conn, attr, None)
                if value:
                    blocked_ips.append(value)
    except Exception as exc:  # noqa: BLE001
        logger.debug("讀取 PVE 連線清單失敗，略過節點黑名單: %s", exc)

    try:
        from app.repositories.proxmox_config import get_proxmox_config  # noqa: PLC0415

        legacy = get_proxmox_config(session)  # type: ignore[arg-type]
        if legacy is not None:
            for attr in ("host", "gateway_ip"):
                value = getattr(legacy, attr, None)
                if value:
                    blocked_ips.append(value)
    except Exception as exc:  # noqa: BLE001
        logger.debug("讀取 proxmox_config 失敗，略過節點黑名單: %s", exc)

    validate_publish_target_ip(
        vm_ip,
        allowed_cidrs=allowed_cidrs,
        blocked_ips=blocked_ips,
    )


__all__ = ["assert_publishable_vm_ip", "validate_publish_target_ip"]
