"""Launch and inspect fixed, multi-machine quick-practice environments."""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlmodel import Session, col, func, select

from app.core.i18n import t
from app.core.permissions import is_admin
from app.exceptions import BadRequestError, NotFoundError
from app.infrastructure.worker import submit_sync
from app.models import (
    CourseEnvironment,
    CourseEnvironmentAudience,
    CourseEnvironmentEdge,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    QuickPracticeSession,
    QuickPracticeSessionMachine,
    Resource,
    TeachingClassStudent,
    User,
    VMProvisioningStatus,
    VMRequest,
    VMRequestStatus,
    VMTemplate,
    VMTemplateStatus,
)
from app.repositories import resource as resource_repo
from app.schemas import VMRequestCreate
from app.services.resource import quota_service
from app.services.scheduling.recurrence import get_schedule_policy
from app.services.vm import vm_request_service

MAX_ACTIVE_SESSIONS_PER_USER = 1
MAX_SESSIONS_PER_24_HOURS = 3
RECLAIM_GRACE = timedelta(minutes=30)
TOPOLOGY_REPAIR_TIMEOUT = timedelta(minutes=15)
QUICK_NETWORK_COMMENT_PREFIX = "SkyLab:practice-net:"
logger = logging.getLogger(__name__)


def _ip_reservation_prefix(practice_id: uuid.UUID) -> str:
    return f"quick:{practice_id}:"


def _ip_reservation_key(practice_id: uuid.UUID, node_key: str) -> str:
    return f"{_ip_reservation_prefix(practice_id)}{node_key}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _environment_for_version(
    session: Session, version: CourseEnvironmentVersion
) -> CourseEnvironment:
    environment = session.get(CourseEnvironment, version.environment_id)
    if environment is None:
        raise NotFoundError(t("quick_practice.environment_not_found"))
    return environment


def is_visible_to(session: Session, *, environment: CourseEnvironment, user) -> bool:
    """Audience check for the student-facing list and for launch.

    ``campus`` is open to every signed-in user, ``class`` only to students of
    the linked classes, and ``owner`` to nobody but the teacher who owns it.
    The owner always sees their own environment so they can rehearse it.
    """
    if environment.owner_id == user.id or is_admin(user):
        return True
    if environment.audience == "campus":
        return True
    if environment.audience != "class":
        return False
    return (
        session.exec(
            select(CourseEnvironmentAudience.id)
            .join(
                TeachingClassStudent,
                col(TeachingClassStudent.class_id)
                == col(CourseEnvironmentAudience.class_id),
            )
            .where(
                CourseEnvironmentAudience.environment_id == environment.id,
                TeachingClassStudent.user_id == user.id,
                TeachingClassStudent.status == "active",
            )
        ).first()
        is not None
    )


def get_published_template(
    session: Session, *, environment_id: uuid.UUID, user
) -> tuple[CourseEnvironment, CourseEnvironmentVersion]:
    environment = session.get(CourseEnvironment, environment_id)
    if environment is None or environment.usage_scope not in {"quick_practice", "both"}:
        raise NotFoundError(t("quick_practice.template_not_found"))
    if not is_visible_to(session, environment=environment, user=user):
        # Same error as "does not exist": the audience must not be probeable.
        raise NotFoundError(t("quick_practice.template_not_found"))
    version = session.exec(
        select(CourseEnvironmentVersion)
        .where(
            CourseEnvironmentVersion.environment_id == environment.id,
            CourseEnvironmentVersion.status == CourseEnvironmentVersionStatus.published,
        )
        .order_by(col(CourseEnvironmentVersion.version).desc())
    ).first()
    if version is None:
        raise NotFoundError(t("quick_practice.published_version_not_found"))
    return environment, version


