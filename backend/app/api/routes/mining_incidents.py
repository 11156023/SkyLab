"""反挖礦事件 API（admin）：清單、停權、誤判解除、資源豁免。"""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, SessionDep
from app.models import MiningIncidentStatus
from app.repositories import mining as mining_repo
from app.schemas.mining import (
    MiningDismissRequest,
    MiningDismissResult,
    MiningExemptRequest,
    MiningExemptResponse,
    MiningIncidentPublic,
)
from app.services.security import mining_service

router = APIRouter(prefix="/mining-incidents", tags=["mining"])


@router.get("", response_model=list[MiningIncidentPublic])
def list_incidents(
    session: SessionDep,
    _: AdminUser,
    status: MiningIncidentStatus | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[MiningIncidentPublic]:
    """挖礦事件列表（可依狀態過濾）。"""
    incidents = mining_repo.list_incidents(
        session=session, status=status, limit=limit
    )
    return [
        MiningIncidentPublic.model_validate(i, from_attributes=True)
        for i in incidents
    ]


@router.post("/{incident_id}/ban", response_model=MiningIncidentPublic)
def ban_incident(
    incident_id: uuid.UUID,
    session: SessionDep,
    current_user: AdminUser,
) -> MiningIncidentPublic:
    """管理員確認挖礦 → 停權帳號（VM 維持暫停，留存證據）。"""
    incident = mining_service.ban_incident(
        session=session, incident_id=incident_id, admin=current_user
    )
    return MiningIncidentPublic.model_validate(incident, from_attributes=True)


@router.post("/{incident_id}/dismiss", response_model=MiningDismissResult)
def dismiss_incident(
    incident_id: uuid.UUID,
    body: MiningDismissRequest,
    session: SessionDep,
    current_user: AdminUser,
) -> MiningDismissResult:
    """管理員判定誤判 → 恢復 VM，可一併加入豁免。

    恢復 VM／刪除存證快照失敗不擋結案，逐條放在 ``warnings`` 回給前端。
    """
    incident, failures = mining_service.dismiss_incident(
        session=session,
        incident_id=incident_id,
        admin=current_user,
        exempt=body.exempt,
        note=body.note,
    )
    public = MiningIncidentPublic.model_validate(incident, from_attributes=True)
    return MiningDismissResult(**public.model_dump(), warnings=failures)


@router.put("/exemptions/{vmid}", response_model=MiningExemptResponse)
def set_exemption(
    vmid: int,
    body: MiningExemptRequest,
    session: SessionDep,
    current_user: AdminUser,
) -> MiningExemptResponse:
    """設定/解除資源的挖礦偵測豁免（合法長時間高負載的 VM）。"""
    resource = mining_service.set_exemption(
        session=session, vmid=vmid, exempt=body.exempt, admin=current_user
    )
    return MiningExemptResponse(vmid=vmid, exempt=resource.mining_exempt)
