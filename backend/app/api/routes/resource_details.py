import logging

from fastapi import APIRouter

from app.api.deps import (
    AdminUser,
    CurrentUser,
    InstructorUser,
    ResourceInfoDep,
    SessionDep,
    TeachingResourceInfoDep,
)
from app.schemas import (
    CurrentStatsResponse,
    DirectSpecUpdateRequest,
    ResetAcceptedResponse,
    RRDDataPoint,
    RRDDataResponse,
    SnapshotCreateRequest,
    SnapshotInfo,
    SnapshotResponse,
)
from app.services.network import snapshot_service
from app.services.resource import reset_service, resource_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resources", tags=["resource-details"])


# ===== Endpoints =====


@router.get("/{vmid}/current-stats", response_model=CurrentStatsResponse)
def get_current_stats(vmid: int, resource_info: ResourceInfoDep):
    stats = resource_service.get_current_stats(vmid=vmid, resource_info=resource_info)
    return CurrentStatsResponse(**stats)


@router.get("/{vmid}/stats", response_model=RRDDataResponse)
def get_rrd_stats(
    vmid: int, resource_info: ResourceInfoDep, timeframe: str = "hour"
):
    rrd_data = resource_service.get_rrd_stats(
        vmid=vmid, resource_info=resource_info, timeframe=timeframe
    )
    data_points = [
        RRDDataPoint(
            time=int(p.get("time", 0)),
            cpu=p.get("cpu"),
            maxcpu=p.get("maxcpu"),
            mem=p.get("mem"),
            maxmem=p.get("maxmem"),
            disk=p.get("disk"),
            maxdisk=p.get("maxdisk"),
            netin=p.get("netin"),
            netout=p.get("netout"),
        )
        for p in rrd_data
    ]
    return RRDDataResponse(timeframe=timeframe, data=data_points)


@router.get("/{vmid}/snapshots", response_model=list[SnapshotInfo])
def list_snapshots(vmid: int, resource_info: ResourceInfoDep):
    return snapshot_service.list_snapshots(vmid=vmid, resource_info=resource_info)


@router.post("/{vmid}/snapshots", response_model=SnapshotResponse)
def create_snapshot(
    vmid: int,
    request: SnapshotCreateRequest,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return snapshot_service.create_snapshot(
        session=session,
        vmid=vmid,
        snapname=request.snapname,
        description=request.description,
        vmstate=request.vmstate,
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.delete("/{vmid}/snapshots/{snapname}", response_model=SnapshotResponse)
def delete_snapshot(
    vmid: int,
    snapname: str,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return snapshot_service.delete_snapshot(
        session=session,
        vmid=vmid,
        snapname=snapname,
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.post(
    "/{vmid}/snapshots/{snapname}/rollback", response_model=SnapshotResponse
)
def rollback_snapshot(
    vmid: int,
    snapname: str,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    return snapshot_service.rollback_snapshot(
        session=session,
        vmid=vmid,
        snapname=snapname,
        resource_info=resource_info,
        user_id=current_user.id,
    )


@router.put("/{vmid}/spec/direct")
def direct_update_spec(
    vmid: int,
    request: DirectSpecUpdateRequest,
    resource_info: ResourceInfoDep,
    session: SessionDep,
    current_user: AdminUser,
):
    return resource_service.direct_update_spec(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user_id=current_user.id,
        cores=request.cores,
        memory=request.memory,
        disk_size=request.disk_size,
    )


@router.post(
    "/{vmid}/reset", response_model=ResetAcceptedResponse, status_code=202
)
def reset_resource(
    vmid: int,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    task_id = reset_service.start_reset(
        session, vmid=vmid, resource_info=resource_info, user=current_user
    )
    return ResetAcceptedResponse(
        message="重置任務已排入背景執行", task_id=task_id
    )


@router.post("/{vmid}/init-snapshot", status_code=201)
def create_init_snapshot(
    vmid: int,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: InstructorUser,
):
    return reset_service.create_init_snapshot(
        session, vmid=vmid, resource_info=resource_info, user=current_user
    )
