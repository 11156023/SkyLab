"""IP 管理服務 — 子網配置、IP 分配/釋放、系統防護。

設計原則：
- SubnetConfig 為 singleton（id=1），管理者設定一次即啟用 IP 管理
- IpAllocation 以 ip_address 的 UNIQUE 約束確保不重複
- allocate_ip 使用 SELECT ... FOR UPDATE 防止並發衝突
- ensure_subnet_configured() 作為所有 VM/LXC 操作的前置防護
"""

import ipaddress
import logging
import uuid

from sqlmodel import Session, func, select

from app.core.i18n import t
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


def blocked_subnet_overlapping(
    lab: ipaddress.IPv4Network, blocked: list[str]
) -> str | None:
    """第一個與實驗室子網重疊的封鎖網段；沒有就回 None（純函式）。

    不是 IPv4 CIDR 的項目（PVE alias / ipset 名稱）無從比對，放行。
    """
    for item in blocked:
        try:
            net = ipaddress.IPv4Network(item.strip(), strict=False)
        except ValueError:
            continue
        if net.overlaps(lab):
            return item.strip()
    return None


def get_forward_port_range(config: SubnetConfig | None) -> tuple[int, int] | None:
    """對外 port 自動配號池；沒有子網設定就沒有池子。"""
    if config is None:
        return None
    return int(config.forward_port_start), int(config.forward_port_end)


def upsert_subnet_config(
    session: Session,
    *,
    cidr: str,
    gateway: str,
    bridge_name: str,
    gateway_vm_ip: str,
    dns_servers: str | None = None,
    extra_blocked_subnets: list[str] | None = None,
    forward_port_start: int | None = None,
    forward_port_end: int | None = None,
    forward_public_host: str | None = None,
) -> SubnetConfig:
    """設定或更新子網配置，並保留系統 IP。

    若已有 VM/LXC 類型的 IP 分配且 CIDR 改變，則拒絕操作。
    """
    network = ipaddress.IPv4Network(cidr, strict=False)

    # 驗證所有 IP 都在 CIDR 範圍內
    for ip_str, label_key in [
        (gateway, "ipManagement.gatewayIpLabel"),
        (gateway_vm_ip, "ipManagement.gatewayVmIpLabel"),
    ]:
        ip = ipaddress.IPv4Address(ip_str)
        if ip not in network:
            raise BadRequestError(
                t("ipManagement.ipNotInSubnet", label=t(label_key), ip=ip_str, cidr=cidr)
            )

    # 閘道與 Gateway VM IP 不可相同
    if gateway == gateway_vm_ip:
        raise BadRequestError(t("ipManagement.gatewayIpsMustDiffer"))

    # 額外封鎖網段會變成每台機器的 out-DROP，而且排在往網關的 ACCEPT 前面；
    # 跟實驗室子網重疊的話，機器連閘道、DNS、其他機器都會被自己擋掉。
    overlapping = blocked_subnet_overlapping(network, extra_blocked_subnets or [])
    if overlapping is not None:
        raise BadRequestError(
            t(
                "ipManagement.blockedSubnetOverlapsLab",
                blocked=overlapping,
                cidr=str(network),
            )
        )

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
                    t("ipManagement.cidrChangeBlockedByAllocations")
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
        if forward_port_start is not None:
            existing.forward_port_start = forward_port_start
        if forward_port_end is not None:
            existing.forward_port_end = forward_port_end
        existing.forward_public_host = forward_public_host
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
            forward_port_start=forward_port_start or 30000,
            forward_port_end=forward_port_end or 39999,
            forward_public_host=forward_public_host,
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
        raise BadRequestError(t("ipManagement.subnetConfigNotFound"))

    vm_alloc = session.exec(
        select(IpAllocation).where(
            IpAllocation.purpose.in_(["vm", "lxc"])  # type: ignore[union-attr]
        )
    ).first()
    if vm_alloc is not None:
        raise ConflictError(t("ipManagement.subnetConfigDeleteBlocked"))

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


