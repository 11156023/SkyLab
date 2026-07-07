"""資源監控 API：全域 overview、節點/VM RRD 趨勢、告警事件。"""

import uuid
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.repositories import governance as governance_repo
from app.schemas.monitoring import AlertEventPublic, MonitoringOverview
from app.services.monitoring import monitoring_service

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/overview", response_model=MonitoringOverview)
def get_overview(_: AdminUser) -> MonitoringOverview:
    """全域監控匯總（叢集容量/用量、節點與 VM 統計）。"""
    return monitoring_service.get_overview()


@router.get("/nodes/{node}/rrd")
def get_node_rrd(
    node: str,
    _: AdminUser,
    timeframe: str = Query(default="hour"),
) -> list[dict[str, Any]]:
    """節點 RRD 趨勢（直接代理 PVE，timeframe: hour|day|week）。"""
    return monitoring_service.get_node_rrd(node, timeframe)


@router.get("/vms/{vmid}/rrd")
def get_vm_rrd(
    vmid: int,
    session: SessionDep,
    current_user: CurrentUser,
    timeframe: str = Query(default="hour"),
) -> list[dict[str, Any]]:
    """VM/LXC RRD 趨勢（擁有者或管理員）。"""
    return monitoring_service.get_vm_rrd(
        session=session, vmid=vmid, timeframe=timeframe, user=current_user
    )


@router.get("/alerts", response_model=list[AlertEventPublic])
def list_alerts(
    session: SessionDep,
    _: AdminUser,
    active: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[AlertEventPublic]:
    """告警事件列表（active=true 只列未解除的）。"""
    alerts = governance_repo.list_alerts(
        session=session, active_only=active, limit=limit
    )
    return [
        AlertEventPublic.model_validate(a, from_attributes=True) for a in alerts
    ]


@router.post("/alerts/{alert_id}/ack", response_model=AlertEventPublic)
def acknowledge_alert(
    alert_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
) -> AlertEventPublic:
    """確認（ack）一筆告警。"""
    alert = governance_repo.acknowledge_alert(
        session=session, alert_id=alert_id, user_id=current_user.id
    )
    return AlertEventPublic.model_validate(alert, from_attributes=True)
