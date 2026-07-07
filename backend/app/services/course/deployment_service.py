"""課程實驗機秒開部署編排。

檢查（published / 綁模板 / 單人上限）→ 由範本組 VMRequestCreate →
委派 vm_request_service.create_course_request（配額 + 核准 + 節點保留 + audit）
→ 寫入 CourseDeployment → commit 後背景觸發 provision。

部署狀態不落地，一律 join VMRequest 即時推導。
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.core.permissions import is_admin, is_teacher
from app.exceptions import (
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models import (
    CourseDeployment,
    CourseRoom,
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
)
from app.repositories import governance as governance_repo
from app.schemas import VMRequestCreate
from app.schemas.course import CourseDeploymentPublic, DeploymentStatus
from app.services.course import course_service
from app.services.vm import vm_request_service

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _derive_status(
    deployment: CourseDeployment, vm_request: VMRequest, *, now: datetime
) -> DeploymentStatus:
    expires_at = deployment.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        return "expired"
    if vm_request.status in (
        VMRequestStatus.rejected,
        VMRequestStatus.cancelled,
    ):
        return "failed"
    if (
        vm_request.migration_status == VMMigrationStatus.failed
        and vm_request.vmid is None
    ):
        return "failed"
    if vm_request.vmid is not None:
        return "running"
    return "provisioning"


def _to_public(
    deployment: CourseDeployment, vm_request: VMRequest, *, now: datetime
) -> CourseDeploymentPublic:
    status = _derive_status(deployment, vm_request, now=now)
    return CourseDeploymentPublic(
        id=deployment.id,
        room_id=deployment.room_id,
        vm_request_id=deployment.vm_request_id,
        vmid=vm_request.vmid,
        status=status,
        error=vm_request.migration_error if status == "failed" else None,
        created_at=deployment.created_at,
        expires_at=deployment.expires_at,
    )


def _get_vm_request(session: Session, vm_request_id: uuid.UUID) -> VMRequest:
    vm_request = session.get(VMRequest, vm_request_id)
    if vm_request is None:
        raise NotFoundError("Deployment VM request not found")
    return vm_request


def _active_deployment_count(
    session: Session, *, user_id: uuid.UUID, now: datetime
) -> int:
    """使用者進行中的課程部署數（排除已失敗/已取消的申請）。"""
    rows = session.exec(
        select(CourseDeployment, VMRequest)
        .join(VMRequest, CourseDeployment.vm_request_id == VMRequest.id)
        .where(
            CourseDeployment.user_id == user_id,
            CourseDeployment.expires_at > now,
        )
    ).all()
    return sum(
        1
        for deployment, vm_request in rows
        if _derive_status(deployment, vm_request, now=now)
        in ("provisioning", "running")
    )


def _short(value: uuid.UUID) -> str:
    return uuid.UUID(str(value)).hex[:6]


def _build_request(
    *,
    room: CourseRoom,
    template: VMTemplate,
    user_id: uuid.UUID,
    now: datetime,
    ttl_hours: int,
) -> VMRequestCreate:
    is_lxc = template.resource_type == "lxc"
    hostname = f"course-{_short(room.id)}-{_short(user_id)}"
    return VMRequestCreate(
        reason=f"Course lab deployment: {room.title[:100]}",
        resource_type="lxc" if is_lxc else "vm",
        hostname=hostname,
        cores=template.default_cores or 2,
        memory=template.default_memory or 2048,
        # 平台流程需要密碼欄位；課程機登入憑證以範本內烘焙為準
        password=secrets.token_urlsafe(24),
        storage=template.storage or "local-lvm",
        environment_type="Course Lab",
        os_info=template.name,
        mode="immediate",
        start_at=now,
        end_at=now + timedelta(hours=ttl_hours),
        rootfs_size=(template.default_disk or 8) if is_lxc else None,
        template_id=template.pve_vmid,
        disk_size=None if is_lxc else template.default_disk,
        username=None if is_lxc else "student",
    )


def deploy(
    session: Session, *, user, room_id: uuid.UUID
) -> CourseDeploymentPublic:
    room = course_service.get_room_or_404(session, room_id)
    course_service.get_published_path_or_404(session, room.path_id)
    if room.template_id is None:
        raise BadRequestError("This room is theory-only and has no lab")
    template = session.get(VMTemplate, room.template_id)
    if template is None:
        raise BadRequestError("Room template no longer exists")
    course_service._require_ready_template(session, template.id)

    governance = governance_repo.get_governance_config(session=session)
    now = _utc_now()
    active = _active_deployment_count(session, user_id=user.id, now=now)
    if active >= governance.course_max_active_per_user:
        raise BadRequestError(
            "You already have an active course lab. "
            "Terminate it before starting another."
        )

    request_in = _build_request(
        room=room,
        template=template,
        user_id=user.id,
        now=now,
        ttl_hours=governance.course_ttl_hours,
    )
    db_request = vm_request_service.create_course_request(
        session=session, request_in=request_in, user=user
    )
    deployment = CourseDeployment(
        room_id=room.id,
        user_id=user.id,
        vm_request_id=db_request.id,
        expires_at=request_in.end_at,
    )
    session.add(deployment)
    session.commit()
    session.refresh(deployment)
    session.refresh(db_request)

    # commit 後才觸發背景 provision（防重複由 runner task_id 去重 + DB 鎖把關）
    vm_request_service.submit_course_provision(db_request.id)
    logger.info(
        "Course lab deploy: user=%s room=%s vm_request=%s",
        user.id,
        room.id,
        db_request.id,
    )
    return _to_public(deployment, db_request, now=now)


def get_my_room_deployment(
    session: Session, *, user_id: uuid.UUID, room_id: uuid.UUID
) -> CourseDeploymentPublic | None:
    """該學生此房間最近一筆未過期部署（無則 None）。"""
    now = _utc_now()
    row = session.exec(
        select(CourseDeployment)
        .where(
            CourseDeployment.room_id == room_id,
            CourseDeployment.user_id == user_id,
            CourseDeployment.expires_at > now,
        )
        .order_by(CourseDeployment.created_at.desc())
    ).first()
    if row is None:
        return None
    vm_request = _get_vm_request(session, row.vm_request_id)
    return _to_public(row, vm_request, now=now)


def _get_owned_deployment(
    session: Session, *, user, deployment_id: uuid.UUID
) -> CourseDeployment:
    deployment = session.get(CourseDeployment, deployment_id)
    if deployment is None:
        raise NotFoundError("Deployment not found")
    if deployment.user_id != user.id and not (is_teacher(user) or is_admin(user)):
        raise PermissionDeniedError("Not your deployment")
    return deployment


def get_deployment(
    session: Session, *, user, deployment_id: uuid.UUID
) -> CourseDeploymentPublic:
    deployment = _get_owned_deployment(
        session, user=user, deployment_id=deployment_id
    )
    vm_request = _get_vm_request(session, deployment.vm_request_id)
    return _to_public(deployment, vm_request, now=_utc_now())


def terminate(
    session: Session, *, user, deployment_id: uuid.UUID
) -> CourseDeploymentPublic:
    """提前歸還：把 VMRequest 的 end_at 提前為 now，交由既有回收排程關機/銷毀。"""
    deployment = _get_owned_deployment(
        session, user=user, deployment_id=deployment_id
    )
    vm_request = _get_vm_request(session, deployment.vm_request_id)
    now = _utc_now()
    vm_request.end_at = now
    deployment.expires_at = now
    session.add(vm_request)
    session.add(deployment)
    session.commit()
    session.refresh(deployment)
    session.refresh(vm_request)
    logger.info(
        "Course lab terminate: user=%s deployment=%s", user.id, deployment_id
    )
    return _to_public(deployment, vm_request, now=now)
