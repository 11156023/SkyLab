"""IP 管理服務 — 子網配置、IP 分配/釋放、系統防護。

設計原則：
- SubnetConfig 為 singleton（id=1），管理者設定一次即啟用 IP 管理
- IpAllocation 以 ip_address 的 UNIQUE 約束確保不重複
- allocate_ip 使用 SELECT ... FOR UPDATE 防止並發衝突
- ensure_subnet_configured() 作為所有 VM/LXC 操作的前置防護
"""

import ipaddress
import logging

from sqlmodel import Session, select

from app.exceptions import BadRequestError, ConflictError
from app.models import Resource
from app.models.base import get_datetime_utc
from app.models.ip_allocation import IpAllocation
from app.models.subnet_config import SubnetConfig

logger = logging.getLogger(__name__)


# ─── 子網配置 CRUD ──────────────────────────────────────────────────────────


def get_subnet_config(session: Session) -> SubnetConfig | None:
    """取得子網配置（singleton）"""
    return session.get(SubnetConfig, 1)


def get_extra_blocked_subnets(config: SubnetConfig | None) -> list[str]:
    """解析 extra_blocked_subnets 欄位為 list[str]（已過濾與去重）。"""
    if config is None or not config.extra_blocked_subnets:
        return []
    raw = config.extra_blocked_subnets.replace("\n", ",")
    items = [s.strip() for s in raw.split(",")]
    seen: set[str] = set()
    out: list[str] = []
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def upsert_subnet_config(
    session: Session,
    *,
    cidr: str,
    gateway: str,
    bridge_name: str,
    gateway_vm_ip: str,
    dns_servers: str | None = None,
    extra_blocked_subnets: list[str] | None = None,
) -> SubnetConfig:
    """設定或更新子網配置，並保留系統 IP。

    若已有 VM/LXC 類型的 IP 分配且 CIDR 改變，則拒絕操作。
    """
    network = ipaddress.IPv4Network(cidr, strict=False)

    # 驗證所有 IP 都在 CIDR 範圍內
    for ip_str, label in [
        (gateway, "閘道 IP"),
        (gateway_vm_ip, "Gateway VM IP"),
    ]:
        ip = ipaddress.IPv4Address(ip_str)
        if ip not in network:
            raise BadRequestError(f"{label} ({ip_str}) 不在子網 {cidr} 範圍內")

    # 閘道與 Gateway VM IP 不可相同
    if gateway == gateway_vm_ip:
        raise BadRequestError("閘道 IP 與 Gateway VM IP 不可相同")

    existing = get_subnet_config(session)

    if existing:
        old_network = ipaddress.IPv4Network(existing.cidr, strict=False)
        if old_network != network:
            # CIDR 改變 → 檢查是否有 VM/LXC 分配
            vm_count = session.exec(
                select(IpAllocation).where(
                    IpAllocation.purpose.in_(["vm", "lxc"])  # type: ignore[union-attr]
                )
            ).first()
            if vm_count is not None:
                raise ConflictError(
                    "已有 VM/LXC 使用目前網段的 IP，無法變更 CIDR。"
                    "請先刪除所有 VM/LXC 後再變更。"
                )

        existing.cidr = str(network)
        existing.gateway = gateway
        existing.bridge_name = bridge_name
        existing.gateway_vm_ip = gateway_vm_ip
        existing.dns_servers = dns_servers
        if extra_blocked_subnets is not None:
            existing.extra_blocked_subnets = (
                ",".join(extra_blocked_subnets) if extra_blocked_subnets else None
            )
        existing.updated_at = get_datetime_utc()
        session.add(existing)
        config = existing
    else:
        config = SubnetConfig(
            id=1,
            cidr=str(network),
            gateway=gateway,
            bridge_name=bridge_name,
            gateway_vm_ip=gateway_vm_ip,
            dns_servers=dns_servers,
            extra_blocked_subnets=(
                ",".join(extra_blocked_subnets)
                if extra_blocked_subnets
                else None
            ),
        )
        session.add(config)

    session.flush()

    # 清除舊的系統 IP 保留並重新建立
    _reserve_system_ips(session, config)

    session.commit()
    session.refresh(config)
    logger.info("子網配置已更新: %s (bridge=%s)", config.cidr, config.bridge_name)
    return config


def delete_subnet_config(session: Session) -> None:
    """刪除子網配置（需先確認無 VM/LXC 分配）"""
    config = get_subnet_config(session)
    if config is None:
        raise BadRequestError("子網配置不存在")

    vm_alloc = session.exec(
        select(IpAllocation).where(
            IpAllocation.purpose.in_(["vm", "lxc"])  # type: ignore[union-attr]
        )
    ).first()
    if vm_alloc is not None:
        raise ConflictError("仍有 VM/LXC 使用 IP 分配，無法刪除子網配置")

    # 刪除所有 IP 分配（含系統保留）
    all_allocs = session.exec(select(IpAllocation)).all()
    for alloc in all_allocs:
        session.delete(alloc)

    session.delete(config)
    session.commit()
    logger.info("子網配置已刪除")


# ─── 系統 IP 保留 ───────────────────────────────────────────────────────────


