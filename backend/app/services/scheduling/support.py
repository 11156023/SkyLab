from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.infrastructure.proxmox import get_proxmox_settings
from app.models import (
    VMMigrationJobStatus,
    VMMigrationStatus,
    VMRequest,
    VMRequestStatus,
)
from app.repositories import vm_migration_job as vm_migration_job_repo
from app.repositories import vm_request as vm_request_repo
from app.services.proxmox import proxmox_service
from app.services.scheduling import policy as scheduling_policy

logger = logging.getLogger(__name__)

_VM_DISK_PREFIXES = ("scsi", "sata", "ide", "virtio", "efidisk", "tpmstate")
_LXC_MOUNT_PREFIXES = ("rootfs", "mp")


def find_existing_resource_for_request(
    *,
    session: Session,
    request: VMRequest,
) -> dict | None:
    expected_type = scheduling_policy.resource_type_for_request(request)
    pool_name = get_proxmox_settings().pool_name
    claimed_vmids = {
        int(item.vmid)
        for item in session.exec(
            select(VMRequest).where(
                VMRequest.status == VMRequestStatus.approved,
                VMRequest.vmid.is_not(None),
                VMRequest.id != request.id,
            )
        ).all()
        if item.vmid is not None
    }
    # hostname is stored as punycode in DB since creation.
    expected_hostname = str(request.hostname or "")
    for resource in proxmox_service.list_all_resources():
        if str(resource.get("type") or "") != expected_type:
            continue
        resource_name = str(resource.get("name") or "")
        if resource_name != expected_hostname:
            continue
        vmid = int(resource.get("vmid"))
        if vmid in claimed_vmids:
            continue
        pool = str(resource.get("pool") or "")
        if pool and pool != pool_name:
            continue
        return resource
    return None


def mark_request_runtime_error(
    *,
    session: Session,
    request_id,
    message: str,
) -> None:
    request = vm_request_repo.get_vm_request_by_id(
        session=session,
        request_id=request_id,
        for_update=True,
    )
    if not request:
        return
    vm_request_repo.update_vm_request_provisioning(
        session=session,
        db_request=request,
        vmid=request.vmid,
        assigned_node=request.assigned_node,
        desired_node=request.desired_node,
        actual_node=request.actual_node,
        placement_strategy_used=request.placement_strategy_used,
        migration_status=VMMigrationStatus.failed,
        migration_error=message[:500],
        rebalance_epoch=request.rebalance_epoch,
        last_rebalanced_at=request.last_rebalanced_at,
        commit=False,
    )
    if any(
        keyword in message.lower()
        for keyword in ("no feasible", "capacity", "no node", "cannot fit")
    ):
        request.resource_warning = message[:500]
        session.add(request)
    session.commit()


def extract_storage_id(config_value: object) -> str | None:
    text = str(config_value or "").strip()
    if not text:
        return None
    if text.startswith("/"):
        return None
    if ":" not in text:
        return None
    return text.split(":", 1)[0].strip() or None


def _storage_is_shared(storage: dict) -> bool:
    shared = storage.get("shared")
    return shared in (1, "1", True, "true", "True")


def storage_ids_available_on_node(*, node: str) -> set[str]:
    return {
        str(item.get("storage") or item.get("id") or "").strip()
        for item in proxmox_service.list_node_storages(node)
        if str(item.get("storage") or item.get("id") or "").strip()
    }


def shared_storage_ids_on_node(*, node: str) -> set[str]:
    return {
        str(item.get("storage") or item.get("id") or "").strip()
        for item in proxmox_service.list_node_storages(node)
        if str(item.get("storage") or item.get("id") or "").strip()
        and _storage_is_shared(item)
    }


def detect_migration_pinned(
    *,
    node: str,
    vmid: int,
    resource_type: str,
    config: dict | None = None,
) -> bool:
    try:
        config = config or proxmox_service.get_config(node, vmid, resource_type)
    except Exception:
        return False

    if resource_type == "qemu":
        if any(key.startswith(("hostpci", "usb")) for key in config):
            return True
        for key, value in config.items():
            if not key.startswith(_VM_DISK_PREFIXES):
                continue
            if str(value or "").strip().startswith("/"):
                return True
    else:
        for key, value in config.items():
            if not key.startswith(_LXC_MOUNT_PREFIXES):
                continue
            if str(value or "").strip().startswith("/"):
                return True
    return False


def auto_migration_block_reason_for_request(
    *,
    request: VMRequest,
) -> str | None:
    resource_type = scheduling_policy.resource_type_for_request(request)
    if resource_type != "qemu":
        return (
            "Migration blocked because automatic migration is limited to QEMU VMs. "
            "LXC containers stay on their current node."
        )
    if str(getattr(request, "gpu_mapping_id", "") or "").strip():
        return (
            "Migration blocked because this VM uses a GPU mapping and should remain "
            "pinned to its current node."
        )
    return None


def should_pin_request_for_auto_migration(
    *,
    request: VMRequest,
    detected_runtime_pin: bool = False,
) -> bool:
    if getattr(request, "migration_pinned", False):
        return True
    if detected_runtime_pin:
        return True
    return auto_migration_block_reason_for_request(request=request) is not None