def list_published_templates(
    session: Session, *, user
) -> list[tuple[CourseEnvironment, CourseEnvironmentVersion]]:
    environments = session.exec(
        select(CourseEnvironment)
        .where(col(CourseEnvironment.usage_scope).in_(["quick_practice", "both"]))
        .order_by(col(CourseEnvironment.updated_at).desc())
    ).all()
    result: list[tuple[CourseEnvironment, CourseEnvironmentVersion]] = []
    for environment in environments:
        if not is_visible_to(session, environment=environment, user=user):
            continue
        version = session.exec(
            select(CourseEnvironmentVersion)
            .where(
                CourseEnvironmentVersion.environment_id == environment.id,
                CourseEnvironmentVersion.status == CourseEnvironmentVersionStatus.published,
            )
            .order_by(col(CourseEnvironmentVersion.version).desc())
        ).first()
        if version is not None:
            result.append((environment, version))
    return result


def nodes_for_version(
    session: Session, *, version_id: uuid.UUID
) -> list[CourseEnvironmentNode]:
    return list(
        session.exec(
            select(CourseEnvironmentNode)
            .where(CourseEnvironmentNode.version_id == version_id)
            .order_by(col(CourseEnvironmentNode.sort_order))
        ).all()
    )


def _segments(value: str | None) -> set[str]:
    return {
        item.strip()
        for item in (value or "lab-net").replace("/", ",").split(",")
        if item.strip()
    }


def _session_machine_rows(
    session: Session, *, practice_id: uuid.UUID
) -> list[tuple[QuickPracticeSessionMachine, VMRequest]]:
    return list(
        session.exec(
            select(QuickPracticeSessionMachine, VMRequest)
            .join(VMRequest, QuickPracticeSessionMachine.vm_request_id == VMRequest.id)
            .where(QuickPracticeSessionMachine.session_id == practice_id)
            .order_by(col(QuickPracticeSessionMachine.sort_order))
        ).all()
    )


def _apply_session_topology(
    session: Session, *, practice: QuickPracticeSession
) -> list[str]:
    """Materialize one student's published topology as idempotent firewall rules."""
    # Local import avoids a module cycle with the VM scheduling coordinator.
    from app.services.teaching import class_network_service  # noqa: PLC0415

    rows = _session_machine_rows(session, practice_id=practice.id)
    machines_by_key = {
        machine.node_key: request
        for machine, request in rows
        if request.vmid is not None
        and request.provisioning_status == VMProvisioningStatus.completed
    }
    nodes = nodes_for_version(session, version_id=practice.environment_version_id)
    nodes_by_key = {node.node_key: node for node in nodes}
    edges = list(
        session.exec(
            select(CourseEnvironmentEdge).where(
                CourseEnvironmentEdge.version_id == practice.environment_version_id
            )
        ).all()
    )
    directions: list[tuple[VMRequest, VMRequest, str, int | None]] = []
    if edges:
        for edge in edges:
            source = machines_by_key.get(edge.source_node_key)
            target = machines_by_key.get(edge.target_node_key)
            if source is None or target is None:
                continue
            directions.append((source, target, edge.protocol, edge.port))
            if edge.direction == "bidirectional":
                directions.append((target, source, edge.protocol, edge.port))
    else:
        # An environment without explicit edges retains the existing logical
        # segment behaviour used by formal classes.
        for source_key, source in machines_by_key.items():
            source_node = nodes_by_key.get(source_key)
            if source_node is None:
                continue
            for target_key, target in machines_by_key.items():
                if source_key == target_key:
                    continue
                target_node = nodes_by_key.get(target_key)
                if target_node is None or not (
                    _segments(source_node.network) & _segments(target_node.network)
                ):
                    continue
                directions.append((source, target, "any", None))

    errors: list[str] = []
    planned = []
    scope_vmids = {
        request.vmid for request in machines_by_key.values() if request.vmid is not None
    }
    for source, target, protocol, port in directions:
        if source.vmid is None or target.vmid is None:
            continue
        try:
            planned.extend(
                class_network_service.plan_one_way(
                    session,
                    scope_id=practice.id,
                    comment_prefix=QUICK_NETWORK_COMMENT_PREFIX,
                    source_vmid=source.vmid,
                    target_vmid=target.vmid,
                    protocol=protocol,
                    port=port,
                )
            )
        except Exception:
            logger.exception(
                "Failed to apply quick-practice topology session=%s source=%s target=%s",
                practice.id,
                source.vmid,
                target.vmid,
            )
            errors.append(f"{source.vmid} → {target.vmid}: topology failed")
    # 同步而非只建立：重試換過 vmid 的機器會留下指向舊 IP 的白名單
    errors.extend(
        class_network_service.sync_scope_rules(
            comment_prefix=QUICK_NETWORK_COMMENT_PREFIX,
            scope_vmids=scope_vmids,
            planned=planned,
        )
    )

    # 「外網 → 機器」的宣告：每位學生各配一個網址，重複執行會略過已發布的
    from app.services.teaching import course_publication_service  # noqa: PLC0415

    owner = session.get(User, practice.user_id)
    if owner is not None:
        errors.extend(
            course_publication_service.apply_for_machines(
                session,
                version_id=practice.environment_version_id,
                vmid_by_key={
                    key: request.vmid
                    for key, request in machines_by_key.items()
                    if request.vmid is not None
                },
                owner=owner,
                scope=f"practice-{practice.id.hex[:8]}",
            )
        )
    return errors