def reserve_ips(
    session: Session,
    *,
    teaching_class_id,
    reservation_keys: list[str],
) -> dict[str, str]:
    """Atomically reserve concrete IPs for one complete environment group.

    ``teaching_class_id`` is set for formal classes. Quick-practice sessions use
    globally unique ``quick:<session>:<node>`` keys with a null class id.
    """
    config = session.exec(
        select(SubnetConfig).where(SubnetConfig.id == 1).with_for_update()
    ).first()
    if config is None:
        raise BadRequestError(t("ipManagement.subnetNotConfigured"))

    existing_rows = session.exec(
        select(IpAllocation).where(
            IpAllocation.teaching_class_id == teaching_class_id,
            IpAllocation.reservation_key.in_(reservation_keys),  # type: ignore[union-attr]
        )
    ).all()
    result = {
        str(row.reservation_key): row.ip_address
        for row in existing_rows
        if row.reservation_key
    }
    missing = [key for key in reservation_keys if key not in result]
    if not missing:
        return result

    network = ipaddress.IPv4Network(config.cidr, strict=False)
    allocated = set(session.exec(select(IpAllocation.ip_address)).all())
    available = [str(ip) for ip in network.hosts() if str(ip) not in allocated]
    if len(available) < len(missing):
        raise ConflictError(
            t("ipManagement.insufficientIpsForReservation", needed=len(missing), available=len(available))
        )
    for key, ip_address in zip(
        missing, available[: len(missing)], strict=True
    ):
        session.add(
            IpAllocation(
                ip_address=ip_address,
                purpose=(
                    "class_reserved"
                    if teaching_class_id is not None
                    else "quick_practice_reserved"
                ),
                reservation_key=key,
                teaching_class_id=teaching_class_id,
                description=(
                    f"班級預留 {key}"
                    if teaching_class_id is not None
                    else f"快速練習預留 {key}"
                ),
            )
        )
        result[key] = ip_address
    session.flush()
    return result