def migration_block_reason(
    *,
    source_node: str,
    target_node: str,
    vmid: int,
    resource_type: str,
    config: dict | None = None,
    source_storages: set[str] | None = None,
    target_storages: set[str] | None = None,
) -> str | None:
    config = config or proxmox_service.get_config(source_node, vmid, resource_type)
    source_storages = (
        source_storages
        if source_storages is not None
        else shared_storage_ids_on_node(node=source_node)
    )
    target_storages = (
        target_storages
        if target_storages is not None
        else shared_storage_ids_on_node(node=target_node)
    )

    if resource_type == "qemu":
        passthrough_keys = [
            key for key in config if key.startswith("hostpci") or key.startswith("usb")
        ]
        if passthrough_keys:
            return (
                "Migration blocked because this VM uses passthrough devices: "
                + ", ".join(sorted(passthrough_keys))
            )

        for key, value in config.items():
            if not key.startswith(_VM_DISK_PREFIXES):
                continue
            storage_id = extract_storage_id(value)
            if storage_id is None:
                if str(value or "").strip().startswith("/"):
                    return f"Migration blocked because disk '{key}' uses a direct path mount."
                continue
            if storage_id not in source_storages or storage_id not in target_storages:
                return (
                    f"Migration blocked because target node '{target_node}' "
                    f"does not expose explicitly shared storage '{storage_id}' "
                    f"required by disk '{key}'."
                )
        return None

    for key, value in config.items():
        if not key.startswith(_LXC_MOUNT_PREFIXES):
            continue
        text = str(value or "").strip()
        if text.startswith("/"):
            return (
                f"Migration blocked because container mount '{key}' is a direct bind mount."
            )
        storage_id = extract_storage_id(value)
        if storage_id and (
            storage_id not in source_storages or storage_id not in target_storages
        ):
            return (
                f"Migration blocked because target node '{target_node}' "
                f"does not expose explicitly shared storage '{storage_id}' "
                f"required by mount '{key}'."
            )
    return None


def migration_precheck_reason_for_request(
    *,
    request: VMRequest,
    current_node: str | None,
    target_node: str,
    policy: scheduling_policy.MigrationPolicy,
    now: datetime,
    safe: bool = False,
    config: dict | None = None,
    current_status: dict | None = None,
    target_storages: set[str] | None = None,
) -> str | None:
    normalized_current = str(current_node or "").strip()
    normalized_target = str(target_node or "").strip()
    if (
        request.vmid is None
        or not normalized_current
        or not normalized_target
        or normalized_current == normalized_target
    ):
        return None

    if not policy.enabled:
        return "Migration deferred because automatic migration is disabled by system policy."

    request_level_block_reason = auto_migration_block_reason_for_request(request=request)
    if request_level_block_reason is not None:
        return request_level_block_reason

    if getattr(request, "migration_pinned", False):
        return (
            "Migration blocked because this resource is pinned to its current node "
            "(hardware passthrough or local mount detected)."
        )

    last_migrated_at = scheduling_policy.normalize_datetime(request.last_migrated_at)
    if (
        last_migrated_at is not None
        and policy.min_interval_minutes > 0
        and now - last_migrated_at < timedelta(minutes=policy.min_interval_minutes)
    ):
        return "Migration deferred because this request was migrated too recently."

    resource_type = scheduling_policy.resource_type_for_request(request)

    try:
        current_config = config or proxmox_service.get_config(
            normalized_current,
            int(request.vmid),
            resource_type,
        )
        source_storages = shared_storage_ids_on_node(node=normalized_current)
        structural_block_reason = migration_block_reason(
            source_node=normalized_current,
            target_node=normalized_target,
            vmid=int(request.vmid),
            resource_type=resource_type,
            config=current_config,
            source_storages=source_storages,
            target_storages=target_storages,
        )
        if structural_block_reason is not None:
            return structural_block_reason

        if resource_type == "lxc" and not policy.lxc_live_enabled:
            status = current_status or proxmox_service.get_status(
                normalized_current,
                int(request.vmid),
                resource_type,
            )
            if str(status.get("status") or "").lower() == "running":
                return (
                    "Migration blocked because LXC live migration is disabled. "
                    "The container must be stopped before migrating, or enable "
                    "migration_lxc_live_enabled in system settings."
                )
        return None
    except Exception as exc:
        if safe:
            return (
                "Migration blocked because feasibility could not be verified: "
                f"{str(exc)[:200]}"
            )
        raise