def reconcile_session(
    session: Session, *, practice_id: uuid.UUID
) -> QuickPracticeSession | None:
    """Advance creating sessions after all machine workers have completed.

    The row lock makes concurrent final machine callbacks idempotent. Topology
    failures remain retryable by the periodic lifecycle scheduler.
    """
    practice = session.exec(
        select(QuickPracticeSession)
        .where(QuickPracticeSession.id == practice_id)
        .with_for_update()
    ).one_or_none()
    if practice is None or practice.status in {"reclaiming", "reclaimed"}:
        return practice

    rows = _session_machine_rows(session, practice_id=practice.id)
    if not rows:
        return practice
    failed = [
        machine.name
        for machine, request in rows
        if request.provisioning_status == VMProvisioningStatus.failed
    ]
    if failed:
        practice.status = "partial_failed"
        practice.last_error = "機器建立失敗：" + "、".join(failed)
        session.add(practice)
        return practice

    completed = all(
        request.vmid is not None
        and request.provisioning_status == VMProvisioningStatus.completed
        for _machine, request in rows
    )
    if not completed:
        practice.status = "creating"
        session.add(practice)
        return practice
    if practice.topology_applied_at is not None:
        practice.status = "ready"
        practice.last_error = None
        session.add(practice)
        return practice

    errors = _apply_session_topology(session, practice=practice)
    if errors:
        practice.status = "partial_failed"
        practice.last_error = "；".join(errors)[:2000]
    else:
        practice.status = "ready"
        practice.topology_applied_at = _utc_now()
        practice.last_error = None
    session.add(practice)
    return practice


def reconcile_for_request(session: Session, *, request_id: uuid.UUID) -> None:
    practice_id = session.exec(
        select(QuickPracticeSessionMachine.session_id).where(
            QuickPracticeSessionMachine.vm_request_id == request_id
        )
    ).first()
    if practice_id is not None:
        reconcile_session(session, practice_id=practice_id)


def _resources_for_session(
    session: Session, *, practice_id: uuid.UUID
) -> list[Resource]:
    return list(
        session.exec(
            select(Resource)
            .join(
                QuickPracticeSessionMachine,
                QuickPracticeSessionMachine.vm_request_id == Resource.request_id,
            )
            .where(QuickPracticeSessionMachine.session_id == practice_id)
        ).all()
    )


