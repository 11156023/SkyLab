"""防火牆管理 API 路由"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import (
    CurrentUser,
    ResourceInfoDep,
    SessionDep,
    check_firewall_access,
)
from app.core.i18n import t
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.models import AuditAction
from app.repositories import firewall_layout as layout_repo
from app.schemas import Message
from app.schemas.firewall import (
    ConnectionCreate,
    ConnectionDelete,
    FirewallOptionsPublic,
    FirewallRuleCreate,
    FirewallRulePublic,
    FirewallRuleUpdate,
    LayoutUpdate,
    PublishedService,
    PublishedServiceCreate,
    PublishedServiceRef,
    PublishedServiceUpdate,
    TopologyResponse,
)
from app.services.network import firewall_service
from app.services.resource.access import require_resource_management
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firewall", tags=["firewall"])


def _check_endpoint_access(
    session: SessionDep, current_user: CurrentUser, *vmids: int | None
) -> None:
    """依序對連線兩端中有指定的 VM 做防火牆權限檢查（None＝Internet／網關，略過）"""
    for vmid in vmids:
        if vmid is not None:
            check_firewall_access(
                vmid=vmid, current_user=current_user, session=session
            )


def _is_managed_rule(rule: dict[str, Any]) -> bool:
    """SkyLab 自動管理的規則（comment 以 SkyLab: 開頭），只能透過連線 UI 變更"""
    return str(rule.get("comment") or "").startswith(firewall_service._CC_PREFIX)


def _get_rule_at(
    resource_info: dict[str, Any], vmid: int, pos: int
) -> dict[str, Any]:
    """取得指定位置的規則；對不到就 404。

    規則位置是會變動的（刪一條後面全往前挪），對不到就別讓 Proxmox 去
    改／刪到剛好遞補到這個位置的別條規則。
    """
    rules = firewall_service.get_vm_firewall_rules(
        resource_info["node"], vmid, resource_info["type"]
    )
    target_rule = next((r for r in rules if r.get("pos") == pos), None)
    if target_rule is None:
        raise NotFoundError(t("firewall.rule_not_found_at_pos", pos=pos))
    return target_rule


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
    """建立 VM 間連線（或 VM 到網關）

    - 來源 VM 必須為當前使用者有權限的機器
    - 目標 VM（如果有）也必須在當前使用者的可見範圍內
    """
    try:
        _check_endpoint_access(
            session, current_user, conn.source_vmid, conn.target_vmid
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
    """刪除 VM 間連線"""
    try:
        _check_endpoint_access(
            session, current_user, conn.source_vmid, conn.target_vmid
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


# ─── 單一 VM 防火牆規則 ───────────────────────────────────────────────────────


@router.get("/{vmid}/rules", response_model=list[FirewallRulePublic])
def list_rules(
    vmid: int,
    resource_info: ResourceInfoDep,
):
    """列出 VM 防火牆規則（包含 SkyLab 管理的規則）

    取不到規則時 service 會回空清單（不丟錯），所以這裡沒有錯誤分支。
    """
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
            is_managed=_is_managed_rule(r),
        )
        for i, r in enumerate(rules)
    ]


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
        if _is_managed_rule(_get_rule_at(resource_info, vmid, pos)):
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
        # 先確認不是 SkyLab 管理的規則
        if _is_managed_rule(_get_rule_at(resource_info, vmid, pos)):
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
    except ProxmoxError as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    """取得 VM 防火牆選項（是否啟用、預設策略）

    取不到選項時 service 會回空 dict（不丟錯），這裡一律套用預設值。
    """
    opts = firewall_service.get_firewall_options(
        resource_info["node"], vmid, resource_info["type"]
    )
    return FirewallOptionsPublic(
        enable=bool(opts.get("enable", False)),
        policy_in=opts.get("policy_in", "DROP"),
        policy_out=opts.get("policy_out", "ACCEPT"),
    )
