"""防火牆佈局資料庫操作"""

import uuid
from datetime import datetime, timezone

from sqlmodel import Session, and_, select

from app.models import Resource
from app.models.firewall_layout import FirewallLayout


def get_layout(*, session: Session, user_id: uuid.UUID) -> list[FirewallLayout]:
    """取得使用者的所有節點佈局"""
    return list(
        session.exec(
            select(FirewallLayout).where(FirewallLayout.user_id == user_id)
        ).all()
    )


def get_node(
    *,
    session: Session,
    user_id: uuid.UUID,
    vmid: int | None,
    node_type: str,
) -> FirewallLayout | None:
    """取得特定節點的佈局"""
    stmt = select(FirewallLayout).where(
        and_(
            FirewallLayout.user_id == user_id,
            FirewallLayout.vmid == vmid,
            FirewallLayout.node_type == node_type,
        )
    )
    return session.exec(stmt).first()


def upsert_layout_batch(
    *,
    session: Session,
    user_id: uuid.UUID,
    nodes: list[dict],
) -> None:
    """批次更新節點位置。

    gateway 節點一律不帶 vmid；VM 節點必須對應現存資源（vmid 是 FK），
    前端送來已不存在的機器座標直接略過。
    """
    now = datetime.now(timezone.utc)
    for node_data in nodes:
        node_type = node_data["node_type"]
        vmid = None if node_type == "gateway" else node_data.get("vmid")
        if node_type != "gateway" and (
            vmid is None or session.get(Resource, vmid) is None
        ):
            continue
        position_x = node_data["position_x"]
        position_y = node_data["position_y"]

        existing = get_node(
            session=session, user_id=user_id, vmid=vmid, node_type=node_type
        )
        if existing:
            existing.position_x = position_x
            existing.position_y = position_y
            existing.updated_at = now
            session.add(existing)
        else:
            node = FirewallLayout(
                user_id=user_id,
                vmid=vmid,
                node_type=node_type,
                position_x=position_x,
                position_y=position_y,
                created_at=now,
                updated_at=now,
            )
            session.add(node)

    session.commit()

