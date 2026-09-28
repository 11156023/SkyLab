"""Launch and inspect fixed, multi-machine quick-practice environments."""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlmodel import Session, col, func, select

from app.core.i18n import t
from app.core.permissions import is_admin
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    CourseEnvironment,
    CourseEnvironmentEdge,
    CourseEnvironmentNode,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
    QuickPracticeSession,
    QuickPracticeSessionMachine,
    Resource,
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
from app.services.template import password_policy
from app.services.vm import vm_request_service

MAX_ACTIVE_SESSIONS_PER_USER = 1
MAX_SESSIONS_PER_24_HOURS = 3
RECLAIM_GRACE = timedelta(minutes=30)
TOPOLOGY_REPAIR_TIMEOUT = timedelta(minutes=15)
QUICK_NETWORK_COMMENT_PREFIX = "SkyLab:practice-net:"
#: 可以拿來開快速練習的課程環境 usage_scope
QUICK_PRACTICE_SCOPES = ("quick_practice", "both")
#: reconcile_session 寫進 last_error 的前綴；lifecycle 看到它就直接整組回收
MACHINE_FAILURE_PREFIX = "機器建立失敗："
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


def _latest_published_version(
    session: Session, environment_id: uuid.UUID
) -> CourseEnvironmentVersion | None:
    return session.exec(
        select(CourseEnvironmentVersion)
        .where(
            CourseEnvironmentVersion.environment_id == environment_id,
            CourseEnvironmentVersion.status == CourseEnvironmentVersionStatus.published,
        )
        .order_by(col(CourseEnvironmentVersion.version).desc())
    ).first()


def get_published_template(
    session: Session, *, environment_id: uuid.UUID
) -> tuple[CourseEnvironment, CourseEnvironmentVersion]:
    """提供為快速練習就代表任何登入者都拿得到。

    開放對象（audience 與班級白名單）已整個移除，快速練習不分對象。
    """
    environment = session.get(CourseEnvironment, environment_id)
    if environment is None or environment.usage_scope not in QUICK_PRACTICE_SCOPES:
        raise NotFoundError(t("quick_practice.template_not_found"))
    version = _latest_published_version(session, environment.id)
    if version is None:
        raise NotFoundError(t("quick_practice.published_version_not_found"))
    return environment, version


