from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime

from sqlmodel import Session, select

from app.core.db import engine
from app.domain.scheduling.models import ScheduledTask
from app.domain.scheduling.runner import run_polling_scheduler
from app.exceptions import NotFoundError
from app.models import (
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
)
from app.repositories import resource as resource_repo
from app.repositories import vm_migration_job as vm_migration_job_repo  # noqa: F401  (re-exported for tests/back-compat)
from app.repositories import vm_request as vm_request_repo
from app.services.network import ip_management_service
from app.services.proxmox import provisioning_service, proxmox_service
from app.services.scheduling import policy as scheduling_policy
from app.services.scheduling import recurrence_scheduler
from app.services.scheduling import support as scheduling_support
from app.services.user import audit_service
from app.services.vm import vm_request_placement_service

logger = logging.getLogger(__name__)

SCHEDULER_POLL_SECONDS = scheduling_policy.SCHEDULER_POLL_SECONDS
_VM_DISK_PREFIXES = ("scsi", "sata", "ide", "virtio", "efidisk", "tpmstate")
_LXC_MOUNT_PREFIXES = ("rootfs", "mp")
_MigrationPolicy = scheduling_policy.MigrationPolicy


def _utc_now() -> datetime:
    return scheduling_policy.utc_now()


def _normalize_datetime(value: datetime | None) -> datetime | None:
    return scheduling_policy.normalize_datetime(value)


def _resource_type_for_request(request: VMRequest) -> str:
    return scheduling_policy.resource_type_for_request(request)


def _should_pin_request_for_auto_migration(
    *,
    request: VMRequest,
    detected_runtime_pin: bool = False,
) -> bool:
    return scheduling_support.should_pin_request_for_auto_migration(
        request=request,
        detected_runtime_pin=detected_runtime_pin,
    )


def _get_migration_policy(*, session: Session) -> _MigrationPolicy:
    return scheduling_policy.get_migration_policy(session=session)


def _migration_worker_id() -> str:
    return scheduling_policy.migration_worker_id()


def _next_retry_at(*, now: datetime, policy: _MigrationPolicy, attempt_count: int) -> datetime:
    return scheduling_policy.next_retry_at(
        now=now,
        policy=policy,
        attempt_count=attempt_count,
    )


def _find_existing_resource_for_request(
    *,
    session: Session,
    request: VMRequest,
) -> dict | None:
    return scheduling_support.find_existing_resource_for_request(
        session=session,
        request=request,
    )


def _adopt_existing_resource(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str, str | None, bool] | None:
    """Try to adopt an already-existing Proxmox resource for this request.

    Returns (vmid, actual_node, placement_strategy, started) or None.
    """
    resource_type = _resource_type_for_request(request)
    existing_resource = _find_existing_resource_for_request(
        session=session,
        request=request,
    )
    if existing_resource is None:
        return None

    desired_node = str(request.desired_node or request.assigned_node or "")
    placement_strategy_used = (
        request.placement_strategy_used
        or vm_request_placement_service.DEFAULT_PLACEMENT_STRATEGY
    )
    vmid = int(existing_resource["vmid"])
    actual_node = str(existing_resource["node"])
    if not resource_repo.get_resource_by_vmid(session=session, vmid=vmid):
        resource_repo.create_resource(
            session=session,
            vmid=vmid,
            user_id=request.user_id,
            environment_type=request.environment_type,
            os_info=request.os_info,
            expiry_date=request.expiry_date,
            template_id=request.template_id,
            service_template_slug=getattr(request, "service_template_slug", None),
            commit=False,
        )
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=vmid,
        assigned_node=desired_node or actual_node,
        desired_node=desired_node or actual_node,
        actual_node=actual_node,
        placement_strategy_used=placement_strategy_used,
        migration_status=(
            VMMigrationStatus.pending
            if desired_node and desired_node != actual_node
            else VMMigrationStatus.idle
        ),
        migration_error=None,
        commit=False,
    )
    request.status = VMRequestStatus.running
    session.add(request)

    status = proxmox_service.get_status(actual_node, vmid, resource_type)
    started = False
    if str(status.get("status") or "").lower() != "running":
        proxmox_service.control(actual_node, vmid, resource_type, "start")
        started = True
    audit_service.log_action(
        session=session,
        user_id=None,
        vmid=vmid,
        action="resource_start",
        details=(
            f"Adopted existing {request.resource_type} resource for request {request.id}"
        ),
        commit=False,
    )
    logger.warning(
        "Adopted existing %s resource VMID %s for request %s",
        resource_type, vmid, request.id,
    )
    detected_runtime_pin = False
    try:
        detected_runtime_pin = _detect_migration_pinned(
            node=actual_node,
            vmid=vmid,
            resource_type=resource_type,
        )
    except Exception:
        logger.debug("Failed to detect migration pinning for VMID %s", vmid, exc_info=True)
    if _should_pin_request_for_auto_migration(
        request=request,
        detected_runtime_pin=detected_runtime_pin,
    ) and not request.migration_pinned:
        request.migration_pinned = True
        session.add(request)
    return vmid, actual_node, placement_strategy_used, started