def migration_allowed_target_nodes_for_request(
    *,
    request: VMRequest,
    candidate_nodes: list[str],
    current_node: str | None,
    policy: scheduling_policy.MigrationPolicy,
    now: datetime,
    safe: bool = False,
    target_storages_by_node: dict[str, set[str]] | None = None,
) -> tuple[set[str], dict[str, str]]:
    normalized_candidates = [
        str(node).strip()
        for node in candidate_nodes
        if str(node).strip()
    ]
    if not normalized_candidates:
        return set(), {}

    if request.vmid is None or not str(current_node or "").strip():
        return set(normalized_candidates), {}

    normalized_current = str(current_node or "").strip()
    allowed = {
        node for node in normalized_candidates
        if node == normalized_current
    }
    blocked: dict[str, str] = {}
    current_config: dict | None = None
    current_status: dict | None = None
    resource_type = scheduling_policy.resource_type_for_request(request)

    try:
        current_config = proxmox_service.get_config(
            normalized_current,
            int(request.vmid),
            resource_type,
        )
    except Exception as exc:
        if safe:
            reason = (
                "Migration blocked because feasibility could not be verified: "
                f"{str(exc)[:200]}"
            )
            for node in normalized_candidates:
                if node != normalized_current:
                    blocked[node] = reason
            return allowed, blocked
        raise

    if resource_type == "lxc" and not policy.lxc_live_enabled:
        try:
            current_status = proxmox_service.get_status(
                normalized_current,
                int(request.vmid),
                resource_type,
            )
        except Exception as exc:
            if safe:
                reason = (
                    "Migration blocked because feasibility could not be verified: "
                    f"{str(exc)[:200]}"
                )
                for node in normalized_candidates:
                    if node != normalized_current:
                        blocked[node] = reason
                return allowed, blocked
            raise

    for node in normalized_candidates:
        if node == normalized_current:
            continue
        reason = migration_precheck_reason_for_request(
            request=request,
            current_node=normalized_current,
            target_node=node,
            policy=policy,
            now=now,
            safe=safe,
            config=current_config,
            current_status=current_status,
            target_storages=(target_storages_by_node or {}).get(node),
        )
        if reason is None:
            allowed.add(node)
        else:
            blocked[node] = reason

    return allowed, blocked


def sync_request_migration_job(
    *,
    session: Session,
    request: VMRequest,
    source_node: str | None,
    now: datetime,
) -> None:
    desired_node = str(request.desired_node or request.assigned_node or "")
    actual_node = str(source_node or request.actual_node or request.assigned_node or "")
    if request.vmid is None:
        vm_migration_job_repo.cancel_pending_jobs_for_request(
            session=session,
            request_id=request.id,
            reason="Migration queue cleared because the request no longer has a VMID.",
            commit=False,
        )
        return
    if not desired_node:
        vm_migration_job_repo.cancel_pending_jobs_for_request(
            session=session,
            request_id=request.id,
            reason=(
                "Migration queue cleared because the request no longer has a "
                "desired target node."
            ),
            commit=False,
        )
        return
    if desired_node == actual_node:
        vm_migration_job_repo.cancel_pending_jobs_for_request(
            session=session,
            request_id=request.id,
            reason="Migration queue cleared because the request is already on the target node.",
            commit=False,
        )
        return

    latest_job = vm_migration_job_repo.get_latest_job_for_request(
        session=session,
        request_id=request.id,
    )
    if (
        latest_job is not None
        and latest_job.status in {VMMigrationJobStatus.failed, VMMigrationJobStatus.blocked}
        and int(latest_job.rebalance_epoch or 0) == int(request.rebalance_epoch or 0)
        and str(latest_job.target_node or "") == desired_node
    ):
        return
    if (
        latest_job is not None
        and latest_job.status in {VMMigrationJobStatus.pending, VMMigrationJobStatus.running}
        and int(latest_job.rebalance_epoch or 0) == int(request.rebalance_epoch or 0)
        and str(latest_job.target_node or "") == desired_node
        and str(latest_job.source_node or "") == actual_node
    ):
        return

    vm_migration_job_repo.create_or_update_pending_job(
        session=session,
        request_id=request.id,
        vmid=request.vmid,
        source_node=actual_node,
        target_node=desired_node,
        rebalance_epoch=int(request.rebalance_epoch or 0),
        last_error=None,
        requested_at=now,
        commit=False,
    )


def effective_request_migration_state(
    *,
    session: Session,
    request: VMRequest,
) -> tuple[VMMigrationStatus, str | None]:
    latest_job = vm_migration_job_repo.get_latest_job_for_request(
        session=session,
        request_id=request.id,
    )
    if latest_job is None:
        return request.migration_status, request.migration_error

    desired_node = str(request.desired_node or request.assigned_node or "")
    if (
        str(latest_job.target_node or "") != desired_node
        or int(latest_job.rebalance_epoch or 0) != int(request.rebalance_epoch or 0)
    ):
        return request.migration_status, request.migration_error

    mapped = {
        VMMigrationJobStatus.pending: VMMigrationStatus.pending,
        VMMigrationJobStatus.running: VMMigrationStatus.running,
        VMMigrationJobStatus.completed: VMMigrationStatus.completed,
        VMMigrationJobStatus.failed: VMMigrationStatus.failed,
        VMMigrationJobStatus.blocked: VMMigrationStatus.blocked,
        VMMigrationJobStatus.cancelled: request.migration_status,
    }
    return mapped.get(latest_job.status, request.migration_status), latest_job.last_error
