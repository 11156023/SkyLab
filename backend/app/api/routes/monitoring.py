"""資源監控 API：全域 overview、節點/VM RRD 趨勢、警告事件、平台健康。"""

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Query, Request, Response

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.infrastructure.redis import get_redis
from app.repositories import governance as governance_repo
from app.schemas.monitoring import (
    AlertEventPublic,
    GrafanaLink,
    MonitoringOverview,
    SystemHealth,
)
from app.services.monitoring import (
    grafana_service,
    monitoring_service,
    system_health_service,
)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/system-health", response_model=SystemHealth)
async def get_system_health(_: AdminUser) -> SystemHealth:
    """平台本身的健康：DB、Redis、worker、PVE API 連線與排程任務心跳。"""
    data = await asyncio.to_thread(system_health_service.collect_system_health)
    return SystemHealth.model_validate(data)


@router.post("/grafana/session", response_model=GrafanaLink)
async def create_grafana_session(
    current_user: AdminUser, request: Request, response: Response
) -> GrafanaLink:
    """監控 stack 的 Grafana 是否啟用（連得到才回網址）；啟用時一併發免密碼登入的 cookie。"""
    link = GrafanaLink.model_validate(await grafana_service.get_grafana_link())
    if link.enabled:
        secure = request.headers.get("x-forwarded-proto") == "https" or (
            link.url or ""
        ).startswith("https://")
        response.set_cookie(
            grafana_service.SESSION_COOKIE,
            grafana_service.create_session_token(current_user),
            max_age=grafana_service.session_max_age_seconds(),
            path=grafana_service.SESSION_COOKIE_PATH,
            httponly=True,
            samesite="lax",
            secure=secure,
        )
    return link


@router.get("/grafana/auth", include_in_schema=False, status_code=204)
async def grafana_auth(
    session: SessionDep,
    token: Annotated[
        str | None, Cookie(alias=grafana_service.SESSION_COOKIE)
    ] = None,
) -> Response:
    """nginx auth_request 專用：cookie 換成 Grafana auth.proxy 的身分標頭。

    一律回 204：cookie 無效時不帶標頭，Grafana 就顯示原本的登入頁；
    回 401 的話 nginx 會把整個 /grafana/ 擋掉，連備用的帳密登入都用不了。
    """
    headers = await grafana_service.resolve_proxy_headers(session, token)
    return Response(status_code=204, headers=headers)


@router.get("/overview", response_model=MonitoringOverview)
async def get_overview(_: AdminUser) -> MonitoringOverview:
    """全域監控匯總（叢集容量/用量、節點與 VM 統計）。"""
    redis = await get_redis()
    return await monitoring_service.get_overview_cached(redis=redis)


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
    """警告事件列表（active=true 只列未解除的）。"""
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
    """確認（ack）一筆警告。"""
    alert = governance_repo.acknowledge_alert(
        session=session, alert_id=alert_id, user_id=current_user.id
    )
    return AlertEventPublic.model_validate(alert, from_attributes=True)