def _provision_new_resource(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str, str | None]:
    """Lock → mark provisioning → clone outside txn → mark running.

    This is the core anti-duplication pattern:
    1. SELECT FOR UPDATE SKIP LOCKED — if locked, bail
    2. status = provisioning, commit  (visible to other sessions)
    3. plan_provision (resolve storage etc.)  — still in a short txn
    4. commit / close session
    5. execute_provision (clone VM) — NO open transaction
    6. Open new session → record vmid + status=running, commit
    """
    resource_type = _resource_type_for_request(request)
    desired_node = str(request.desired_node or request.assigned_node or "")

    # Service template deployment path: community-scripts creates the LXC
    # itself, so skip normal clone-based provisioning.
    if resource_type == "lxc" and request.service_template_slug:
        return _provision_via_service_template(session=session, request=request)

    # --- Phase 1: mark as provisioning + plan (short txn) -----------------
    request.status = VMRequestStatus.provisioning
    session.add(request)
    session.commit()
    logger.info("Marked request %s as provisioning", request.id)

    try:
        plan = provisioning_service.plan_provision(
            session=session,
            db_request=request,
        )
    except Exception:
        # Plan failed — revert to approved so scheduler can retry.
        # IP allocated during plan_provision is already flushed to session;
        # rollback first, then revert status cleanly.
        session.rollback()
        request = vm_request_repo.get_vm_request_by_id(
            session=session, request_id=request.id, for_update=True,
        )
        if request:
            request.status = VMRequestStatus.approved
            session.add(request)
            session.commit()
        raise

    request_id = request.id
    request_user_id = request.user_id
    request_env_type = request.environment_type
    request_os_info = request.os_info
    request_expiry_date = request.expiry_date
    request_template_id = request.template_id
    request_resource_type = request.resource_type
    request_migration_pinned = request.migration_pinned
    request_service_template_slug = getattr(request, "service_template_slug", None)

    # Close session so clone runs outside any transaction.
    session.commit()

    # --- Phase 2: execute clone (NO open transaction) ---------------------
    try:
        new_vmid, actual_node = provisioning_service.execute_provision(plan)
    except Exception:
        # Clone failed — revert to approved and release allocated IP.
        with Session(engine) as rollback_session:
            # Release IP allocated during planning
            try:
                ip_management_service.release_ip(rollback_session, plan["vmid"])
                rollback_session.commit()
            except Exception:
                logger.warning("Failed to release IP for VMID %s during rollback", plan["vmid"])

            req = vm_request_repo.get_vm_request_by_id(
                session=rollback_session, request_id=request_id, for_update=True,
            )
            if req and req.status == VMRequestStatus.provisioning:
                req.status = VMRequestStatus.approved
                rollback_session.add(req)
                rollback_session.commit()
                logger.warning("Reverted request %s to approved after provision failure", request_id)
        raise

    # --- Phase 3: record result (new short txn) ---------------------------
    with Session(engine) as finish_session:
        req = vm_request_repo.get_vm_request_by_id(
            session=finish_session, request_id=request_id, for_update=True,
        )
        if req is None:
            logger.error("Request %s vanished after provisioning VMID %s", request_id, new_vmid)
            raise NotFoundError(f"Request {request_id} no longer exists")

        resource_repo.create_resource(
            session=finish_session,
            vmid=new_vmid,
            user_id=request_user_id,
            environment_type=request_env_type,
            os_info=request_os_info,
            expiry_date=request_expiry_date,
            template_id=request_template_id,
            ssh_private_key_encrypted=plan.get("ssh_private_key_encrypted"),
            ssh_public_key=plan.get("ssh_public_key"),
            service_template_slug=request_service_template_slug,
            commit=False,
        )
        vm_request_repo.update_vm_request_provisioning(
            session=finish_session,
            db_request=req,
            vmid=new_vmid,
            assigned_node=desired_node or actual_node,
            desired_node=desired_node or actual_node,
            actual_node=actual_node,
            placement_strategy_used=plan["placement_strategy"],
            migration_status=(
                VMMigrationStatus.pending
                if desired_node and desired_node != actual_node
                else VMMigrationStatus.completed
            ),
            migration_error=None,
            commit=False,
        )
        req.status = VMRequestStatus.running
        finish_session.add(req)

        audit_service.log_action(
            session=finish_session,
            user_id=None,
            vmid=new_vmid,
            action="lxc_create" if request_resource_type == "lxc" else "vm_create",
            details=f"Provisioned {request_resource_type} for request {request_id} on {actual_node}",
            commit=False,
        )
        detected_runtime_pin = False
        try:
            detected_runtime_pin = _detect_migration_pinned(
                node=actual_node, vmid=new_vmid,
                resource_type="lxc" if request_resource_type == "lxc" else "qemu",
            )
        except Exception:
            logger.debug("Failed to detect migration pinning for VMID %s", new_vmid, exc_info=True)
        if _should_pin_request_for_auto_migration(
            request=req,
            detected_runtime_pin=detected_runtime_pin,
        ) and not request_migration_pinned:
            req.migration_pinned = True
            finish_session.add(req)
        finish_session.commit()

    logger.info(
        "Provisioned request %s → VMID %s on node %s",
        request_id, new_vmid, actual_node,
    )
    return new_vmid, actual_node, plan["placement_strategy"]