def release_class_reservations(session: Session, teaching_class_id) -> int:
    rows = session.exec(
        select(IpAllocation).where(
            IpAllocation.teaching_class_id == teaching_class_id,
            IpAllocation.vmid.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def release_reservations_by_prefix(session: Session, prefix: str) -> int:
    """Release unused group reservations after rollback or final reclaim."""
    rows = session.exec(
        select(IpAllocation).where(
            IpAllocation.reservation_key.startswith(prefix),  # type: ignore[union-attr]
            IpAllocation.vmid.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    for row in rows:
        session.delete(row)
    session.flush()
    return len(rows)


def allocate_ip(
    session: Session,
    vmid: int,
    purpose: str,
    *,
    reservation_key: str | None = None,
) -> str:
    """為 VM/LXC 分配下一個可用 IP。

    使用 SELECT FOR UPDATE 鎖定 subnet_config 防止並發衝突，
    ip_allocation 表的 UNIQUE 約束作為最終保障。
    """
    # 鎖定 subnet_config 確保串行化
    config = session.exec(
        select(SubnetConfig).where(SubnetConfig.id == 1).with_for_update()
    ).first()
    if config is None:
        raise BadRequestError(t("ipManagement.subnetNotConfigured"))

    if reservation_key:
        reserved = session.exec(
            select(IpAllocation)
            .where(IpAllocation.reservation_key == reservation_key)
            .with_for_update()
        ).first()
        if reserved is None:
            raise ConflictError(t("ipManagement.reservedIpNotFound"))
        if reserved.vmid not in (None, vmid):
            if not _reclaim_stale_class_reservation(session, reserved):
                raise ConflictError(t("ipManagement.reservedIpAlreadyUsed"))
        reserved.vmid = vmid
        reserved.resource_vmid = (
            vmid if session.get(Resource, vmid) is not None else None
        )
        reserved.purpose = purpose
        reserved.description = f"VMID {vmid}（課程預留）"
        session.add(reserved)
        session.flush()
        return reserved.ip_address

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

    raise ConflictError(t("ipManagement.ipPoolExhausted"))


def _reclaim_stale_class_reservation(
    session: Session, reserved: IpAllocation
) -> bool:
    """回收一次已被錯誤 VMID 綁定、但沒有對應資源的課程預留列。

    早期批次回滾只以 VMID 查詢，可能把 A 任務的預留列留在 B 的 VMID
    上。這裡只在能證明該 VMID 已屬於同班同學生、但**不同邏輯節點**的
    已登記資源時自動修復；查不到完整關係就維持原本的衝突保護，避免把
    仍在使用的 IP 誤釋放。
    """
    if (
        not reserved.reservation_key
        or reserved.teaching_class_id is None
        or reserved.resource_vmid is not None
        or reserved.vmid is None
    ):
        return False

    class_raw, separator, remainder = reserved.reservation_key.partition(":")
    node_key, user_separator, user_raw = remainder.rpartition(":")
    if not separator or not user_separator or not node_key:
        return False
    try:
        class_id = uuid.UUID(class_raw)
        user_id = uuid.UUID(user_raw)
    except ValueError:
        return False
    if class_id != reserved.teaching_class_id:
        return False

    resource = session.get(Resource, reserved.vmid)
    if resource is None:
        return False
    if (
        resource.teaching_class_id != reserved.teaching_class_id
        or resource.user_id != user_id
        or resource.batch_job_id is None
    ):
        return False

    # Local imports keep this low-level network service independent from the
    # batch service's module import order.
    from app.models import BatchProvisionJob, TeachingClassMachineNode

    job = session.get(BatchProvisionJob, resource.batch_job_id)
    if job is None or job.teaching_class_id != reserved.teaching_class_id:
        return False
    node = session.exec(
        select(TeachingClassMachineNode).where(
            TeachingClassMachineNode.batch_job_id == job.id,
            TeachingClassMachineNode.node_key == node_key,
        )
    ).first()
    if node is not None:
        # The live resource belongs to this logical node; the reservation is
        # not stale even if the batch job itself was retried.
        return False

    reserved.vmid = None
    reserved.resource_vmid = None
    reserved.purpose = "class_reserved"
    reserved.description = f"班級預留 {reserved.reservation_key}"
    session.add(reserved)
    session.flush()
    logger.warning(
        "Reclaimed stale class IP reservation key=%s bound to unrelated VMID=%s",
        reserved.reservation_key,
        resource.vmid,
    )
    return True


def link_ip_to_resource(
    session: Session,
    vmid: int,
    *,
    reservation_key: str | None = None,
) -> bool:
    """將成功建立的資源寫回 allocation 的正式 FK。"""
    conditions = [IpAllocation.vmid == vmid]
    if reservation_key is not None:
        conditions.append(IpAllocation.reservation_key == reservation_key)
    allocation = session.exec(select(IpAllocation).where(*conditions)).first()
    if allocation is None:
        return False
    allocation.resource_vmid = vmid
    session.add(allocation)
    session.flush()
    return True


def release_ip(
    session: Session,
    vmid: int,
    *,
    restore_reservation: bool = False,
    reservation_key: str | None = None,
) -> str | None:
    """釋放指定 VMID 的 IP 分配，回傳被釋放的 IP 或 None。

    批次課程會讓多個候選任務短暫使用同一個 VMID。回滾時若只用
    ``vmid`` 查詢，可能釋放到另一個任務的預留列；有穩定預留鍵時必須
    同時比對鍵與 VMID，讓失敗任務只能回收自己成功寫入的那一列。
    """
    conditions = [IpAllocation.vmid == vmid]
    if reservation_key is not None:
        conditions.append(IpAllocation.reservation_key == reservation_key)
    alloc = session.exec(select(IpAllocation).where(*conditions)).first()
    if alloc is None:
        logger.debug("VMID %s 無 IP 分配記錄，跳過釋放", vmid)
        return None

    ip = alloc.ip_address
    if restore_reservation and alloc.reservation_key:
        alloc.vmid = None
        alloc.resource_vmid = None
        alloc.purpose = "class_reserved"
        alloc.description = f"班級預留 {alloc.reservation_key}"
        session.add(alloc)
    else:
        session.delete(alloc)
    session.flush()
    _forget_ssh_host_key(ip)
    logger.info("已釋放 VMID %s 的 IP %s", vmid, ip)
    return ip


def _forget_ssh_host_key(ip: str) -> None:
    """IP 回收後清除 pinned SSH host key，避免新主機因 key 不符被拒連。"""
    try:
        from app.infrastructure.ssh import forget_host_key  # noqa: PLC0415

        forget_host_key(ip)
    except Exception:
        logger.warning("清除 IP %s 的 SSH host key 失敗（非致命）", ip, exc_info=True)


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

    used = session.exec(select(func.count()).select_from(IpAllocation)).one()
    return {"total": total, "used": used, "available": max(0, total - used)}


# ─── 防護 ──────────────────────────────────────────────────────────────────


def ensure_subnet_configured(session: Session) -> SubnetConfig:
    """斷言子網已設定。若未設定則 raise BadRequestError。

    同時回傳 config 方便呼叫者使用。
    """
    config = get_subnet_config(session)
    if config is None:
        raise BadRequestError(t("ipManagement.subnetNotConfigured"))
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