def _queue_session_reclaim(
    session: Session, *, practice: QuickPracticeSession
) -> int:
    """Queue all remaining resources through the normal idempotent delete path."""
    from app.services.proxmox import proxmox_service  # noqa: PLC0415
    from app.services.resource import (  # noqa: PLC0415
        deletion_service,
        resource_service,
    )

    resources = _resources_for_session(session, practice_id=practice.id)
    if not resources:
        from app.services.network import ip_management_service  # noqa: PLC0415

        ip_management_service.release_reservations_by_prefix(
            session,
            _ip_reservation_prefix(practice.id),
        )
        practice.status = "reclaimed"
        practice.reclaimed_at = _utc_now()
        session.add(practice)
        session.commit()
        return 0

    practice.status = "reclaiming"
    practice.reclaim_started_at = practice.reclaim_started_at or _utc_now()
    session.add(practice)
    session.commit()
    active = deletion_service.list_active_for_vmids(
        session=session,
        vmids=[resource.vmid for resource in resources],
    )
    queued = 0
    errors: list[str] = []
    for resource in resources:
        if resource.vmid in active:
            continue
        try:
            resource_info = proxmox_service.find_resource(resource.vmid)
        except NotFoundError:
            resource_service.delete_orphan_db_record(
                session=session,
                vmid=resource.vmid,
                user_id=practice.user_id,
            )
            session.commit()
            continue
        except Exception:
            logger.exception(
                "Failed to inspect quick-practice resource session=%s vmid=%s",
                practice.id,
                resource.vmid,
            )
            errors.append(f"VMID {resource.vmid} 無法排入回收")
            continue

        deletion = deletion_service.create_deletion_request(
            session=session,
            user_id=practice.user_id,
            vmid=resource.vmid,
            resource_info=resource_info,
            purge=True,
            force=True,
        )
        submit_sync(
            deletion_service.process_one_request,
            deletion.id,
            name=f"quick-practice-reclaim:{practice.id}:{resource.vmid}",
            task_id=str(deletion.id),
            max_retries=2,
        )
        queued += 1

    refreshed = session.get(QuickPracticeSession, practice.id)
    if refreshed is not None:
        if not _resources_for_session(session, practice_id=practice.id):
            from app.services.network import ip_management_service  # noqa: PLC0415

            ip_management_service.release_reservations_by_prefix(
                session,
                _ip_reservation_prefix(practice.id),
            )
            refreshed.status = "reclaimed"
            refreshed.reclaimed_at = _utc_now()
        elif errors:
            refreshed.last_error = "；".join(errors)[:2000]
        session.add(refreshed)
        session.commit()
    return queued


def process_lifecycle() -> int:
    """Reconcile topology and reclaim expired quick-practice sessions."""
    from app.core.db import engine  # noqa: PLC0415

    now = _utc_now()
    with Session(engine) as session:
        candidate_ids = list(
            session.exec(
                select(QuickPracticeSession.id)
                .where(
                    QuickPracticeSession.reclaimed_at.is_(None),  # type: ignore[union-attr]
                    sa.or_(
                        QuickPracticeSession.topology_applied_at.is_(None),  # type: ignore[union-attr]
                        QuickPracticeSession.expires_at <= now,
                        QuickPracticeSession.status.in_(  # type: ignore[union-attr]
                            ["stopping", "reclaiming"]
                        ),
                    ),
                )
                .order_by(col(QuickPracticeSession.created_at))
                .limit(200)
            ).all()
        )

    processed = 0
    for practice_id in candidate_ids:
        try:
            with Session(engine) as session:
                practice = session.get(QuickPracticeSession, practice_id)
                if practice is None or practice.reclaimed_at is not None:
                    continue
                expires_at = _ensure_utc(practice.expires_at)
                if now < expires_at:
                    reconciled = reconcile_session(
                        session,
                        practice_id=practice.id,
                    )
                    session.commit()
                    if (
                        reconciled is not None
                        and reconciled.status == "partial_failed"
                        and (
                            (reconciled.last_error or "").startswith("機器建立失敗")
                            or now
                            >= _ensure_utc(reconciled.created_at)
                            + TOPOLOGY_REPAIR_TIMEOUT
                        )
                    ):
                        # Do not hand a partial environment to the student.
                        # Provisioning workers already had their bounded retry.
                        # Topology gets a bounded repair window. If either can
                        # no longer recover, reclaim successful siblings too.
                        _queue_session_reclaim(session, practice=reconciled)
                    processed += 1
                    continue
                if now < expires_at + RECLAIM_GRACE:
                    practice.status = "stopping"
                    session.add(practice)
                    session.commit()
                    processed += 1
                    continue
                _queue_session_reclaim(session, practice=practice)
                processed += 1
        except Exception:
            logger.exception(
                "Quick-practice lifecycle failed for session %s", practice_id
            )
    return processed


