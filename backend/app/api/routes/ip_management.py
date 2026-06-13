"""IP 管理 API — 子網配置與 IP 分配查詢（僅管理員）"""

import logging

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.schemas.common import Message
from app.schemas.ip_management import (
    IpAllocationListResponse,
    IpAllocationPublic,
    SubnetConfigCreate,
    SubnetConfigPublic,
    SubnetStatusResponse,
)
from app.services.network import ip_management_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ip-management", tags=["ip-management"])


# ─── 子網配置 ──────────────────────────────────────────────────────────────────


@router.get("/subnet", response_model=SubnetConfigPublic | None)
def get_subnet_config(session: SessionDep, _: AdminUser):
    """取得子網配置"""
    config = ip_management_service.get_subnet_config(session)
    if config is None:
        return None
    stats = ip_management_service.get_ip_stats(session)
    return SubnetConfigPublic(
        cidr=config.cidr,
        gateway=config.gateway,
        bridge_name=config.bridge_name,
        gateway_vm_ip=config.gateway_vm_ip,
        dns_servers=config.dns_servers,
        extra_blocked_subnets=ip_management_service.get_extra_blocked_subnets(config),
        updated_at=config.updated_at,
        total_ips=stats["total"],
        used_ips=stats["used"],
        available_ips=stats["available"],
    )


@router.put("/subnet", response_model=SubnetConfigPublic)
def upsert_subnet_config(
    session: SessionDep,
    _: AdminUser,
    body: SubnetConfigCreate,
):
    """設定或更新子網配置"""
    config = ip_management_service.upsert_subnet_config(
        session,
        cidr=body.cidr,
        gateway=body.gateway,
        bridge_name=body.bridge_name,
        gateway_vm_ip=body.gateway_vm_ip,
        dns_servers=body.dns_servers,
        extra_blocked_subnets=body.extra_blocked_subnets,
    )
    # 同步所有 VM/LXC 的封鎖規則 dest 為新子網與額外封鎖網段
    try:
        from app.services.network import firewall_service  # noqa: PLC0415
        firewall_service.sync_block_local_subnet_rules()
    except Exception as e:
        logger.warning("同步預設封鎖防火牆規則失敗（非致命）: %s", e)
    stats = ip_management_service.get_ip_stats(session)
    return SubnetConfigPublic(
        cidr=config.cidr,
        gateway=config.gateway,
        bridge_name=config.bridge_name,
        gateway_vm_ip=config.gateway_vm_ip,
        dns_servers=config.dns_servers,
        extra_blocked_subnets=ip_management_service.get_extra_blocked_subnets(config),
        updated_at=config.updated_at,
        total_ips=stats["total"],
        used_ips=stats["used"],
        available_ips=stats["available"],
    )


@router.delete("/subnet", response_model=Message)
def delete_subnet_config(session: SessionDep, _: AdminUser):
    """刪除子網配置（需先移除所有 VM/LXC IP 分配）"""
    ip_management_service.delete_subnet_config(session)
    return Message(message="子網配置已刪除")


# ─── IP 分配查詢 ───────────────────────────────────────────────────────────────


@router.get("/allocations", response_model=IpAllocationListResponse)
def list_allocations(session: SessionDep, _: AdminUser):
    """列出所有 IP 分配記錄"""
    allocs = ip_management_service.get_all_allocations(session)
    items = [
        IpAllocationPublic(
            ip_address=a.ip_address,
            purpose=a.purpose,
            vmid=a.vmid,
            description=a.description,
            allocated_at=a.allocated_at,
        )
        for a in allocs
    ]
    return IpAllocationListResponse(allocations=items, total=len(items))


# ─── 狀態 ─────────────────────────────────────────────────────────────────────


@router.get("/status", response_model=SubnetStatusResponse)
def get_subnet_status(session: SessionDep, _: CurrentUser):
    """取得子網配置狀態（所有登入使用者可查詢）"""
    config = ip_management_service.get_subnet_config(session)
    if config is None:
        return SubnetStatusResponse(configured=False)
    stats = ip_management_service.get_ip_stats(session)
    return SubnetStatusResponse(
        configured=True,
        cidr=config.cidr,
        bridge_name=config.bridge_name,
        total_ips=stats["total"],
        used_ips=stats["used"],
        available_ips=stats["available"],
    )
