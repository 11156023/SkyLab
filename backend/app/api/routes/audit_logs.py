import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, CurrentUser, ResourceInfoDep, SessionDep
from app.exceptions import BadRequestError
from app.models import AuditAction
from app.schemas import (
    AuditActionMeta,
    AuditLogsPublic,
    AuditLogStats,
    AuditUserOption,
)
from app.services.user import audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


def _parse_user_id(user_id: str | None) -> uuid.UUID | None:
    if not user_id:
        return None
    try:
        return uuid.UUID(user_id)
    except ValueError:
        raise BadRequestError("Invalid user_id format")


@router.get("/", response_model=AuditLogsPublic)
def get_all_audit_logs(
    session: SessionDep,
    current_user: AdminUser,
    skip: int = 0,
    limit: int = 100,
    vmid: int | None = None,
    user_id: str | None = None,
    action: AuditAction | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
):
    return audit_service.get_all(
        session=session,
        skip=skip,
        limit=limit,
        vmid=vmid,
        user_id=_parse_user_id(user_id),
        action=action,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )


@router.get("/stats", response_model=AuditLogStats)
def get_audit_log_stats(
    session: SessionDep,
    current_user: AdminUser,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    """Aggregated counters for the admin dashboard cards."""
    return audit_service.get_stats(
        session=session, start_time=start_time, end_time=end_time
    )


@router.get("/actions", response_model=list[AuditActionMeta])
def list_audit_actions(current_user: AdminUser):
    """Returns every supported AuditAction value with its UI category."""
    return audit_service.list_action_metas()


@router.get("/users", response_model=list[AuditUserOption])
def list_audit_users(session: SessionDep, current_user: AdminUser):
    """List of users who have appeared as audit log actors (for filter dropdown)."""
    return audit_service.list_audit_users(session=session)


@router.get("/export")
def export_audit_logs(
    session: SessionDep,
    current_user: AdminUser,
    vmid: int | None = None,
    user_id: str | None = None,
    action: AuditAction | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    ip_address: str | None = None,
    search: str | None = None,
):
    """Stream a CSV file of (filtered) audit logs."""
    csv_text = audit_service.export_csv(
        session=session,
        vmid=vmid,
        user_id=_parse_user_id(user_id),
        action=action,
        start_time=start_time,
        end_time=end_time,
        ip_address=ip_address,
        search=search,
    )
    filename = f"audit-logs-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.csv"
    # UTF-8 BOM so Excel opens Chinese correctly.
    body = "\ufeff" + csv_text
    return StreamingResponse(
        iter([body]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/my", response_model=AuditLogsPublic)
def get_my_audit_logs(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
    action: AuditAction | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    return audit_service.get_all(
        session=session,
        skip=skip,
        limit=limit,
        user_id=current_user.id,
        action=action,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/resources/{vmid}", response_model=AuditLogsPublic)
def get_resource_audit_logs(
    vmid: int,
    session: SessionDep,
    current_user: CurrentUser,
    resource_info: ResourceInfoDep,
    skip: int = 0,
    limit: int = 100,
):
    return audit_service.get_all(
        session=session, vmid=vmid, skip=skip, limit=limit
    )