def _provision_via_service_template(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str, str | None]:
    """Provision LXC by running a community-scripts template (e.g. docker/nginx).

    The community script creates the container itself; we just trigger it
    synchronously via SSH then record the resulting vmid/node in our DB.
    """
    from app.core.security import decrypt_value
    from app.models import IpAllocation
    from app.services.network import script_deploy_service

    request_id = request.id
    request_user_id = request.user_id
    request_env_type = request.environment_type
    request_os_info = request.os_info
    request_expiry_date = request.expiry_date
    template_slug = str(request.service_template_slug or "")
    script_path = request.service_template_script_path or f"ct/{template_slug}.sh"
    hostname = request.hostname
    cores = int(request.cores or 2)
    memory = int(request.memory or 2048)
    disk = int(request.rootfs_size or 8)

    # Generate SSH key pair so the platform can manage the container after deploy
    from app.core.security import encrypt_value
    from app.infrastructure.ssh.client import generate_ed25519_keypair
    private_key_pem, public_key = generate_ed25519_keypair()
    encrypted_private_key = encrypt_value(private_key_pem)

    try:
        password_plain = decrypt_value(request.password)
    except Exception as exc:
        logger.error("Failed to decrypt password for request %s: %s", request_id, exc)
        raise

    # Mark provisioning and close txn before the long-running SSH deploy.
    request.status = VMRequestStatus.provisioning
    session.add(request)
    session.commit()
    logger.info(
        "Marked request %s as provisioning (service template %s)",
        request_id, template_slug,
    )

    # ── 預先分配 IP（必要：服務模板需要靜態 IP，不允許 silent fallback 到 DHCP）──
    # 使用 proxmox_service.next_vmid() 取得候選 CTID，分配 IP 後傳給 community-scripts。
    # 若部署後實際建立的 VMID 與候選不同，稍後會更新 IpAllocation.vmid 對應。
    from app.services.network import ip_management_service
    with Session(engine) as prep_session:
        try:
            net_cfg = ip_management_service.get_network_config_for_vm(prep_session)
        except Exception as exc:
            logger.error(
                "子網未設定或讀取失敗，服務模板部署中止（不使用 DHCP fallback）: %s",
                exc,
            )
            with Session(engine) as rb:
                req = vm_request_repo.get_vm_request_by_id(
                    session=rb, request_id=request_id, for_update=True,
                )
                if req and req.status == VMRequestStatus.provisioning:
                    req.status = VMRequestStatus.approved
                    rb.add(req)
                    rb.commit()
            raise RuntimeError(
                f"無法取得 IP 管理子網設定，請先到「網路 → IP 管理」設定子網：{exc}"
            ) from exc

        candidate_vmid = proxmox_service.next_vmid()
        try:
            allocated_ip = ip_management_service.allocate_ip(
                prep_session, candidate_vmid, "lxc",
            )
            prep_session.commit()
        except Exception as exc:
            prep_session.rollback()
            logger.error(
                "為候選 VMID %s 預留 IP 失敗（不使用 DHCP fallback）: %s",
                candidate_vmid, exc,
            )
            with Session(engine) as rb:
                req = vm_request_repo.get_vm_request_by_id(
                    session=rb, request_id=request_id, for_update=True,
                )
                if req and req.status == VMRequestStatus.provisioning:
                    req.status = VMRequestStatus.approved
                    rb.add(req)
                    rb.commit()
            raise RuntimeError(f"無法分配靜態 IP：{exc}") from exc

        logger.info(
            "已為候選 VMID %s 預留 IP %s (服務模板 %s)",
            candidate_vmid, allocated_ip, template_slug,
        )
        deploy_net = {
            "ip_cidr": f"{allocated_ip}/{net_cfg['prefix_len']}",
            "gateway": net_cfg.get("gateway"),
            "bridge": net_cfg.get("bridge_name"),
            "nameserver": net_cfg.get("dns_servers"),
        }

    try:
        new_vmid, _task = script_deploy_service.deploy_for_vm_request_sync(
            user_id=str(request_user_id),
            template_slug=template_slug,
            script_path=script_path,
            hostname=hostname,
            password=password_plain,
            cpu=cores,
            ram=memory,
            disk=disk,
            unprivileged=True,
            ssh=True,
            environment_type=request_env_type,
            os_info=request_os_info,
            net_config=deploy_net,
            ssh_public_key=public_key,
            request_id=str(request.id),
            candidate_vmid=candidate_vmid,
        )
    except Exception as exc:
        logger.error(
            "Script deploy failed for request %s (%s): %s",
            request_id, template_slug, exc,
        )
        # 回收已預留的 IP
        if candidate_vmid is not None:
            try:
                from app.services.network import ip_management_service
                with Session(engine) as rb_ip:
                    ip_management_service.release_ip(rb_ip, candidate_vmid)
                    rb_ip.commit()
                logger.info("已釋放部署失敗的預留 IP（候選 VMID %s）", candidate_vmid)
            except Exception as release_exc:
                logger.warning("釋放預留 IP 失敗: %s", release_exc)
        with Session(engine) as rb:
            req = vm_request_repo.get_vm_request_by_id(
                session=rb, request_id=request_id, for_update=True,
            )
            if req and req.status == VMRequestStatus.provisioning:
                req.status = VMRequestStatus.approved
                rb.add(req)
                rb.commit()
        raise

    try:
        info = proxmox_service.find_resource(new_vmid)
        actual_node = str(info.get("node") or "")
    except Exception:
        actual_node = ""

    # 若實際建立的 VMID 與候選不同，更新 IpAllocation 讓 vmid 指向真實容器
    if candidate_vmid is not None and new_vmid != candidate_vmid:
        update_ok = False
        try:
            # 先驗證 new_vmid 在 PVE 確實存在再重新指派
            proxmox_service.find_resource(new_vmid)
            with Session(engine) as fix_session:
                alloc = fix_session.exec(
                    select(IpAllocation).where(IpAllocation.vmid == candidate_vmid)
                ).first()
                if alloc is not None:
                    alloc.vmid = new_vmid
                    alloc.description = f"VMID {new_vmid}"
                    fix_session.add(alloc)
                    fix_session.commit()
                    update_ok = True
                    logger.info(
                        "已將 IpAllocation 從候選 VMID %s 更新為實際 VMID %s",
                        candidate_vmid, new_vmid,
                    )
                else:
                    update_ok = True  # 沒有可遷的紀錄，視為已完成
        except Exception as exc:
            logger.warning("更新 IpAllocation.vmid 失敗: %s", exc)

        if not update_ok:
            # 為避免 IP 洩漏，強制把候選 VMID 上的 IP 釋放
            try:
                from app.services.network import ip_management_service
                with Session(engine) as orphan_rb:
                    ip_management_service.release_ip(orphan_rb, candidate_vmid)
                    orphan_rb.commit()
                logger.info("已強制釋放孤立 IP（候選 VMID %s）", candidate_vmid)
            except Exception as release_exc:
                logger.error("孤立 IP 釋放失敗（候選 VMID %s）: %s", candidate_vmid, release_exc)

    with Session(engine) as finish_session:
        req = vm_request_repo.get_vm_request_by_id(
            session=finish_session, request_id=request_id, for_update=True,
        )
        if req is None:
            raise NotFoundError(f"Request {request_id} no longer exists")

        resource_repo.create_resource(
            session=finish_session,
            vmid=new_vmid,
            user_id=request_user_id,
            environment_type=request_env_type,
            os_info=request_os_info,
            expiry_date=request_expiry_date,
            ssh_private_key_encrypted=encrypted_private_key,
            ssh_public_key=public_key,
            service_template_slug=template_slug or None,
            commit=False,
        )
        vm_request_repo.update_vm_request_provisioning(
            session=finish_session,
            db_request=req,
            vmid=new_vmid,
            assigned_node=actual_node or None,
            desired_node=actual_node or None,
            actual_node=actual_node or None,
            placement_strategy_used="service_template",
            migration_status=VMMigrationStatus.completed,
            migration_error=None,
            commit=False,
        )
        req.status = VMRequestStatus.running
        finish_session.add(req)

        audit_service.log_action(
            session=finish_session,
            user_id=None,
            vmid=new_vmid,
            action="script_deploy",
            details=(
                f"Deployed service template {template_slug} for request "
                f"{request_id} on {actual_node or 'unknown'}"
            ),
            commit=False,
        )
        finish_session.commit()

    logger.info(
        "Service template deployed: request %s → VMID %s (%s) on %s",
        request_id, new_vmid, template_slug, actual_node,
    )
    return new_vmid, actual_node, "service_template"