def _node_disk_gb(session: Session, node: CourseEnvironmentNode) -> int:
    """節點磁碟的實際大小，含來源範本下限；配額與申請單共用同一個值。"""
    # 頂層 import 會與 provisioning_service 互相相依
    from app.services.proxmox import provisioning_service  # noqa: PLC0415

    return provisioning_service.clone_source_disk_gb(session, node)


def _machine_request(
    *,
    session: Session,
    node: CourseEnvironmentNode,
    environment: CourseEnvironment,
    practice_session_id: uuid.UUID,
    now: datetime,
    expires_at: datetime,
) -> VMRequestCreate:
    is_lxc = node.resource_type.lower() == "lxc"
    template: VMTemplate | None = None
    if node.source_type == "template" and node.source_template_id:
        template = session.get(VMTemplate, node.source_template_id)
        if template is None or template.status != VMTemplateStatus.ready:
            raise BadRequestError(
                t("quick_practice.machine_template_not_ready", name=node.name)
            )

    template_id: int | None = None
    ostemplate: str | None = None
    storage = node.custom_storage or "local-lvm"
    username: str | None = None
    if template is not None:
        template_id = template.pve_vmid
        storage = template.storage or storage
        if not is_lxc:
            username = "student"
    elif is_lxc:
        ostemplate = node.custom_image_ref
    else:
        try:
            template_id = int(node.custom_image_ref or "0")
        except ValueError as exc:
            raise BadRequestError(
                t("quick_practice.machine_template_invalid", name=node.name)
            ) from exc
        username = node.custom_username or "student"

    disk_gb = _node_disk_gb(session, node)
    return VMRequestCreate(
        reason=f"Quick practice environment: {environment.name[:120]}",
        resource_type="lxc" if is_lxc else "vm",
        hostname=f"practice-{practice_session_id.hex[:6]}-{node.sort_order + 1}",
        cores=node.cpu,
        memory=node.memory_mb,
        password=secrets.token_urlsafe(24),
        storage=storage,
        environment_type=f"快速練習｜{environment.name}",
        os_info=node.name,
        mode="immediate",
        start_at=now,
        end_at=expires_at,
        ostemplate=ostemplate,
        rootfs_size=disk_gb if is_lxc else None,
        template_id=template_id,
        disk_size=None if is_lxc else disk_gb,
        username=username,
    )


def _session_has_live_request(session: Session, item: QuickPracticeSession) -> bool:
    requests = list(
        session.exec(
            select(VMRequest)
            .join(
                QuickPracticeSessionMachine,
                QuickPracticeSessionMachine.vm_request_id == VMRequest.id,
            )
            .where(QuickPracticeSessionMachine.session_id == item.id)
        ).all()
    )
    return any(
        request.status == VMRequestStatus.approved
        and (
            request.vmid is not None
            or request.provisioning_status != VMProvisioningStatus.failed
        )
        for request in requests
    )


