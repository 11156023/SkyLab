"""GPU (PCI resource mapping) service.

Wraps Proxmox /cluster/mapping/pci endpoints and provides GPU availability
and usage tracking by cross-referencing VM configurations.
"""

import logging
import re
import threading
import time

from sqlmodel import Session, select

from app.core.db import engine
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import get_proxmox_api
from app.models import Resource
from app.schemas.gpu import (
    GPUDeviceMap,
    GPUMappingDetail,
    GPUSummary,
    GPUUsageInfo,
)

logger = logging.getLogger(__name__)
_GPU_NODE_COUNTS_CACHE_TTL_SECONDS = 20
_gpu_node_counts_cache: dict[str, tuple[float, dict[str, int]]] = {}
_gpu_node_counts_cache_lock = threading.Lock()


def _get_managed_vmids() -> set[int]:
    """Return the set of VMIDs that are tracked in the SkyLab DB."""
    try:
        with Session(engine) as session:
            rows = session.exec(select(Resource.vmid)).all()
            return {int(v) for v in rows if v is not None}
    except Exception as e:
        logger.error("Failed to load managed VMIDs from DB: %s", e)
        return set()


def _parse_map_entry(entry: str) -> GPUDeviceMap:
    """Parse a PVE map entry string like 'node=pve1,path=0000:01:00.0,...'."""
    parts: dict[str, str] = {}
    for segment in entry.split(","):
        if "=" in segment:
            key, _, val = segment.partition("=")
            parts[key.strip()] = val.strip()
    # mdev=1 or mdev=true indicates mediated device (SR-IOV / vGPU)
    is_mdev = parts.get("mdev", "0") in ("1", "true")
    return GPUDeviceMap(
        node=parts.get("node", ""),
        path=parts.get("path", ""),
        id=parts.get("id", ""),
        subsystem_id=parts.get("subsystem-id"),
        iommu_group=int(parts["iommu_group"]) if "iommu_group" in parts else None,
        description=parts.get("description"),
        is_mdev=is_mdev,
    )


def _extract_gpu_info(description: str, mapping_id: str) -> tuple[str, str, int]:
    """Try to extract GPU model and VRAM from the description or mapping ID.

    Returns (model, vram_str, vram_mb).
    """
    text = description or mapping_id
    model = text
    vram = ""
    vram_mb = 0

    # Try to find VRAM pattern like "24GB", "12 GB", "8192MB"
    # 數字與空白都用有界量詞（而非 \d+、\s*）避免長輸入造成多項式回溯；
    # (?<!\d) 確保不會從長數字中間開始匹配
    vram_match = re.search(
        r"(?<!\d)(\d{1,6})\s{0,8}(GB|MB|GiB|MiB)", text, re.IGNORECASE
    )
    if vram_match:
        amount = int(vram_match.group(1))
        unit = vram_match.group(2).upper()
        if unit in ("MB", "MIB"):
            vram_mb = amount
            if amount < 1024:
                vram = f"{amount} MB"
            elif amount % 1024 == 0:
                vram = f"{amount // 1024} GB"
            else:
                gb_amount = amount / 1024
                vram = f"{gb_amount:.2f}".rstrip("0").rstrip(".") + " GB"
        else:
            vram = f"{amount} GB"
            vram_mb = amount * 1024

    return model, vram, vram_mb


def _count_physical_gpus(maps: list[GPUDeviceMap]) -> tuple[int, bool]:
    """Estimate physical GPU count by grouping PCI paths by bus number.

    PCI path format: DDDD:BB:DD.F  (domain:bus:device.function)
    - Different bus numbers → different physical GPUs
    - Same bus, different device/function → SR-IOV VFs of the same GPU

    Returns (physical_gpu_count, is_sriov).
    """
    buses: set[str] = set()
    for m in maps:
        path = m.path.strip()
        if not path:
            continue
        # Extract domain:bus portion (e.g. "0000:15" from "0000:15:01.3")
        parts = path.split(":")
        if len(parts) >= 2:
            bus_key = f"{parts[0]}:{parts[1]}"
        else:
            bus_key = path
        buses.add(bus_key)

    physical = max(len(buses), 1) if maps else 0
    is_sriov = len(maps) > len(buses) if buses else False
    return physical, is_sriov


