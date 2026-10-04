import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.api.deps import (
    AdminUser,
    ControlResourceInfoDep,
    CurrentUser,
    InstructorUser,
    ResourceInfoDep,
    SessionDep,
    TeachingResourceInfoDep,
    check_resource_ownership,
)
from app.schemas import (
    BackupCapability,
    BackupCreateRequest,
    BackupInfo,
    BackupRestoreRequest,
    CurrentStatsResponse,
    DirectSpecUpdateRequest,
    ResetAcceptedResponse,
    RRDDataPoint,
    RRDDataResponse,
    SnapshotCapability,
    SnapshotCreateRequest,
    SnapshotInfo,
    SnapshotResponse,
)
from app.schemas.template import (
    ResourceTemplateManual,
    TemplateAttachmentPublic,
)
from app.services.network import snapshot_service
from app.services.resource import backup_service, reset_service, resource_service
from app.services.resource.access import require_resource_management
from app.services.template import template_service

router = APIRouter(prefix="/resources", tags=["resource-details"])


# ===== Endpoints =====


@router.get("/{vmid}/template-manual", response_model=ResourceTemplateManual)
def get_template_manual(
    vmid: int, current_user: CurrentUser, session: SessionDep
) -> ResourceTemplateManual:
    """克隆機來源範本的使用手冊（資源擁有者或 admin）。

    以資源擁有權授權，不受範本可見範圍影響——範本轉私人後，
    已克隆機的擁有者仍可下載手冊。
    """
    check_resource_ownership(vmid, current_user, session)
    template, attachments = template_service.get_manual_for_cloned_resource(
        session=session, vmid=vmid
    )
    data = [TemplateAttachmentPublic.model_validate(a) for a in attachments]
    return ResourceTemplateManual(
        template_name=template.name if template else None,
        data=data,
        count=len(data),
    )


@router.get("/{vmid}/template-manual/{attachment_id}/download")
def download_template_manual(
    vmid: int,
    attachment_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> FileResponse:
    """下載來源範本手冊，還原原始檔名（資源擁有者或 admin）。"""
    check_resource_ownership(vmid, current_user, session)
    path, attachment = (
        template_service.get_manual_attachment_for_cloned_resource(
            session=session, vmid=vmid, attachment_id=attachment_id
        )
    )
    return FileResponse(
        path,
        filename=attachment.filename,
        media_type=attachment.content_type or "application/octet-stream",
    )


@router.get("/{vmid}/current-stats", response_model=CurrentStatsResponse)
def get_current_stats(vmid: int, resource_info: ControlResourceInfoDep):
    stats = resource_service.get_current_stats(vmid=vmid, resource_info=resource_info)
    return CurrentStatsResponse(**stats)


@router.get("/{vmid}/stats", response_model=RRDDataResponse)
def get_rrd_stats(
    vmid: int, resource_info: ControlResourceInfoDep, timeframe: str = "hour"
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


@router.get("/{vmid}/snapshot-capability", response_model=SnapshotCapability)
def get_snapshot_capability(
    vmid: int, resource_info: TeachingResourceInfoDep
) -> SnapshotCapability:
    """這台機器當下能否使用快照（含一鍵重置）；不可用時前端隱藏整組快照功能。"""
    return SnapshotCapability(
        **snapshot_service.get_capability(vmid=vmid, resource_info=resource_info)
    )


@router.get("/{vmid}/snapshots", response_model=list[SnapshotInfo])
def list_snapshots(vmid: int, resource_info: TeachingResourceInfoDep):
    return snapshot_service.list_snapshots(vmid=vmid, resource_info=resource_info)


@router.post("/{vmid}/snapshots", response_model=SnapshotResponse)
def create_snapshot(
    vmid: int,
    request: SnapshotCreateRequest,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return snapshot_service.create_snapshot(
        session=session,
        vmid=vmid,
        snapname=request.snapname,
        description=request.description,
        vmstate=request.vmstate,
        resource_info=resource_info,
        user=current_user,
    )


@router.delete("/{vmid}/snapshots/{snapname}", response_model=SnapshotResponse)
def delete_snapshot(
    vmid: int,
    snapname: str,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
    return snapshot_service.delete_snapshot(
        session=session,
        vmid=vmid,
        snapname=snapname,
        resource_info=resource_info,
        user=current_user,
    )


@router.post(
    "/{vmid}/snapshots/{snapname}/rollback", response_model=SnapshotResponse
)
def rollback_snapshot(
    vmid: int,
    snapname: str,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
):
    require_resource_management(session=session, user=current_user, vmid=vmid)
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


# 路徑不能用 /{vmid}/reset：resources.py 已有同路徑的電源硬重啟端點，
# 兩者同時註冊會讓 OpenAPI schema 互相覆蓋、其中一個 runtime 打不到。
@router.post(
    "/{vmid}/reset-to-init", response_model=ResetAcceptedResponse, status_code=202
)
def reset_to_init(
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


# ===== 備份／還原（快照不能用的機器以備份當還原點） =====


@router.get("/{vmid}/backup-capability", response_model=BackupCapability)
def get_backup_capability(
    vmid: int,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
) -> BackupCapability:
    """這台機器能否使用備份（所屬叢集有設定備份 storage 且可用）。"""
    return BackupCapability(
        **backup_service.get_capability(
            session=session, vmid=vmid, resource_info=resource_info, user=current_user
        )
    )


@router.get("/{vmid}/backups", response_model=list[BackupInfo])
def list_backups(
    vmid: int, resource_info: TeachingResourceInfoDep, session: SessionDep
) -> list[BackupInfo]:
    """只列出 SkyLab 為這台機器建立的備份，不含機構自己的排程備份。"""
    return [
        BackupInfo(**item)
        for item in backup_service.list_backups(
            session=session, vmid=vmid, resource_info=resource_info
        )
    ]


@router.post(
    "/{vmid}/backups", response_model=ResetAcceptedResponse, status_code=202
)
def create_backup(
    vmid: int,
    request: BackupCreateRequest,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
) -> ResetAcceptedResponse:
    require_resource_management(session=session, user=current_user, vmid=vmid)
    task_id = backup_service.start_backup(
        session,
        vmid=vmid,
        resource_info=resource_info,
        user=current_user,
        description=request.description,
    )
    return ResetAcceptedResponse(message="備份任務已排入背景執行", task_id=task_id)


@router.post(
    "/{vmid}/backups/restore", response_model=ResetAcceptedResponse, status_code=202
)
def restore_backup(
    vmid: int,
    request: BackupRestoreRequest,
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
) -> ResetAcceptedResponse:
    require_resource_management(session=session, user=current_user, vmid=vmid)
    task_id = backup_service.start_restore(
        session,
        vmid=vmid,
        resource_info=resource_info,
        user=current_user,
        volid=request.volid,
    )
    return ResetAcceptedResponse(message="還原任務已排入背景執行", task_id=task_id)


# volid 含斜線與冒號，放 query 而不是路徑片段
@router.delete("/{vmid}/backups", response_model=SnapshotResponse)
def delete_backup(
    vmid: int,
    volid: Annotated[str, Query(min_length=1, max_length=500)],
    resource_info: TeachingResourceInfoDep,
    session: SessionDep,
    current_user: CurrentUser,
) -> SnapshotResponse:
    require_resource_management(session=session, user=current_user, vmid=vmid)
    result = backup_service.delete_backup(
        session=session,
        vmid=vmid,
        resource_info=resource_info,
        user=current_user,
        volid=volid,
    )
    return SnapshotResponse(message=result["message"])