def launch(
    session: Session, *, user, environment_id: uuid.UUID
) -> QuickPracticeSession:
    environment, version = get_published_template(
        session, environment_id=environment_id, user=user
    )
    nodes = nodes_for_version(session, version_id=version.id)
    if not nodes:
        raise BadRequestError(t("quick_practice.template_no_machines"))

    # Serialize launches for one user so simultaneous clicks cannot bypass the
    # one-active-session and rolling 24-hour limits.
    locked_user = session.exec(
        select(User).where(User.id == user.id).with_for_update()
    ).one_or_none()
    if locked_user is None:
        raise NotFoundError(t("quick_practice.user_not_found"))

    now = _utc_now()
    active_sessions = list(
        session.exec(
            select(QuickPracticeSession).where(
                QuickPracticeSession.user_id == user.id,
                QuickPracticeSession.expires_at > now,
                QuickPracticeSession.status != "reclaimed",
            )
        ).all()
    )
    if sum(_session_has_live_request(session, item) for item in active_sessions) >= MAX_ACTIVE_SESSIONS_PER_USER:
        raise BadRequestError(t("quick_practice.active_session_exists"))

    recent_count = session.exec(
        select(func.count(col(QuickPracticeSession.id))).where(
            QuickPracticeSession.user_id == user.id,
            QuickPracticeSession.created_at >= now - timedelta(hours=24),
            sa.not_(
                sa.and_(
                    QuickPracticeSession.status == "reclaimed",
                    QuickPracticeSession.last_error.isnot(None),  # type: ignore[union-attr]
                )
            ),
        )
    ).one()
    if int(recent_count or 0) >= MAX_SESSIONS_PER_24_HOURS:
        raise BadRequestError(t("quick_practice.daily_limit_reached"))

    if environment.max_concurrent_sessions:
        version_ids = list(
            session.exec(
                select(CourseEnvironmentVersion.id).where(
                    CourseEnvironmentVersion.environment_id == environment.id
                )
            ).all()
        )
        running = [
            item
            for item in session.exec(
                select(QuickPracticeSession).where(
                    col(QuickPracticeSession.environment_version_id).in_(version_ids),
                    QuickPracticeSession.expires_at > now,
                    QuickPracticeSession.status != "reclaimed",
                )
            ).all()
            if _session_has_live_request(session, item)
        ]
        if len(running) >= environment.max_concurrent_sessions:
            raise BadRequestError(
                t(
                    "quick_practice.environment_full",
                    max=environment.max_concurrent_sessions,
                )
            )

    quota_service.check_quota(
        session,
        user.id,
        delta_cores=sum(node.cpu for node in nodes),
        delta_memory_mb=sum(node.memory_mb for node in nodes),
        delta_disk_gb=sum(_node_disk_gb(session, node) for node in nodes),
        delta_instances=len(nodes),
    )

    duration_hours = get_schedule_policy(session=session).practice_session_hours
    practice = QuickPracticeSession(
        user_id=user.id,
        environment_version_id=version.id,
        expires_at=now + timedelta(hours=duration_hours),
        status="creating",
    )
    session.add(practice)
    session.flush()

    # Reserve the entire environment's concrete IPs before creating any
    # machine request. IP shortage therefore rolls back the same launch
    # transaction instead of leaving a partial multi-machine environment.
    from app.services.network import ip_management_service  # noqa: PLC0415

    ip_management_service.reserve_ips(
        session,
        teaching_class_id=None,
        reservation_keys=[
            _ip_reservation_key(practice.id, node.node_key) for node in nodes
        ],
    )

    request_ids: list[uuid.UUID] = []
    for node in nodes:
        request_in = _machine_request(
            session=session,
            node=node,
            environment=environment,
            practice_session_id=practice.id,
            now=now,
            expires_at=practice.expires_at,
        )
        db_request = vm_request_service.create_quick_practice_request(
            session=session,
            request_in=request_in,
            user=user,
            # 整組共用 Session id 當群組鍵：placement 會把後續機器釘在
            # 第一台選中的節點上，拓樸 edge 才有實際連通性可言。
            placement_group_id=practice.id,
        )
        session.add(
            QuickPracticeSessionMachine(
                session_id=practice.id,
                vm_request_id=db_request.id,
                node_key=node.node_key,
                name=node.name,
                role=node.role,
                resource_type=node.resource_type,
                sort_order=node.sort_order,
            )
        )
        request_ids.append(db_request.id)

    session.commit()
    session.refresh(practice)
    for request_id in request_ids:
        vm_request_service.submit_course_provision(request_id)
    return practice