def list_published_templates(
    session: Session,
) -> list[tuple[CourseEnvironment, CourseEnvironmentVersion]]:
    environments = session.exec(
        select(CourseEnvironment)
        .where(col(CourseEnvironment.usage_scope).in_(QUICK_PRACTICE_SCOPES))
        .order_by(col(CourseEnvironment.updated_at).desc())
    ).all()
    result: list[tuple[CourseEnvironment, CourseEnvironmentVersion]] = []
    for environment in environments:
        version = _latest_published_version(session, environment.id)
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
    from app.services.teaching import class_network_service

    rows = _session_machine_rows(session, practice_id=practice.id)
    machines_by_key = {
        machine.node_key: request
        for machine, request in rows
        if request.vmid is not None
        and request.provisioning_status == VMProvisioningStatus.completed
    }
    nodes = nodes_for_version(session, version_id=practice.environment_version_id)
    edges = list(
        session.exec(
            select(CourseEnvironmentEdge).where(
                CourseEnvironmentEdge.version_id == practice.environment_version_id
            )
        ).all()
    )
    peer_policy = class_network_service.peer_policy_for_version(
        session, practice.environment_version_id
    )
    vmid_by_key = {
        key: request.vmid
        for key, request in machines_by_key.items()
        if request.vmid is not None
    }
    network_by_key = {node.node_key: node.network for node in nodes}
    # 與正式班級共用同一套展開規則（explicit 連線／segment 同網段互通）
    directions = class_network_service.topology_directions(
        peer_policy=peer_policy,
        edges=edges,
        vmid_by_key=vmid_by_key,
        network_by_key=network_by_key,
    )

    errors: list[str] = []
    planned = []
    scope_vmids = {
        request.vmid for request in machines_by_key.values() if request.vmid is not None
    }
    for source_vmid, target_vmid, protocol, port in directions:
        try:
            planned.extend(
                class_network_service.plan_one_way(
                    session,
                    scope_id=practice.id,
                    comment_prefix=QUICK_NETWORK_COMMENT_PREFIX,
                    source_vmid=source_vmid,
                    target_vmid=target_vmid,
                    protocol=protocol,
                    port=port,
                )
            )
        except Exception:
            logger.exception(
                "Failed to apply quick-practice topology session=%s source=%s target=%s",
                practice.id,
                source_vmid,
                target_vmid,
            )
            errors.append(f"{source_vmid} → {target_vmid}: topology failed")
    # 同步而非只建立：重試換過 vmid 的機器會留下指向舊 IP 的白名單
    errors.extend(
        class_network_service.sync_scope_rules(
            comment_prefix=QUICK_NETWORK_COMMENT_PREFIX,
            scope_vmids=scope_vmids,
            planned=planned,
        )
    )

    # 「外網 → 機器」的宣告：每位學生各配一個網址，重複執行會略過已發布的
    from app.services.teaching import course_publication_service

    owner = session.get(User, practice.user_id)
    if owner is not None:
        errors.extend(
            course_publication_service.apply_for_machines(
                session,
                version_id=practice.environment_version_id,
                vmid_by_key=vmid_by_key,
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
        practice.last_error = MACHINE_FAILURE_PREFIX + "、".join(failed)
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


_UNSTARTED_PROVISIONING = {
    VMProvisioningStatus.idle,
    VMProvisioningStatus.pending,
    VMProvisioningStatus.blocked,
}
_OPEN_REQUEST_STATUSES = {VMRequestStatus.pending, VMRequestStatus.approved}


def _clone_in_flight(request: VMRequest, *, now: datetime) -> bool:
    """clone 正在 worker 上跑：running 且未超過 stale 門檻。"""
    from app.services.scheduling import policy as scheduling_policy

    return (
        request.provisioning_status == VMProvisioningStatus.running
        and not scheduling_policy.is_provisioning_stale(
            request.provisioning_started_at, now=now
        )
    )


def _settle_unstarted_machines(
    session: Session, *, practice_id: uuid.UUID
) -> bool:
    """取消還沒開始 clone 的機器申請單；回傳是否仍有 clone 在進行。

    回收只看得到已經有 Resource 的機器。還在排隊的申請單若維持 approved，
    排程器之後照樣會 clone，session 卻已標 reclaimed、IP 預留也放掉了，那台
    機器就沒人回收。沒開始的直接取消（worker 開跑前會檢查 status ==
    approved）；正在 clone 的（running 且未逾時，或被 worker 鎖住）不能動，
    交給下一輪 lifecycle 等它建好再排刪除。
    """
    now = _utc_now()
    in_flight = False
    cancellable: list[uuid.UUID] = []
    for _machine, request in _session_machine_rows(session, practice_id=practice_id):
        if request.vmid is not None or request.status not in _OPEN_REQUEST_STATUSES:
            continue
        if _clone_in_flight(request, now=now):
            in_flight = True
        elif request.provisioning_status in _UNSTARTED_PROVISIONING or (
            # 逾時的 running 視同沒開始：worker 真的還在跑的話，完成時看到
            # 非 approved 會自己把機器收掉。
            request.provisioning_status == VMProvisioningStatus.running
        ):
            cancellable.append(request.id)
    if not cancellable:
        return in_flight

    # SKIP LOCKED：worker 正拿著鎖準備 clone 的單不等它，視為進行中。
    locked = session.exec(
        select(VMRequest)
        .where(col(VMRequest.id).in_(cancellable))
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    if len(locked) < len(cancellable):
        in_flight = True
    for request in locked:
        if request.vmid is not None or request.status not in _OPEN_REQUEST_STATUSES:
            continue
        if _clone_in_flight(request, now=now):
            in_flight = True
            continue
        request.status = VMRequestStatus.cancelled
        request.provisioning_status = VMProvisioningStatus.idle
        request.review_comment = "Cancelled by quick-practice reclaim"
        session.add(request)
    session.commit()
    return in_flight


def _mark_reclaimed(session: Session, practice: QuickPracticeSession) -> None:
    from app.services.network import ip_management_service

    ip_management_service.release_reservations_by_prefix(
        session,
        _ip_reservation_prefix(practice.id),
    )
    practice.status = "reclaimed"
    practice.reclaimed_at = _utc_now()


def _queue_session_reclaim(
    session: Session, *, practice: QuickPracticeSession
) -> int:
    """Queue all remaining resources through the normal idempotent delete path."""
    from app.services.proxmox import proxmox_service
    from app.services.resource import (
        deletion_service,
        resource_service,
    )

    in_flight = _settle_unstarted_machines(session, practice_id=practice.id)
    resources = _resources_for_session(session, practice_id=practice.id)
    if not resources:
        if in_flight:
            # 還有機器在 clone：保留 IP 預留、停在 reclaiming，
            # process_lifecycle 每輪會再進來，等它建好就排刪除。
            practice.status = "reclaiming"
            practice.reclaim_started_at = practice.reclaim_started_at or _utc_now()
        else:
            _mark_reclaimed(session, practice)
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
        deletion_service.enqueue_processing(session=session, req=deletion)
        queued += 1


    refreshed = session.get(QuickPracticeSession, practice.id)
    if refreshed is not None:
        if not in_flight and not _resources_for_session(
            session, practice_id=practice.id
        ):
            _mark_reclaimed(session, refreshed)
        elif errors:
            refreshed.last_error = "；".join(errors)[:2000]
        session.add(refreshed)
        session.commit()
    return queued


def process_lifecycle() -> int:
    """Reconcile topology and reclaim expired quick-practice sessions."""
    from app.core.db import engine

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
                if practice.status == "reclaiming":
                    # 學生提前結束時還沒到期。不先收尾的話會掉進下面的
                    # reconcile 分支（對 reclaiming 直接 return），session 就卡到
                    # expires_at + RECLAIM_GRACE，期間佔住「同時一組」名額無法重開。
                    _queue_session_reclaim(session, practice=practice)
                    processed += 1
                    continue
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
                            (reconciled.last_error or "").startswith(
                                MACHINE_FAILURE_PREFIX
                            )
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
    from app.services.proxmox import provisioning_service

    return provisioning_service.clone_source_disk_gb(session, node)


def _hostname_label(node: CourseEnvironmentNode) -> str:
    """機器名轉成合法的主機名片段。

    用 ``name`` 而不是 ``node_key``：後者是編輯器產生的 ``node-<timestamp>``，
    放進主機名比流水號更難讀。名稱是老師自己取的，才帶得出「哪台是哪台」。
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", (node.name or "").lower()).strip("-")
    # 截斷後可能斷在連字號上，再修一次尾巴；純中文名會清空，退回流水號
    return cleaned[:24].strip("-") or f"m{node.sort_order + 1}"


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
        hostname=f"practice-{practice_session_id.hex[:6]}-{_hostname_label(node)}",
        cores=node.cpu,
        memory=node.memory_mb,
        # 範本不勾「允許自訂登入密碼」就沿用範本內的密碼（None）；否則發隨機密碼，
        # 會真的套用並存進 resources 憑證卡片
        password=password_policy.resolve_login_password(template=template),
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
        session, environment_id=environment_id
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
    from app.services.network import ip_management_service

    ip_management_service.reserve_ips(
        session,
        teaching_class_id=None,
        reservation_keys=[
            _ip_reservation_key(practice.id, node.node_key) for node in nodes
        ],
    )

    request_ids: list[uuid.UUID] = []
    requester_id = user.id
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
        vm_request_service.submit_course_provision(
            session, request_id=request_id, user_id=requester_id
        )
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
    rows = _session_machine_rows(session, practice_id=item.id)
    # 對外網址直接讀反向代理紀錄，清單頁不打 Proxmox
    from app.services.teaching import course_publication_service

    vmids = [request.vmid for _machine, request in rows if request.vmid is not None]
    public_urls = course_publication_service.public_urls_by_vmid(session, vmids)
    forward_endpoints = course_publication_service.forward_endpoints_by_vmid(
        session, vmids
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
                "forward_endpoints": (
                    forward_endpoints.get(request.vmid, []) if request.vmid else []
                ),
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
