"""防火牆管理 API 路由"""

import logging

from fastapi import APIRouter, HTTPException

from app.api.deps import (
    AdminUser,
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
    check_firewall_access,
)
from app.core.authorizers import can_bypass_resource_ownership
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import AuditAction
from app.repositories import firewall_layout as layout_repo
from app.repositories import nat_rule as nat_repo
from app.repositories import reverse_proxy as rp_repo
from app.schemas import Message
from app.schemas.firewall import (
    ConnectionCreate,
    ConnectionDelete,
    FirewallOptionsPublic,
    FirewallRuleCreate,
    FirewallRulePublic,
    FirewallRuleUpdate,
    LayoutUpdate,
    NATRulePublic,
    ReverseProxyRulePublic,
    TopologyResponse,
)
from app.services.network import firewall_service, nat_service, reverse_proxy_service
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firewall", tags=["firewall"])


# ─── 拓撲 ─────────────────────────────────────────────────────────────────────


@router.get("/topology", response_model=TopologyResponse)
def get_topology(session: SessionDep, current_user: CurrentUser):
    """取得當前使用者有權限的 VM 防火牆拓撲（節點 + 連線）"""
    try:
        return firewall_service.get_topology(user=current_user, session=session)
    except (NotFoundError, BadRequestError) as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except ProxmoxError as e:
        logger.error(f"Proxmox error in get_topology: {e}")
        raise HTTPException(status_code=502, detail="Proxmox 服務不可用")
    except Exception:
        logger.exception("取得拓撲失敗")
        raise HTTPException(status_code=500, detail="取得拓撲失敗")


# ─── 佈局管理 ──────────────────────────────────────────────────────────────────


@router.get("/layout")
def get_layout(session: SessionDep, current_user: CurrentUser):
    """取得使用者儲存的圖形佈局節點位置"""
    records = layout_repo.get_layout(session=session, user_id=current_user.id)
    return [
        {
            "vmid": r.vmid,
            "node_type": r.node_type,
            "position_x": r.position_x,
            "position_y": r.position_y,
        }
        for r in records
    ]


@router.put("/layout", response_model=Message)
def save_layout(
    layout_update: LayoutUpdate,
    session: SessionDep,
    current_user: CurrentUser,
):
    """批次儲存圖形佈局節點位置"""
    nodes = [
        {
            "vmid": node.vmid,
            "node_type": node.node_type,
            "position_x": node.position_x,
            "position_y": node.position_y,
        }
        for node in layout_update.nodes
    ]
    layout_repo.upsert_layout_batch(
        session=session, user_id=current_user.id, nodes=nodes
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.firewall_layout_update,
        details=f"Saved firewall layout ({len(nodes)} nodes)",
    )
    return Message(message="佈局已儲存")


# ─── 連線管理（高階）─────────────────────────────────────────────────────────