def _mark_request_runtime_error(
    *,
    session: Session,
    request_id,
    message: str,
) -> None:
    scheduling_support.mark_request_runtime_error(
        session=session,
        request_id=request_id,
        message=message,
    )


def _refresh_actual_node(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[str, dict]:
    db_request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request.id,
        for_update=True,
    ) or request
    if request.vmid is None:
        raise NotFoundError(f"Request {request.id} has no provisioned VMID")
    resource = proxmox_service.find_resource(request.vmid)
    resource_name = str(resource.get("name") or "")
    # hostname is stored as punycode in DB since creation, so a direct
    # comparison is sufficient.
    expected_hostname = str(request.hostname or "")
    if resource_name != expected_hostname:
        raise NotFoundError(
            f"Provisioned resource {request.vmid} name '{resource_name}' "
            f"does not match request hostname '{expected_hostname}'"
        )
    actual_node = str(resource["node"])
    detected_runtime_pin = False
    try:
        detected_runtime_pin = _detect_migration_pinned(
            node=actual_node,
            vmid=int(request.vmid),
            resource_type=_resource_type_for_request(request),
        )
    except Exception:
        logger.debug(
            "Failed to refresh migration pinning for request %s (VMID %s)",
            request.id,
            request.vmid,
            exc_info=True,
        )
    if _should_pin_request_for_auto_migration(
        request=db_request,
        detected_runtime_pin=detected_runtime_pin,
    ) and not db_request.migration_pinned:
        db_request.migration_pinned = True
        session.add(db_request)
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=db_request,
        vmid=request.vmid,
        assigned_node=db_request.assigned_node,
        desired_node=db_request.desired_node,
        actual_node=actual_node,
        placement_strategy_used=db_request.placement_strategy_used,
        migration_status=(
            VMMigrationStatus.pending
            if db_request.desired_node and db_request.desired_node != actual_node
            else db_request.migration_status
        ),
        migration_error=(
            None
            if db_request.desired_node == actual_node
            else db_request.migration_error
        ),
        rebalance_epoch=db_request.rebalance_epoch,
        last_rebalanced_at=db_request.last_rebalanced_at,
        last_migrated_at=db_request.last_migrated_at,
        commit=False,
    )
    return actual_node, resource