def end_session(
    session: Session, *, user, practice_id: uuid.UUID
) -> QuickPracticeSession:
    """學生自己結束練習：立刻回收整組，不必等到期。

    只有本人或管理員能結束；建立次數已經計入 24 小時上限，提早結束只釋放
    資源與「同時一組」的名額，不會退還次數。
    """
    practice = session.get(QuickPracticeSession, practice_id)
    if practice is None or (practice.user_id != user.id and not is_admin(user)):
        raise NotFoundError(t("quick_practice.session_not_found"))
    if practice.status in {"reclaiming", "reclaimed"} or practice.reclaimed_at:
        return practice
    _queue_session_reclaim(session, practice=practice)
    session.refresh(practice)
    return practice


def list_sessions(
    session: Session, *, user_id: uuid.UUID | None = None
) -> list[QuickPracticeSession]:
    now = _utc_now()
    sessions_with_resources = select(QuickPracticeSessionMachine.session_id).join(
        Resource,
        Resource.request_id == QuickPracticeSessionMachine.vm_request_id,
    )
    statement = select(QuickPracticeSession).where(
        QuickPracticeSession.status != "reclaimed",
        sa.or_(
            QuickPracticeSession.expires_at > now,
            col(QuickPracticeSession.id).in_(sessions_with_resources),
        )
    )
    if user_id is not None:
        statement = statement.where(QuickPracticeSession.user_id == user_id)
    return list(
        session.exec(
            statement.order_by(col(QuickPracticeSession.created_at).desc()).limit(100)
        ).all()
    )


def serialize_session(session: Session, item: QuickPracticeSession) -> dict:
    version = session.get(CourseEnvironmentVersion, item.environment_version_id)
    if version is None:
        raise NotFoundError(t("quick_practice.version_not_found"))
    environment = _environment_for_version(session, version)
    rows = list(
        session.exec(
            select(QuickPracticeSessionMachine, VMRequest)
            .join(VMRequest, QuickPracticeSessionMachine.vm_request_id == VMRequest.id)
            .where(QuickPracticeSessionMachine.session_id == item.id)
            .order_by(col(QuickPracticeSessionMachine.sort_order))
        ).all()
    )
    # 對外網址直接讀反向代理紀錄，清單頁不打 Proxmox
    from app.services.teaching import course_publication_service  # noqa: PLC0415

    public_urls = course_publication_service.public_urls_by_vmid(
        session, [request.vmid for _machine, request in rows if request.vmid is not None]
    )
    machines = []
    for machine, request in rows:
        if request.vmid is not None:
            status = "running" if request.provisioning_status == VMProvisioningStatus.completed else "provisioning"
        elif request.provisioning_status == VMProvisioningStatus.failed:
            status = "failed"
        else:
            status = "provisioning"
        machines.append(
            {
                "id": machine.id,
                "node_key": machine.node_key,
                "name": machine.name,
                "role": machine.role,
                "resource_type": machine.resource_type,
                "request_id": request.id,
                "vmid": request.vmid,
                "status": status,
                "node": request.actual_node or request.assigned_node or request.desired_node,
                "ip_address": (
                    resource_repo.get_cached_ip_address(session=session, vmid=request.vmid)
                    if request.vmid is not None
                    else None
                ),
                "os_info": request.os_info,
                "public_url": public_urls.get(request.vmid) if request.vmid else None,
            }
        )
    statuses = {machine["status"] for machine in machines}
    machine_group_status = (
        "failed"
        if statuses == {"failed"}
        else "partial_failed"
        if "failed" in statuses
        else "running"
        if statuses == {"running"}
        else "provisioning"
    )
    group_status = practice_status = item.status or "creating"
    if practice_status == "creating":
        group_status = machine_group_status
        # Even when every VM exists, the environment stays provisioning until
        # the topology coordinator has completed successfully.
        if machine_group_status == "running" and item.topology_applied_at is None:
            group_status = "provisioning"
    elif practice_status == "ready":
        group_status = "running"
    return {
        "id": item.id,
        "kind": "quick_practice",
        "kind_label": "快速練習",
        "title": environment.name,
        "environment_id": environment.id,
        "environment_version_id": version.id,
        "version": version.version,
        "status": group_status,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "topology_applied_at": item.topology_applied_at,
        "reclaim_started_at": item.reclaim_started_at,
        "error": item.last_error,
        "machines": machines,
    }