@router.post("/connections", response_model=Message)
def create_connection(
    conn: ConnectionCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    """建立 VM 間連線（或 VM 到網關）

    - 來源 VM 必須為當前使用者有權限的機器
    - 目標 VM（如果有）也必須在當前使用者的可見範圍內
    """
    try:
        # 權限檢查：來源 VM（若有）
        if conn.source_vmid is not None:
            check_firewall_access(
                vmid=conn.source_vmid,
                current_user=current_user,
                session=session,
            )
        # 權限檢查：目標 VM（若有）
        if conn.target_vmid is not None:
            check_firewall_access(
                vmid=conn.target_vmid,
                current_user=current_user,
                session=session,
            )

        firewall_service.create_connection(
            source_vmid=conn.source_vmid,
            target_vmid=conn.target_vmid,
            ports=conn.ports,
            direction=conn.direction,
            session=session,
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=conn.source_vmid or conn.target_vmid,
            action=AuditAction.firewall_connection_create,
            details=(
                f"Firewall connection: src={conn.source_vmid} → "
                f"dst={conn.target_vmid} ports={conn.ports} dir={conn.direction}"
            ),
        )
        return Message(message="連線已建立")
    except (BadRequestError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/connections", response_model=Message)
def delete_connection(
    conn: ConnectionDelete,
    session: SessionDep,
    current_user: CurrentUser,
):
    """刪除 VM 間連線"""
    try:
        # 權限檢查：
        # - 若 source_vmid 為 None（例如 Internet -> VM 入站），
        #   則以 target_vmid 作為被變更規則的 VM 進行檢查
        # - 否則先檢查 source_vmid，再檢查（若有的）target_vmid
        if conn.source_vmid is None:
            if conn.target_vmid is not None:
                check_firewall_access(
                    vmid=conn.target_vmid,
                    current_user=current_user,
                    session=session,
                )
        else:
            check_firewall_access(
                vmid=conn.source_vmid,
                current_user=current_user,
                session=session,
            )
            if conn.target_vmid is not None:
                check_firewall_access(
                    vmid=conn.target_vmid,
                    current_user=current_user,
                    session=session,
                )

        firewall_service.delete_connection(
            source_vmid=conn.source_vmid,
            target_vmid=conn.target_vmid,
            ports=conn.ports,
            session=session,
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=conn.source_vmid or conn.target_vmid,
            action=AuditAction.firewall_connection_delete,
            details=(
                f"Deleted firewall connection: src={conn.source_vmid} → "
                f"dst={conn.target_vmid} ports={conn.ports}"
            ),
        )
        return Message(message="連線已刪除")
    except (BadRequestError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 單一 VM 防火牆規則（原始 CRUD）─────────────────────────────────────────


@router.get("/{vmid}/rules", response_model=list[FirewallRulePublic])
def list_rules(
    vmid: int,
    resource_info: ResourceInfoDep,
):
    """列出 VM 防火牆規則（包含 SkyLab 管理的規則）"""
    try:
        rules = firewall_service.get_vm_firewall_rules(
            resource_info["node"], vmid, resource_info["type"]
        )
        return [
            FirewallRulePublic(
                pos=r.get("pos", i),
                type=r.get("type", "in"),
                action=r.get("action", "DROP"),
                source=r.get("source"),
                dest=r.get("dest"),
                proto=r.get("proto"),
                dport=r.get("dport"),
                sport=r.get("sport"),
                enable=r.get("enable", 1),
                comment=r.get("comment"),
                is_managed=bool(
                    r.get("comment", "").startswith("SkyLab:")
                    if r.get("comment")
                    else False
                ),
            )
            for i, r in enumerate(rules)
        ]
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{vmid}/rules", response_model=Message)
def create_rule(
    vmid: int,
    rule: FirewallRuleCreate,
    session: SessionDep,
    current_user: CurrentUser,
    resource_info: ResourceInfoDep,
):
    """在 VM 上建立防火牆規則"""
    try:
        rule_dict = {k: v for k, v in rule.model_dump().items() if v is not None}
        firewall_service.create_rule(resource_info["node"], vmid, resource_info["type"], rule_dict)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=vmid,
            action=AuditAction.firewall_rule_create,
            details=f"Created firewall rule on VM {vmid}: {rule_dict}",
        )
        return Message(message="規則已建立")
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{vmid}/rules/{pos}", response_model=Message)
def update_rule(
    vmid: int,
    pos: int,
    rule: FirewallRuleUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    resource_info: ResourceInfoDep,
):
    """更新 VM 防火牆規則（不可修改 SkyLab 管理的規則）"""
    try:
        rules = firewall_service.get_vm_firewall_rules(
            resource_info["node"], vmid, resource_info["type"]
        )
        target_rule = next((r for r in rules if r.get("pos") == pos), None)
        if target_rule and str(target_rule.get("comment", "")).startswith("SkyLab:"):
            raise HTTPException(
                status_code=400,
                detail="此規則由 SkyLab 管理，不可修改",
            )
        rule_dict = {k: v for k, v in rule.model_dump().items() if v is not None}
        firewall_service.update_rule(
            resource_info["node"], vmid, resource_info["type"], pos, rule_dict
        )
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=vmid,
            action=AuditAction.firewall_rule_update,
            details=f"Updated firewall rule pos={pos} on VM {vmid}: {rule_dict}",
        )
        return Message(message="規則已更新")
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{vmid}/rules/{pos}", response_model=Message)
def delete_rule(
    vmid: int,
    pos: int,
    session: SessionDep,
    current_user: CurrentUser,
    resource_info: ResourceInfoDep,
):
    """刪除 VM 防火牆規則（不可刪除 SkyLab 管理的規則，請使用連線刪除 API）"""
    try:
        # 先取得規則確認不是 SkyLab 管理的規則
        rules = firewall_service.get_vm_firewall_rules(
            resource_info["node"], vmid, resource_info["type"]
        )
        target_rule = next((r for r in rules if r.get("pos") == pos), None)
        if target_rule and str(target_rule.get("comment", "")).startswith("SkyLab:"):
            raise HTTPException(
                status_code=400,
                detail="此規則由 SkyLab 管理，請使用連線管理介面進行操作",
            )
        firewall_service.delete_rule_by_pos(resource_info["node"], vmid, resource_info["type"], pos)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=vmid,
            action=AuditAction.firewall_rule_delete,
            details=f"Deleted firewall rule pos={pos} on VM {vmid}",
        )
        return Message(message="規則已刪除")
    except HTTPException:
        raise
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── NAT 端口轉發管理 ──────────────────────────────────────────────────────────