def _reserve_system_ips(session: Session, config: SubnetConfig) -> None:
    """保留系統級 IP（閘道、Gateway VM）。

    先清除既有系統保留，再重新建立。
    """
    system_purposes = ["subnet_gateway", "gateway_vm"]
    old_allocs = session.exec(
        select(IpAllocation).where(
            IpAllocation.purpose.in_(system_purposes)  # type: ignore[union-attr]
        )
    ).all()
    for alloc in old_allocs:
        session.delete(alloc)
    session.flush()

    reserves = [
        (config.gateway, "subnet_gateway", "子網閘道"),
        (config.gateway_vm_ip, "gateway_vm", "Gateway VM"),
    ]
    for ip, purpose, desc in reserves:
        alloc = IpAllocation(
            ip_address=ip,
            purpose=purpose,
            description=desc,
        )
        session.add(alloc)

    session.flush()


# ─── IP 分配與釋放 ──────────────────────────────────────────────────────────


def allocate_ip(session: Session, vmid: int, purpose: str) -> str:
    """為 VM/LXC 分配下一個可用 IP。

    使用 SELECT FOR UPDATE 鎖定 subnet_config 防止並發衝突，
    ip_allocation 表的 UNIQUE 約束作為最終保障。
    """
    # 鎖定 subnet_config 確保串行化
    config = session.exec(
        select(SubnetConfig).where(SubnetConfig.id == 1).with_for_update()
    ).first()
    if config is None:
        raise BadRequestError("請先設定 IP 管理網段才能進行此操作")

    network = ipaddress.IPv4Network(config.cidr, strict=False)

    # 取得所有已分配的 IP
    allocated = set(
        session.exec(select(IpAllocation.ip_address)).all()
    )

    # 遍歷 host IPs（排除 network 和 broadcast）
    for host_ip in network.hosts():
        ip_str = str(host_ip)
        if ip_str not in allocated:
            alloc = IpAllocation(
                ip_address=ip_str,
                purpose=purpose,
                vmid=vmid,
                resource_vmid=vmid if session.get(Resource, vmid) is not None else None,
                description=f"VMID {vmid}",
            )
            session.add(alloc)
            session.flush()
            logger.info("已為 VMID %s 分配 IP %s (purpose=%s)", vmid, ip_str, purpose)
            return ip_str

    raise ConflictError("IP 地址已耗盡，無法分配新的 IP")


def release_ip(session: Session, vmid: int) -> str | None:
    """釋放指定 VMID 的 IP 分配，回傳被釋放的 IP 或 None。"""
    alloc = session.exec(
        select(IpAllocation).where(IpAllocation.vmid == vmid)
    ).first()
    if alloc is None:
        logger.debug("VMID %s 無 IP 分配記錄，跳過釋放", vmid)
        return None

    ip = alloc.ip_address
    session.delete(alloc)
    session.flush()
    logger.info("已釋放 VMID %s 的 IP %s", vmid, ip)
    return ip


def release_ip_by_address(session: Session, ip_address: str) -> bool:
    """依 IP 位址釋放分配"""
    alloc = session.exec(
        select(IpAllocation).where(IpAllocation.ip_address == ip_address)
    ).first()
    if alloc is None:
        return False
    session.delete(alloc)
    session.flush()
    logger.info("已釋放 IP %s", ip_address)
    return True


# ─── 查詢 ──────────────────────────────────────────────────────────────────


def get_all_allocations(session: Session) -> list[IpAllocation]:
    """取得所有 IP 分配記錄"""
    allocs = list(session.exec(select(IpAllocation)).all())
    # 依 IP 數值排序（而非字串排序）
    allocs.sort(key=lambda a: ipaddress.IPv4Address(a.ip_address))
    return allocs


def get_ip_stats(session: Session) -> dict[str, int]:
    """回傳 IP 統計: total, used, available"""
    config = get_subnet_config(session)
    if config is None:
        return {"total": 0, "used": 0, "available": 0}

    network = ipaddress.IPv4Network(config.cidr, strict=False)
    total = network.num_addresses - 2  # 去除 network 和 broadcast
    if total < 0:
        total = 0

    used = len(session.exec(select(IpAllocation)).all())
    return {"total": total, "used": used, "available": max(0, total - used)}


# ─── 防護 ──────────────────────────────────────────────────────────────────


def ensure_subnet_configured(session: Session) -> SubnetConfig:
    """斷言子網已設定。若未設定則 raise BadRequestError。

    同時回傳 config 方便呼叫者使用。
    """
    config = get_subnet_config(session)
    if config is None:
        raise BadRequestError("請先設定 IP 管理網段才能進行此操作")
    return config


def get_network_config_for_vm(session: Session) -> dict:
    """取得 VM/LXC 建立時所需的網路配置資訊。

    回傳 dict 含: bridge_name, prefix_len, gateway, dns_servers
    """
    config = ensure_subnet_configured(session)
    network = ipaddress.IPv4Network(config.cidr, strict=False)
    result = {
        "bridge_name": config.bridge_name,
        "prefix_len": network.prefixlen,
        "gateway": config.gateway,
        "gateway_vm_ip": config.gateway_vm_ip,
    }
    if config.dns_servers:
        result["dns_servers"] = config.dns_servers
    return result