# Migration sub-domain has been extracted to ``app.services.scheduling.migration``.
# Re-exported here so that callers and test monkey-patches against
# ``app.services.scheduling.coordinator.<name>`` keep working.
# ruff: noqa: E402, F401, I001
from app.services.scheduling.migration import (
    _detect_migration_pinned,
    _effective_request_migration_state,
    _extract_storage_id,
    _migrate_request_to_desired_node,
    _migration_block_reason,
    _process_claimed_migration_job,
    _process_pending_migration_jobs,
    _record_migration_gate_result,
    _storage_ids_available_on_node,
    _sync_request_migration_job,
)

def _adopt_or_provision_due_request(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[int, str | None, str | None, bool] | None:
    """Acquire lock, then adopt existing Proxmox resource or fully provision.

    Returns ``(vmid, actual_node, strategy, started)`` on success, or ``None``
    if the lock cannot be acquired (another worker has it) or the request has
    already been handled.
    """
    # SELECT FOR UPDATE SKIP LOCKED — skip if another session holds it.
    locked = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request.id,
        for_update=True,
        skip_locked=True,
    )
    if locked is None:
        return None
    # Re-check: another process may have set vmid or changed status.
    if locked.vmid is not None or locked.status == VMRequestStatus.provisioning:
        return None

    # Try adopting an existing Proxmox resource first.
    adopted = _adopt_existing_resource(session=session, request=locked)
    if adopted is not None:
        vmid, actual_node, strategy, started = adopted
        session.commit()
        return vmid, actual_node, strategy, started

    # Full provision: mark provisioning → clone outside txn → mark running.
    # _provision_new_resource manages its own sessions/commits.
    _provision_new_resource(session=session, request=locked)
    refreshed = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=locked.id,
    )
    if refreshed is None or refreshed.vmid is None:
        return None
    started = refreshed.status in (
        VMRequestStatus.provisioning,
        VMRequestStatus.running,
    )
    return (
        refreshed.vmid,
        refreshed.actual_node,
        refreshed.placement_strategy_used,
        started,
    )