@router.get("/nat-rules", response_model=list[NATRulePublic])
def list_nat_rules(
    session: SessionDep,
    current_user: CurrentUser,
):
    """列出所有 NAT 端口轉發規則（僅 superuser 可查看所有；一般使用者只看自己的 VM）"""
    from app.repositories import resource as resource_repo  # noqa: PLC0415

    rules = nat_repo.list_rules(session)
    if can_bypass_resource_ownership(current_user):
        visible_rules = rules
    else:
        own_resources = resource_repo.get_resources_by_user(
            session=session, user_id=current_user.id
        )
        own_vmids = {r.vmid for r in own_resources}
        visible_rules = [r for r in rules if r.vmid in own_vmids]

    return [
        NATRulePublic(
            id=r.id,
            ssh_host=r.ssh_host,
            vmid=r.vmid,
            vm_ip=r.vm_ip,
            external_port=r.external_port,
            internal_port=r.internal_port,
            protocol=r.protocol,
            created_at=r.created_at,
        )
        for r in visible_rules
    ]


@router.delete("/nat-rules/{rule_id}", response_model=Message)
def delete_nat_rule(
    rule_id: str,
    session: SessionDep,
    current_user: CurrentUser,
):
    """刪除 NAT 端口轉發規則"""
    import uuid  # noqa: PLC0415

    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="無效的規則 ID")

    rule = nat_repo.get_rule(session, rule_uuid)
    if rule is None:
        raise HTTPException(status_code=404, detail="NAT 規則不存在")

    check_firewall_access(vmid=rule.vmid, current_user=current_user, session=session)

    try:
        nat_service.remove_nat_rule_by_id(session=session, rule_id=rule_id)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=rule.vmid,
            action=AuditAction.nat_rule_delete,
            details=(
                f"Deleted NAT rule {rule_id} (vmid={rule.vmid} "
                f"ext={rule.external_port} → int={rule.internal_port}/{rule.protocol})"
            ),
        )
        return Message(message="NAT 規則已刪除")
    except ProxmoxError as e:
        logger.error(f"Proxmox error removing NAT rule {rule_id}: {e}")
        raise HTTPException(status_code=502, detail="Proxmox 操作失敗")
    except Exception:
        logger.exception(f"Failed to remove NAT rule {rule_id}")
        raise HTTPException(status_code=500, detail="刪除 NAT 規則失敗")


