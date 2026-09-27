"""GPU (PCI resource mapping) management routes."""

from collections import Counter
from datetime import datetime

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.repositories import vm_request as vm_request_repo
from app.schemas.gpu import (
    GPUMappingCreate,
    GPUMappingDetail,
    GPUMappingsPublic,
    GPUSummary,
)
from app.services.proxmox import gpu_service

router = APIRouter(prefix="/gpu", tags=["gpu"])


@router.get("/mappings", response_model=GPUMappingsPublic)
def list_gpu_mappings(current_user: AdminUser):
    """List all GPU (PCI) resource mappings with usage info (admin only)."""
    mappings = gpu_service.list_gpu_mappings()
    return GPUMappingsPublic(data=mappings, count=len(mappings))


@router.get("/mappings/{mapping_id}", response_model=GPUMappingDetail)
def get_gpu_mapping(mapping_id: str, current_user: AdminUser):
    """Get details of a specific GPU mapping (admin only)."""
    return gpu_service.get_gpu_mapping(mapping_id)


@router.post("/mappings", status_code=201)
def create_gpu_mapping(body: GPUMappingCreate, current_user: AdminUser):
    """Create a new PCI resource mapping (admin only)."""
    gpu_service.create_gpu_mapping(
        mapping_id=body.id,
        description=body.description,
        map_entries=body.map,
    )
    return {"message": f"GPU mapping '{body.id}' created"}


@router.delete("/mappings/{mapping_id}")
def delete_gpu_mapping(mapping_id: str, current_user: AdminUser):
    """Delete a PCI resource mapping (admin only)."""
    gpu_service.delete_gpu_mapping(mapping_id)
    return {"message": f"GPU mapping '{mapping_id}' deleted"}


@router.get("/options", response_model=list[GPUSummary])
def list_gpu_options(
    current_user: CurrentUser,
    session: SessionDep,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    node: str | None = None,
):
    """List available GPU options for VM request forms.

    Returns a simplified list showing model, VRAM, and availability.
    ``node``（範本所在節點）指定時只回同一個 PVE 連線上的 GPU。
    """
    options = gpu_service.list_gpu_options(node=node)
    if not start_at or not end_at:
        return options

    # Keep response stable for invalid or reversed windows.
    if end_at <= start_at:
        return options

    overlapping = vm_request_repo.get_approved_vm_requests_overlapping_window(
        session=session,
        window_start=start_at,
        window_end=end_at,
    )
    reserved_counts = Counter(
        str(item.gpu_mapping_id)
        for item in overlapping
        # Running/provisioned VMs are already reflected by Proxmox runtime usage.
        if item.gpu_mapping_id and item.vmid is None
    )

    adjusted: list[GPUSummary] = []
    for option in options:
        reserved = int(reserved_counts.get(option.mapping_id, 0))
        if reserved <= 0:
            adjusted.append(option)
            continue

        adjusted.append(
            option.model_copy(
                update={
                    "used_count": min(option.device_count, option.used_count + reserved),
                    "available_count": max(0, option.available_count - reserved),
                }
            )
        )

    return adjusted