def _parse_vram_from_mdev_description(description: str) -> int:
    """Extract VRAM in MB from an mdev type description.

    Examples:
      "GRID H200 NVL-16Q" → 16384 (16 GB)
      "GRID A100-40C"     → 40960 (40 GB)
      "NVIDIA A100-1-5C"  → 5120  (5 GB)
    """
    # Match the last number before a Q/C/A/B suffix at end-of-string
    match = re.search(r"[\-](\d+)[QCAB]\s*$", description.strip(), re.IGNORECASE)
    if match:
        return int(match.group(1)) * 1024
    # Fallback: generic "XX GB" pattern
    match = re.search(r"(\d+)\s*(GB|GiB)", description, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 1024
    return 0


def _get_mdev_types(node: str, pci_path: str) -> dict[str, int]:
    """Query available mdev types for a PCI device from PVE.

    Returns dict of mdev_type_name → vram_mb.
    """
    try:
        proxmox = get_proxmox_api()
        mdev_list = proxmox.nodes(node).hardware.pci(pci_path).mdev.get()
    except Exception as e:
        logger.debug("Cannot get mdev types for %s on %s: %s", pci_path, node, e)
        return {}

    result: dict[str, int] = {}
    for mdev in mdev_list:
        mdev_type = mdev.get("type", "")
        description = mdev.get("description", "")
        vram_mb = _parse_vram_from_mdev_description(description)
        if mdev_type:
            result[mdev_type] = vram_mb
    return result


def _resolve_vram_for_mapping(
    maps: list[GPUDeviceMap],
    has_mdev: bool,
    physical_gpu_count: int,
    description: str,
    mapping_id: str,
    used_by: list[GPUUsageInfo],
) -> tuple[int, int]:
    """Calculate total_vram_mb and used_vram_mb for a mapping.

    For passthrough: total = physical_count × per_card_vram, used = used_count × per_card_vram
    For vGPU/mdev:   total = max mdev profile VRAM × physical_count,
                     used  = sum of each VM's mdev profile VRAM
    Also sets allocated_vram_mb on each used_by entry.

    Returns (total_vram_mb, used_vram_mb).
    """
    _, _, per_card_vram_mb = _extract_gpu_info(description, mapping_id)

    if has_mdev and maps:
        # Query mdev types from the first available device on the first node
        first_map = maps[0]
        mdev_type_vram = _get_mdev_types(first_map.node, first_map.path)

        # Total VRAM = largest mdev profile (full-card profile) × physical GPUs
        positive_profile_vram = [v for v in mdev_type_vram.values() if v > 0]
        if positive_profile_vram:
            max_profile_vram = max(positive_profile_vram)
            total_vram_mb = max_profile_vram * physical_gpu_count
        elif per_card_vram_mb:
            total_vram_mb = per_card_vram_mb * physical_gpu_count
        else:
            total_vram_mb = 0

        # Used VRAM = sum of each assigned VM's mdev profile VRAM
        used_vram_mb = 0
        for u in used_by:
            if u.mdev_type and mdev_type_vram.get(u.mdev_type):
                u.allocated_vram_mb = mdev_type_vram[u.mdev_type]
            used_vram_mb += u.allocated_vram_mb

        return total_vram_mb, used_vram_mb

    # Passthrough: each used device consumes the full card VRAM
    total_vram_mb = per_card_vram_mb * physical_gpu_count
    used_vram_mb = per_card_vram_mb * len(used_by)
    for u in used_by:
        u.allocated_vram_mb = per_card_vram_mb
    return total_vram_mb, used_vram_mb


def list_gpu_mappings() -> list[GPUMappingDetail]:
    """List all PCI hardware mappings from PVE cluster."""
    try:
        proxmox = get_proxmox_api()
        raw_mappings = proxmox.cluster.mapping.pci.get()
    except Exception as e:
        logger.error("Failed to list PCI mappings: %s", e)
        raise ProxmoxError("Failed to list GPU mappings from Proxmox")

    # Get all VM configs to find GPU usage
    usage_map = _build_usage_map()
    managed_vmids = _get_managed_vmids()

    results: list[GPUMappingDetail] = []
    for mapping in raw_mappings:
        mapping_id = mapping.get("id", "")
        description = mapping.get("description", "")
        raw_maps = mapping.get("map", [])

        if isinstance(raw_maps, str):
            raw_maps = [raw_maps]

        maps = [_parse_map_entry(m) for m in raw_maps if isinstance(m, str)]

        used_by = usage_map.get(mapping_id, [])
        physical_gpu_count, is_sriov = _count_physical_gpus(maps)
        device_count = len(maps)
        used_count = len(used_by)
        available_count = max(0, device_count - used_count)
        has_mdev = any(m.is_mdev for m in maps)

        # Compute VRAM totals based on the FULL usage list (so counts stay correct
        # regardless of whether VMs are SkyLab-managed or external).
        total_vram_mb, used_vram_mb = _resolve_vram_for_mapping(
            maps, has_mdev, physical_gpu_count, description, mapping_id, used_by,
        )

        # Only expose SkyLab-managed VMs in the UI list.
        visible_used_by = [u for u in used_by if u.vmid in managed_vmids]

        results.append(
            GPUMappingDetail(
                id=mapping_id,
                description=description,
                maps=maps,
                physical_gpu_count=physical_gpu_count,
                device_count=device_count,
                used_count=used_count,
                available_count=available_count,
                is_sriov=is_sriov,
                has_mdev=has_mdev,
                total_vram_mb=total_vram_mb,
                used_vram_mb=used_vram_mb,
                used_by=visible_used_by,
            )
        )

    return results


def get_gpu_node_counts(mapping_id: str | None = None) -> dict[str, int]:
    """Return assignable GPU mapping slots by PVE node.

    Placement uses this lightweight view instead of a hand-maintained config
    so node GPU capacity follows Proxmox PCI resource mappings.
    """
    cache_key = str(mapping_id or "")
    now = time.monotonic()
    with _gpu_node_counts_cache_lock:
        cached = _gpu_node_counts_cache.get(cache_key)
        if cached and now - cached[0] <= _GPU_NODE_COUNTS_CACHE_TTL_SECONDS:
            return dict(cached[1])

    try:
        proxmox = get_proxmox_api()
        if mapping_id:
            raw_mappings = [proxmox.cluster.mapping.pci(str(mapping_id)).get()]
        else:
            raw_mappings = proxmox.cluster.mapping.pci.get()
    except Exception as e:
        logger.warning("Failed to load GPU mapping node counts: %s", e)
        return {}

    counts: dict[str, int] = {}
    for mapping in raw_mappings:
        raw_maps = mapping.get("map", []) if isinstance(mapping, dict) else []
        if isinstance(raw_maps, str):
            raw_maps = [raw_maps]

        for raw_map in raw_maps:
            if not isinstance(raw_map, str):
                continue
            parsed = _parse_map_entry(raw_map)
            node = str(parsed.node or "").strip()
            if not node:
                continue
            counts[node] = counts.get(node, 0) + 1

    with _gpu_node_counts_cache_lock:
        _gpu_node_counts_cache[cache_key] = (now, dict(counts))

    return counts


def get_gpu_mapping(mapping_id: str) -> GPUMappingDetail:
    """Get a single PCI mapping by ID."""
    try:
        proxmox = get_proxmox_api()
        mapping = proxmox.cluster.mapping.pci(mapping_id).get()
    except Exception as e:
        logger.error("Failed to get PCI mapping '%s': %s", mapping_id, e)
        raise NotFoundError(f"GPU mapping '{mapping_id}' not found")

    description = mapping.get("description", "")
    raw_maps = mapping.get("map", [])
    if isinstance(raw_maps, str):
        raw_maps = [raw_maps]

    maps = [_parse_map_entry(m) for m in raw_maps if isinstance(m, str)]
    usage_map = _build_usage_map()
    used_by = usage_map.get(mapping_id, [])
    physical_gpu_count, is_sriov = _count_physical_gpus(maps)
    device_count = len(maps)
    used_count = len(used_by)
    has_mdev = any(m.is_mdev for m in maps)

    total_vram_mb, used_vram_mb = _resolve_vram_for_mapping(
        maps, has_mdev, physical_gpu_count, description, mapping_id, used_by,
    )

    managed_vmids = _get_managed_vmids()
    visible_used_by = [u for u in used_by if u.vmid in managed_vmids]

    return GPUMappingDetail(
        id=mapping_id,
        description=description,
        maps=maps,
        physical_gpu_count=physical_gpu_count,
        device_count=device_count,
        used_count=used_count,
        available_count=max(0, device_count - used_count),
        is_sriov=is_sriov,
        has_mdev=has_mdev,
        total_vram_mb=total_vram_mb,
        used_vram_mb=used_vram_mb,
        used_by=visible_used_by,
    )


def create_gpu_mapping(
    *, mapping_id: str, description: str = "", map_entries: list[str]
) -> None:
    """Create a new PCI resource mapping."""
    try:
        proxmox = get_proxmox_api()
        proxmox.cluster.mapping.pci.post(
            id=mapping_id, description=description, **{"map": map_entries}
        )
    except Exception as e:
        logger.error("Failed to create PCI mapping '%s': %s", mapping_id, e)
        raise ProxmoxError(f"Failed to create GPU mapping: {e}")


def delete_gpu_mapping(mapping_id: str) -> None:
    """Delete a PCI resource mapping."""
    try:
        proxmox = get_proxmox_api()
        proxmox.cluster.mapping.pci(mapping_id).delete()
    except Exception as e:
        logger.error("Failed to delete PCI mapping '%s': %s", mapping_id, e)
        raise ProxmoxError(f"Failed to delete GPU mapping: {e}")


def list_gpu_options() -> list[GPUSummary]:
    """Return a simplified list of available GPUs for form selection.

    Cross-references mappings with current VM assignments to determine
    availability.
    """
    mappings = list_gpu_mappings()
    options: list[GPUSummary] = []

    for mapping in mappings:
        model, vram, _ = _extract_gpu_info(mapping.description, mapping.id)

        # Build node list from maps
        nodes = list({m.node for m in mapping.maps if m.node})
        node_str = ", ".join(sorted(nodes))

        options.append(
            GPUSummary(
                mapping_id=mapping.id,
                description=mapping.description,
                model=model,
                vram=vram,
                node=node_str,
                physical_gpu_count=mapping.physical_gpu_count,
                device_count=mapping.device_count,
                used_count=mapping.used_count,
                available_count=mapping.available_count,
                is_sriov=mapping.is_sriov,
                has_mdev=mapping.has_mdev,
                total_vram_mb=mapping.total_vram_mb,
                used_vram_mb=mapping.used_vram_mb,
            )
        )

    return options


def _build_usage_map() -> dict[str, list[GPUUsageInfo]]:
    """Scan all VMs to find which ones are using PCI resource mappings.

    Returns a dict mapping mapping_id → list of GPUUsageInfo.
    """
    usage: dict[str, list[GPUUsageInfo]] = {}

    try:
        proxmox = get_proxmox_api()
        all_resources = proxmox.cluster.resources.get(type="vm")
    except Exception as e:
        logger.warning("Failed to scan VM resources for GPU usage: %s", e)
        return usage

    for resource in all_resources:
        if resource.get("type") != "qemu":
            continue

        vmid = resource.get("vmid")
        node = resource.get("node", "")
        vm_name = resource.get("name", "")
        status = resource.get("status", "")

        if not vmid or not node:
            continue

        try:
            config = proxmox.nodes(node).qemu(vmid).config.get()
        except Exception:
            continue

        # Check hostpci0..hostpci15 for mapping= references
        for i in range(16):
            key = f"hostpci{i}"
            val = config.get(key)
            if not val:
                continue

            val_str = str(val)
            # Format: mapping=<mapping_id>,... or raw PCI address
            mapping_match = re.search(r"mapping=([^,\s]+)", val_str)
            if mapping_match:
                mid = mapping_match.group(1)
                # Extract mdev type if present (e.g. mdev=nvidia-1028)
                mdev_match = re.search(r"mdev=([^,\s]+)", val_str)
                mdev_type = mdev_match.group(1) if mdev_match else ""
                usage.setdefault(mid, []).append(
                    GPUUsageInfo(
                        vmid=vmid,
                        vm_name=vm_name,
                        node=node,
                        status=status,
                        mdev_type=mdev_type,
                    )
                )

    return usage