def _ensure_request_running(
    *,
    session: Session,
    request: VMRequest,
    now: datetime,
    policy: _MigrationPolicy,
    migrations_used: int,
) -> tuple[bool, int]:
    """Make sure an approved/running request has a live VM.

    For requests without a vmid: lock → mark provisioning → clone → mark running.
    For requests with a vmid: ensure the VM is started.
    """
    resource_type = _resource_type_for_request(request)

    # ---- No VMID yet → need to provision ---------------------------------
    if request.vmid is None:
        outcome = _adopt_or_provision_due_request(session=session, request=request)
        if outcome is None:
            return False, migrations_used
        _vmid, outcome_actual_node, _strategy, started = outcome
        # If freshly provisioned on the desired node, mark migration as
        # completed so any "pending/idle" state left over from the most
        # recent active-window rebalance is reconciled.
        refreshed_after = vm_request_repo.get_vm_request_by_id(
            session=session, request_id=request.id,
        )
        if (
            refreshed_after is not None
            and refreshed_after.vmid is not None
            and refreshed_after.desired_node
            and outcome_actual_node
            and refreshed_after.desired_node == outcome_actual_node
            and refreshed_after.migration_status
            in (VMMigrationStatus.idle, VMMigrationStatus.pending)
        ):
            vm_request_repo.update_vm_request_provisioning(
                session=session,
                db_request=refreshed_after,
                vmid=refreshed_after.vmid,
                assigned_node=refreshed_after.assigned_node or outcome_actual_node,
                desired_node=refreshed_after.desired_node,
                actual_node=outcome_actual_node,
                placement_strategy_used=refreshed_after.placement_strategy_used,
                migration_status=VMMigrationStatus.completed,
                migration_error=None,
                rebalance_epoch=refreshed_after.rebalance_epoch,
                last_rebalanced_at=refreshed_after.last_rebalanced_at,
                last_migrated_at=refreshed_after.last_migrated_at,
                commit=False,
            )
            session.commit()
        return started, migrations_used

    # ---- Already provisioned → ensure VM is started ----------------------
    actual_node, _ = _refresh_actual_node(session=session, request=request)
    _sync_request_migration_job(
        session=session, request=request, source_node=actual_node, now=now,
    )
    request = vm_request_repo.get_vm_request_by_id(
        session=session, request_id=request.id, for_update=True,
    ) or request
    effective_migration_status, effective_migration_error = _effective_request_migration_state(
        session=session, request=request,
    )

    pve_status = proxmox_service.get_status(actual_node, request.vmid, resource_type)
    is_running = str(pve_status.get("status") or "").lower() == "running"
    if not is_running:
        proxmox_service.control(actual_node, request.vmid, resource_type, "start")

    # Ensure status is 'running' in DB.
    if request.status != VMRequestStatus.running:
        request.status = VMRequestStatus.running
        session.add(request)
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=request.desired_node or actual_node,
        desired_node=request.desired_node or actual_node,
        actual_node=actual_node,
        placement_strategy_used=request.placement_strategy_used,
        migration_status=(
            VMMigrationStatus.completed
            if request.desired_node and request.desired_node == actual_node
            else effective_migration_status
        ),
        migration_error=(
            None
            if request.desired_node and request.desired_node == actual_node
            else effective_migration_error
        ),
        rebalance_epoch=request.rebalance_epoch,
        last_rebalanced_at=request.last_rebalanced_at,
        last_migrated_at=request.last_migrated_at,
        commit=False,
    )
    if not is_running:
        audit_service.log_action(
            session=session,
            user_id=None,
            vmid=request.vmid,
            action="resource_start",
            details=f"Auto-started {request.resource_type} request {request.id}",
            commit=False,
        )
        logger.info(
            "Auto-started request %s on node %s with VMID %s",
            request.id, actual_node, request.vmid,
        )
    return not is_running, migrations_used


