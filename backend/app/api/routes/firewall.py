"""防火牆管理 API 路由"""

import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.api.deps import (
    AdminUser,
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
    check_firewall_access,
)
from app.core.authorizers import can_bypass_resource_ownership
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import AuditAction
from app.repositories import firewall_layout as layout_repo
from app.repositories import nat_rule as nat_repo
from app.schemas import Message
from app.schemas.firewall import (
    ClassExposureCreate,
    ClassExposurePublic,
    ClassExposureUpdate,
    ConnectionCreate,
    ConnectionDelete,
    FirewallOptionsPublic,
    FirewallRuleCreate,
    FirewallRulePublic,
    FirewallRuleUpdate,
    LayoutUpdate,
    NATRulePublic,
    PublishedService,
    PublishedServiceCreate,
    PublishedServiceRef,
    PublishedServiceUpdate,
    TopologyResponse,
)
from app.services.network import (
    class_exposure_service,
    firewall_service,
    nat_service,
)
from app.services.resource.access import require_resource_management
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
        raise HTTPException(status_code=502, detail=t("firewall.proxmox_unavailable"))
    except Exception:
        logger.exception("取得拓撲失敗")
        raise HTTPException(status_code=500, detail=t("firewall.get_topology_failed"))


# ─── 佈局管理 ──────────────────────────────────────────────────────────────────


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
    return Message(message=t("firewall.layout_saved"))


# ─── 連線管理（高階）─────────────────────────────────────────────────────────