@router.post("/nat-rules/sync", response_model=Message)
def sync_nat_rules(
    session: SessionDep,
    current_user: AdminUser,
):
    """手動將 DB 中的 NAT 規則同步到 Gateway VM haproxy"""
    try:
        nat_service.sync_to_gateway(session=session)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.nat_rule_sync,
            details="Manually synced NAT rules to Gateway VM",
        )
        return Message(message="NAT 規則已同步到 Gateway VM")
    except ProxmoxError as e:
        logger.error(f"Proxmox error syncing NAT rules: {e}")
        raise HTTPException(status_code=502, detail="Proxmox 操作失敗")
    except Exception:
        logger.exception("Failed to sync NAT rules")
        raise HTTPException(status_code=500, detail="同步 NAT 規則失敗")


# ─── 反向代理規則管理 ─────────────────────────────────────────────────────────


@router.get("/reverse-proxy-rules", response_model=list[ReverseProxyRulePublic])
def list_reverse_proxy_rules(
    session: SessionDep,
    current_user: CurrentUser,
):
    """列出反向代理規則"""
    from app.repositories import resource as resource_repo  # noqa: PLC0415

    rules = rp_repo.list_rules(session)
    if can_bypass_resource_ownership(current_user):
        visible_rules = rules
    else:
        own_resources = resource_repo.get_resources_by_user(
            session=session, user_id=current_user.id
        )
        own_vmids = {r.vmid for r in own_resources}
        visible_rules = [r for r in rules if r.vmid in own_vmids]

    return [
        ReverseProxyRulePublic(
            id=r.id,
            vmid=r.vmid,
            vm_ip=r.vm_ip,
            domain=r.domain,
            internal_port=r.internal_port,
            enable_https=r.enable_https,
            dns_provider=r.dns_provider,
            created_at=r.created_at,
        )
        for r in visible_rules
    ]


@router.delete("/reverse-proxy-rules/{rule_id}", response_model=Message)
def delete_reverse_proxy_rule(
    rule_id: str,
    session: SessionDep,
    current_user: CurrentUser,
):
    """刪除反向代理規則"""
    import uuid  # noqa: PLC0415

    try:
        rule_uuid = uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="無效的規則 ID")

    rule = rp_repo.get_rule(session, rule_uuid)
    if rule is None:
        raise HTTPException(status_code=404, detail="反向代理規則不存在")

    check_firewall_access(vmid=rule.vmid, current_user=current_user, session=session)

    try:
        reverse_proxy_service.remove_reverse_proxy_rule_by_id(session=session, rule_id=rule_id)
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
        return Message(message="反向代理規則已刪除")
    except ProxmoxError as e:
        logger.error(f"Proxmox error removing reverse proxy rule {rule_id}: {e}")
        raise HTTPException(status_code=502, detail="Proxmox 操作失敗")
    except Exception:
        logger.exception(f"Failed to remove reverse proxy rule {rule_id}")
        raise HTTPException(status_code=500, detail="刪除反向代理規則失敗")


@router.post("/reverse-proxy-rules/sync", response_model=Message)
def sync_reverse_proxy_rules(
    session: SessionDep,
    current_user: AdminUser,
):
    """手動將反向代理規則同步到 Gateway VM Traefik"""
    try:
        reverse_proxy_service.sync_to_gateway(session=session)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.reverse_proxy_rule_sync,
            details="Manually synced reverse proxy rules to Gateway VM",
        )
        return Message(message="反向代理規則已同步到 Gateway VM")
    except ProxmoxError as e:
        logger.error(f"Proxmox error syncing reverse proxy rules: {e}")
        raise HTTPException(status_code=502, detail="Proxmox 操作失敗")
    except Exception:
        logger.exception("Failed to sync reverse proxy rules")
        raise HTTPException(status_code=500, detail="同步反向代理規則失敗")


@router.get("/{vmid}/options", response_model=FirewallOptionsPublic)
def get_options(
    vmid: int,
    resource_info: ResourceInfoDep,
):
    """取得 VM 防火牆選項（是否啟用、預設策略）"""
    try:
        opts = firewall_service.get_firewall_options(
            resource_info["node"], vmid, resource_info["type"]
        )
        return FirewallOptionsPublic(
            enable=bool(opts.get("enable", False)),
            policy_in=opts.get("policy_in", "DROP"),
            policy_out=opts.get("policy_out", "ACCEPT"),
        )
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))