def _rebalance_active_window(now: datetime) -> int:
    with Session(engine) as session:
        policy = _get_migration_policy(session=session)
        due_requests = vm_request_repo.list_due_for_rebalance_vm_requests(
            session=session,
            at_time=now,
            interval_minutes=policy.active_rebalance_interval_minutes,
        )
        if not due_requests:
            return 0

        active_requests = vm_request_repo.list_active_approved_vm_requests(
            session=session,
            at_time=now,
        )
        if not active_requests:
            return 0

        selections = vm_request_placement_service.rebalance_active_assignments(
            session=session,
            requests=active_requests,
        )
        rebalance_epoch = max(
            (int(item.rebalance_epoch or 0) for item in active_requests),
            default=0,
        ) + 1

        for request in active_requests:
            selection = selections.get(request.id)
            if not selection or not selection.node:
                raise ValueError(
                    f"No feasible active placement exists for request {request.id}"
                )
            known_actual_node = request.actual_node
            if request.vmid is not None and not known_actual_node:
                known_actual_node = request.assigned_node
            vm_request_repo.update_vm_request_provisioning(
                session=session,
                db_request=request,
                vmid=request.vmid,
                assigned_node=selection.node,
                desired_node=selection.node,
                actual_node=known_actual_node,
                placement_strategy_used=selection.strategy,
                migration_status=(
                    VMMigrationStatus.pending
                    if request.vmid is not None
                    and known_actual_node
                    and known_actual_node != selection.node
                    else VMMigrationStatus.idle
                ),
                migration_error=None,
                rebalance_epoch=rebalance_epoch,
                last_rebalanced_at=now,
                commit=False,
            )
            _sync_request_migration_job(
                session=session,
                request=request,
                source_node=known_actual_node,
                now=now,
            )
        session.commit()
        return len(due_requests)


def process_single_request_start(request_id: uuid.UUID) -> bool:
    """Immediately trigger provisioning for a single approved request."""
    now = _utc_now()
    with Session(engine) as session:
        policy = _get_migration_policy(session=session)
        request = vm_request_repo.get_vm_request_by_id(
            session=session,
            request_id=request_id,
            for_update=True,
            skip_locked=True,
        )
        if not request or request.status not in (
            VMRequestStatus.approved,
            VMRequestStatus.running,
        ):
            return False
        try:
            started, _ = _ensure_request_running(
                session=session,
                request=request,
                now=now,
                policy=policy,
                migrations_used=0,
            )
            session.commit()
            return started
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to immediately provision request %s", request_id
            )
            return False


def process_due_request_starts() -> int:
    started_count = 0
    now = _utc_now()

    try:
        _rebalance_active_window(now)
    except ValueError:
        logger.exception("Failed to rebalance active VM request window")
    except Exception:
        logger.exception("Unexpected error while rebalancing active VM request window")

    with Session(engine) as session:
        policy = _get_migration_policy(session=session)
        active_requests = vm_request_repo.list_active_approved_vm_requests(
            session=session,
            at_time=now,
        )
        migrations_used = _process_pending_migration_jobs(
            session=session,
            now=now,
            policy=policy,
            active_requests=active_requests,
        )

        for request in active_requests:
            try:
                started, migrations_used = _ensure_request_running(
                    session=session,
                    request=request,
                    now=now,
                    policy=policy,
                    migrations_used=migrations_used,
                )
                if started:
                    started_count += 1
                session.commit()
            except NotFoundError:
                stale_vmid = request.vmid
                session.rollback()
                # Retry find_resource up to 3 times with a short delay
                # to tolerate transient Proxmox API hiccups.
                if stale_vmid is not None:
                    confirmed_gone = True
                    for attempt in range(3):
                        try:
                            proxmox_service.find_resource(stale_vmid)
                            confirmed_gone = False
                            break
                        except NotFoundError:
                            if attempt < 2:
                                time.sleep(2)
                    if not confirmed_gone:
                        logger.info(
                            "VMID %s still exists on Proxmox; "
                            "skipping recovery for request %s",
                            stale_vmid, request.id,
                        )
                        continue
                # VMID confirmed absent — clear and re-provision.
                try:
                    if stale_vmid is not None:
                        vm_request_repo.clear_vm_request_provisioning(
                            session=session,
                            db_request=request,
                            commit=False,
                        )
                        request.status = VMRequestStatus.approved
                        session.add(request)
                        session.commit()
                    started, migrations_used = _ensure_request_running(
                        session=session,
                        request=request,
                        now=now,
                        policy=policy,
                        migrations_used=migrations_used,
                    )
                    if started:
                        started_count += 1
                    session.commit()
                    logger.warning(
                        "Recovered request %s from stale VMID %s",
                        request.id, stale_vmid,
                    )
                except Exception as exc:
                    session.rollback()
                    _mark_request_runtime_error(
                        session=session,
                        request_id=request.id,
                        message=str(exc),
                    )
                    logger.exception(
                        "Failed to recover request %s from stale VMID %s",
                        request.id, stale_vmid,
                    )
            except Exception as exc:
                session.rollback()
                _mark_request_runtime_error(
                    session=session,
                    request_id=request.id,
                    message=str(exc),
                )
                logger.exception(
                    "Failed to reconcile approved request %s with VMID %s",
                    request.id,
                    request.vmid,
                )

    return started_count