@router.post("/connections", response_model=Message)
def create_connection(
    conn: ConnectionCreate,
    session: SessionDep,
    current_user: CurrentUser,
):
    """建立 VM 間連線（或 VM 到網關、Internet 入站）

    權限是非對稱的：
    - 來源 VM 一定要有管理權（規則寫在自己的機器上）
    - 目標 VM 有管理權直接過；沒有的話，只要它是老師開放給我所屬班級的
      機器、埠在允許範圍內、方向單向，也放行（老師事先同意，見
      class_exposure_service）
    - Internet 入站（source=None）只看目標的管理權
    """
    try:
        if conn.source_vmid is not None:
            check_firewall_access(
                vmid=conn.source_vmid,
                current_user=current_user,
                session=session,
            )
        if conn.target_vmid is not None:
            if conn.source_vmid is None:
                check_firewall_access(
                    vmid=conn.target_vmid,
                    current_user=current_user,
                    session=session,
                )
            else:
                class_exposure_service.require_connection_target(
                    session=session,
                    user=current_user,
                    target_vmid=conn.target_vmid,
                    ports=conn.ports,
                    direction=conn.direction,
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
        return Message(message=t("firewall.connection_created"))
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
    """刪除 VM 間連線

    拆掉的是 ACCEPT，只會讓兩邊更嚴，所以管理任一端就可以拆：
    老師能拆學生連進來的線，學生也能拆自己連到老師機器的線。
    Internet 入站（source=None）看目標的管理權。
    """
    try:
        if conn.source_vmid is None:
            if conn.target_vmid is not None:
                check_firewall_access(
                    vmid=conn.target_vmid,
                    current_user=current_user,
                    session=session,
                )
        elif conn.target_vmid is None:
            check_firewall_access(
                vmid=conn.source_vmid,
                current_user=current_user,
                session=session,
            )
        else:
            class_exposure_service.require_connection_delete(
                session=session,
                user=current_user,
                source_vmid=conn.source_vmid,
                target_vmid=conn.target_vmid,
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
        return Message(message=t("firewall.connection_deleted"))
    except (BadRequestError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 開放給班級（老師機器 → 學生可連） ─────────────────────────────────────────


@router.get("/{vmid}/class-exposures", response_model=list[ClassExposurePublic])
def list_class_exposures(
    vmid: int,
    session: SessionDep,
    current_user: CurrentUser,
) -> list[ClassExposurePublic]:
    """這台機器開放給哪些班級（只有能管這台機器的人看得到）"""
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return class_exposure_service.list_exposures(session=session, vmid=vmid)


@router.post(
    "/{vmid}/class-exposures", response_model=ClassExposurePublic, status_code=201
)
def create_class_exposure(
    vmid: int,
    body: ClassExposureCreate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassExposurePublic:
    """把這台機器的指定埠開放給一個自己的班級"""
    result = class_exposure_service.create_exposure(
        session=session,
        user=current_user,
        vmid=vmid,
        class_id=body.class_id,
        ports=body.ports,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_rule_create,
        details=(
            f"Class exposure: vmid={vmid} class={body.class_id} "
            f"ports={[f'{p.port}/{p.protocol}' for p in result.ports]}"
        ),
    )
    return result


@router.put(
    "/{vmid}/class-exposures/{exposure_id}", response_model=ClassExposurePublic
)
def update_class_exposure(
    vmid: int,
    exposure_id: uuid.UUID,
    body: ClassExposureUpdate,
    session: SessionDep,
    current_user: CurrentUser,
) -> ClassExposurePublic:
    """改允許的埠；被拿掉的埠，學生已連上的規則一併清掉"""
    result = class_exposure_service.update_exposure(
        session=session,
        user=current_user,
        vmid=vmid,
        exposure_id=exposure_id,
        ports=body.ports,
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_rule_update,
        details=(
            f"Class exposure updated: vmid={vmid} class={result.class_id} "
            f"ports={[f'{p.port}/{p.protocol}' for p in result.ports]}"
        ),
    )
    return result


@router.delete("/{vmid}/class-exposures/{exposure_id}", response_model=Message)
def delete_class_exposure(
    vmid: int,
    exposure_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
) -> Message:
    """關閉開放，並拆掉班上學生連進來的連線"""
    removed = class_exposure_service.delete_exposure(
        session=session, user=current_user, vmid=vmid, exposure_id=exposure_id
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_rule_delete,
        details=f"Class exposure removed: vmid={vmid} id={exposure_id} pruned={removed}",
    )
    return Message(message=t("firewall.exposure_deleted", count=removed))


# ─── 單一 VM 防火牆規則 ───────────────────────────────────────────────────────


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
    require_resource_management(session=session, user=current_user, vmid=vmid)
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
        return Message(message=t("firewall.rule_created"))
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
    require_resource_management(session=session, user=current_user, vmid=vmid)
    try:
        rules = firewall_service.get_vm_firewall_rules(
            resource_info["node"], vmid, resource_info["type"]
        )
        target_rule = next((r for r in rules if r.get("pos") == pos), None)
        if target_rule and str(target_rule.get("comment", "")).startswith("SkyLab:"):
            raise HTTPException(
                status_code=400,
                detail=t("firewall.rule_managed_no_modify"),
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
        return Message(message=t("firewall.rule_updated"))
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
    require_resource_management(session=session, user=current_user, vmid=vmid)
    try:
        # 先取得規則確認不是 SkyLab 管理的規則
        rules = firewall_service.get_vm_firewall_rules(
            resource_info["node"], vmid, resource_info["type"]
        )
        target_rule = next((r for r in rules if r.get("pos") == pos), None)
        if target_rule and str(target_rule.get("comment", "")).startswith("SkyLab:"):
            raise HTTPException(
                status_code=400,
                detail=t("firewall.rule_managed_use_connection_ui"),
            )
        firewall_service.delete_rule_by_pos(resource_info["node"], vmid, resource_info["type"], pos)
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            vmid=vmid,
            action=AuditAction.firewall_rule_delete,
            details=f"Deleted firewall rule pos={pos} on VM {vmid}",
        )
        return Message(message=t("firewall.rule_deleted"))
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
    """列出 NAT 端口轉發規則。

    可見範圍與拓撲一致：admin 全部；老師含自己班級的學生機器；其餘只看自己的 VM。
    """
    from app.services.resource import access as resource_access  # noqa: PLC0415

    rules = nat_repo.list_rules(session)
    if can_bypass_resource_ownership(current_user):
        visible_rules = rules
    else:
        visible_vmids = resource_access.list_reachable_vmids(
            session=session, user=current_user
        )
        visible_rules = [r for r in rules if r.vmid in visible_vmids]

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
        raise HTTPException(status_code=400, detail=t("firewall.invalid_rule_id"))

    rule = nat_repo.get_rule(session, rule_uuid)
    if rule is None:
        raise HTTPException(status_code=404, detail=t("firewall.nat_rule_not_found"))

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
        return Message(message=t("firewall.nat_rule_deleted"))
    except ProxmoxError as e:
        logger.error(f"Proxmox error removing NAT rule {rule_id}: {e}")
        raise HTTPException(
            status_code=502, detail=t("firewall.proxmox_operation_failed")
        )
    except Exception:
        logger.exception(f"Failed to remove NAT rule {rule_id}")
        raise HTTPException(
            status_code=500, detail=t("firewall.delete_nat_rule_failed")
        )


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
        return Message(message=t("firewall.nat_rules_synced"))
    except ProxmoxError as e:
        logger.error(f"Proxmox error syncing NAT rules: {e}")
        raise HTTPException(
            status_code=502, detail=t("firewall.proxmox_operation_failed")
        )
    except Exception:
        logger.exception("Failed to sync NAT rules")
        raise HTTPException(
            status_code=500, detail=t("firewall.sync_nat_rules_failed")
        )


# ─── 單台 VM：迷你拓撲與對外服務 ──────────────────────────────────────────────


@router.get("/{vmid}/topology", response_model=TopologyResponse)
def get_vm_topology(
    vmid: int,
    session: SessionDep,
    _resource_info: ResourceInfoDep,
):
    """以這台 VM 為中心的迷你拓撲（Internet、這台 VM、與它有連線的其他 VM）"""
    try:
        return firewall_service.get_vm_topology(vmid, session)
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{vmid}/services", response_model=list[PublishedService])
def list_published_services(
    vmid: int,
    session: SessionDep,
    _resource_info: ResourceInfoDep,
):
    """列出這台 VM 的對外服務（對外網址 / port 轉發 / 僅開放防火牆）"""
    try:
        return firewall_service.list_vm_published_services(vmid, session)
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{vmid}/services", response_model=PublishedService)
def publish_service(
    vmid: int,
    body: PublishedServiceCreate,
    session: SessionDep,
    current_user: CurrentUser,
    _resource_info: ResourceInfoDep,
):
    """發布一條對外服務：先開 Proxmox 防火牆，再依模式套反向代理或 NAT"""
    require_resource_management(session=session, user=current_user, vmid=vmid)
    try:
        service = firewall_service.publish_vm_service(vmid, body, session)
    except (BadRequestError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_connection_create,
        details=(
            f"Published service on VM {vmid}: {body.port}/{body.protocol} "
            f"mode={body.mode} domain={body.domain} external_port={body.external_port}"
        ),
    )
    return service


@router.put("/{vmid}/services", response_model=PublishedService)
def replace_published_service(
    vmid: int,
    body: PublishedServiceUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    _resource_info: ResourceInfoDep,
):
    """把一條服務換成新的發布方式（先撤下舊的再重新發布）"""
    require_resource_management(session=session, user=current_user, vmid=vmid)
    try:
        service = firewall_service.replace_vm_service(
            vmid, body.current, body.replacement, session
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BadRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_connection_create,
        details=(
            f"Replaced published service on VM {vmid}: "
            f"{body.current.port}/{body.current.protocol} -> "
            f"{body.replacement.port}/{body.replacement.protocol} "
            f"mode={body.replacement.mode} domain={body.replacement.domain} "
            f"external_port={body.replacement.external_port}"
        ),
    )
    return service


@router.delete("/{vmid}/services", response_model=Message)
def unpublish_service(
    vmid: int,
    body: PublishedServiceRef,
    session: SessionDep,
    current_user: CurrentUser,
    _resource_info: ResourceInfoDep,
):
    """撤下一條對外服務（刪防火牆入站規則並清 NAT / 反向代理）"""
    require_resource_management(session=session, user=current_user, vmid=vmid)
    try:
        firewall_service.unpublish_vm_service(vmid, body, session)
    except (BadRequestError, NotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProxmoxError as e:
        raise HTTPException(status_code=502, detail=str(e))
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        vmid=vmid,
        action=AuditAction.firewall_connection_delete,
        details=f"Unpublished service on VM {vmid}: {body.port}/{body.protocol}",
    )
    return Message(message=t("firewall.serviceUnpublished"))


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