def process_due_request_stops() -> int:
    stopped_count = 0
    now = _utc_now()

    with Session(engine) as session:
        _stop_statuses = (
            VMRequestStatus.approved,
            VMRequestStatus.provisioning,
            VMRequestStatus.running,
        )
        due_requests = list(
            session.exec(
                select(VMRequest).where(
                    VMRequest.status.in_(_stop_statuses),
                    VMRequest.vmid.is_not(None),
                    VMRequest.end_at.is_not(None),
                    VMRequest.end_at <= now,
                )
            ).all()
        )

        for request in due_requests:
            vmid = request.vmid
            if vmid is None:
                continue

            resource_type = _resource_type_for_request(request)

            try:
                resource = proxmox_service.find_resource(vmid)
                node = str(resource["node"])
                status = proxmox_service.get_status(node, vmid, resource_type)
                current_status = str(status.get("status") or "").lower()
                if current_status in {"stopped", "paused"}:
                    continue

                proxmox_service.control(node, vmid, resource_type, "shutdown")
                audit_service.log_action(
                    session=session,
                    user_id=None,
                    vmid=vmid,
                    action="resource_shutdown",
                    details=(
                        "Scheduled auto-shutdown for approved "
                        f"{request.resource_type} request {request.id}"
                    ),
                    commit=False,
                )
                stopped_count += 1
                logger.info(
                    "Auto-shutdown triggered for approved request %s on node %s with VMID %s",
                    request.id,
                    node,
                    vmid,
                )
            except NotFoundError:
                logger.debug(
                    "Scheduled shutdown skipped: resource %s not found for request %s, clearing vmid",
                    vmid,
                    request.id,
                )
                request.vmid = None
                session.add(request)
                session.commit()
            except Exception:
                logger.exception(
                    "Failed to auto-shutdown approved request %s with VMID %s",
                    request.id,
                    vmid,
                )

        if stopped_count > 0:
            session.commit()

    return stopped_count


async def run_scheduler(stop_event: asyncio.Event) -> None:
    logger.info("VM request scheduler is running")
    await run_polling_scheduler(
        stop_event=stop_event,
        interval_seconds=SCHEDULER_POLL_SECONDS,
        tasks=[
            ScheduledTask(name="process_due_request_starts", handler=process_due_request_starts),
            ScheduledTask(name="process_due_request_stops", handler=process_due_request_stops),
            ScheduledTask(name="process_pending_deletions", handler=process_pending_deletions_task),
            ScheduledTask(
                name="process_recurrence_windows",
                handler=recurrence_scheduler.process_recurrence_windows,
            ),
            ScheduledTask(
                name="process_scheduled_boot",
                handler=recurrence_scheduler.process_scheduled_boot,
            ),
            ScheduledTask(
                name="process_auto_stops",
                handler=recurrence_scheduler.process_auto_stops,
            ),
        ],
    )
    logger.info("VM request scheduler stopped")


def process_pending_deletions_task() -> int:
    """Scheduler tick：處理一筆 pending DeletionRequest（每 tick 最多一筆，避免長阻塞）。"""
    from app.services.resource import (
        deletion_service,  # noqa: PLC0415 — 避免 import cycle
    )

    try:
        with Session(engine) as session:
            deletion_service.process_pending_deletions(session)
        return 0
    except Exception:
        logger.exception("process_pending_deletions_task failed")
        return 0
