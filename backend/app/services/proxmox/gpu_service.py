"""GPU (PCI resource mapping) service.

Wraps Proxmox /cluster/mapping/pci endpoints and provides GPU availability
and usage tracking by cross-referencing VM configurations.
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from sqlmodel import Session, select

from app.core.db import engine
from app.exceptions import NotFoundError, ProxmoxError
from app.infrastructure.proxmox import (
    get_connection_id_for_node,
    get_nodes_for_connection,
    get_proxmox_api_for_node,
)
from app.infrastructure.proxmox.operations import iter_connection_clients
from app.models import Resource
from app.schemas.gpu import (
    GPUDeviceMap,
    GPUMappingDetail,
    GPUProfileOption,
    GPUSummary,
    GPUUsageInfo,
)

logger = logging.getLogger(__name__)
_GPU_NODE_COUNTS_CACHE_TTL_SECONDS = 20
_gpu_node_counts_cache: dict[str, tuple[float, dict[str, int]]] = {}
_gpu_node_counts_cache_lock = threading.Lock()

# PVE 沒有批次讀 VM 設定的 API，只能一台一台問；序列化會讓 GPU 管理頁
# 隨叢集 VM 數線性變慢，因此併發送出並對整份掃描結果做短期快取。
_VM_CONFIG_FETCH_WORKERS = 12
_USAGE_MAP_CACHE_TTL_SECONDS = 15
_usage_map_cache: tuple[float, dict[str, list[GPUUsageInfo]]] | None = None
_usage_map_cache_lock = threading.Lock()

# mdev 探測同樣是每個 mapping 一次 HTTP；creatable 旗標會變動，只做短期快取。
_MDEV_TYPES_CACHE_TTL_SECONDS = 15
_mdev_types_cache: dict[tuple[str, str], tuple[float, dict[str, "MdevProfile"] | None]] = {}
_mdev_types_cache_lock = threading.Lock()

# 累積式 vGPU profile 目錄（type → 規格）。NVIDIA 驅動只回報「目前還建立得
# 起來」的 profile：記憶體吃緊時大 profile 會從清單消失，但已佔用 VM 的
# used VRAM 仍需要它們的規格。規格對同一驅動版本是靜態的，process 存活期間
# 看過一次就永久記住。
_mdev_profile_catalog: dict[str, "MdevProfile"] = {}


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

    # Try to find VRAM pattern like "24GB", "12 GB", "8192MB", "8G"
    # 數字與空白都用有界量詞（而非 \d+、\s*）避免長輸入造成多項式回溯；
    # (?<!\d) 確保不會從長數字中間開始匹配
    vram_match = re.search(
        r"(?<!\d)(\d{1,6})\s{0,8}(GiB|MiB|GB|MB|G|M)(?![A-Za-z])",
        text,
        re.IGNORECASE,
    )
    if vram_match:
        amount = int(vram_match.group(1))
        unit = vram_match.group(2).upper()
        if unit in ("MB", "MIB", "M"):
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


class MdevProfile(NamedTuple):
    """A vGPU (mdev) profile exposed by PVE for a PCI device."""

    name: str            # e.g. "NVIDIA H200-4C"
    vram_mb: int         # framebuffer per instance
    max_instances: int   # max instances per physical GPU (0 = unknown)


def _parse_vram_from_profile_text(text: str) -> int:
    """Extract VRAM in MB from a profile name or free-form description.

    Examples:
      "GRID H200 NVL-16Q" → 16384 (16 GB)
      "GRID A100-40C"     → 40960 (40 GB)
      "NVIDIA A100-1-5C"  → 5120  (5 GB)
    """
    # Match the last number before a Q/C/A/B suffix at end-of-string
    match = re.search(r"[\-](\d+)[QCAB]\s*$", text.strip(), re.IGNORECASE)
    if match:
        return int(match.group(1)) * 1024
    # Fallback: generic "XX GB" pattern
    match = re.search(r"(\d+)\s*(GB|GiB)", text, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 1024
    return 0


def _parse_mdev_entry(mdev: dict) -> MdevProfile:
    """Parse one entry of PVE's /nodes/{node}/hardware/pci/{path}/mdev.

    Modern NVIDIA SR-IOV vGPU (Ampere/Hopper) returns a key=value description:
      name: "NVIDIA H200-4C"
      description: "class=Compute\\nmax-instances=32\\n...\\nframebuffer-size=4096MiB\\n..."
    Legacy GRID drivers put the profile name in the description instead.
    """
    name = str(mdev.get("name", "") or "")
    description = str(mdev.get("description", "") or "")

    props: dict[str, str] = {}
    for line in re.split(r"[\n,]", description):
        key, _, val = line.partition("=")
        if val:
            props[key.strip().lower()] = val.strip()

    vram_mb = 0
    fb = props.get("framebuffer-size", "")
    fb_match = re.match(r"(\d+)\s*(MiB|MB|GiB|GB)?", fb, re.IGNORECASE)
    if fb_match and fb_match.group(1):
        amount = int(fb_match.group(1))
        unit = (fb_match.group(2) or "MiB").upper()
        vram_mb = amount * 1024 if unit in ("GB", "GIB") else amount
    if vram_mb <= 0:
        vram_mb = _parse_vram_from_profile_text(name) or _parse_vram_from_profile_text(
            description
        )

    try:
        max_instances = int(props.get("max-instances", "0"))
    except ValueError:
        max_instances = 0

    return MdevProfile(name=name, vram_mb=vram_mb, max_instances=max_instances)


def _fetch_mdev_types(node: str, pci_path: str) -> dict[str, MdevProfile] | None:
    """Query available mdev types for a PCI device from PVE (uncached)."""
    try:
        proxmox = get_proxmox_api_for_node(node)
        mdev_list = proxmox.nodes(node).hardware.pci(pci_path).mdev.get()
    except Exception as e:
        logger.debug("Cannot get mdev types for %s on %s: %s", pci_path, node, e)
        return None

    result: dict[str, MdevProfile] = {}
    for mdev in mdev_list:
        mdev_type = mdev.get("type", "")
        if mdev_type:
            result[mdev_type] = _parse_mdev_entry(mdev)
    _mdev_profile_catalog.update(result)
    return result


def _get_mdev_types(node: str, pci_path: str) -> dict[str, MdevProfile] | None:
    """Query available mdev types for a PCI device, with a short TTL cache.

    Returns dict of mdev_type (e.g. "nvidia-1436") → MdevProfile.
    注意語意：NVIDIA 驅動只回報「目前還建立得起來」的 profile——
    空 dict 代表查詢成功但已無法再建立任何 vGPU（記憶體已滿或非 vGPU 裝置）；
    None 代表查詢失敗（無法連線等），呼叫端應退回保守估計。

    每個 mapping 都要探測一次，列表頁一次就是數個 HTTP round trip；
    creatable 旗標會變動，所以只快取數秒，足以讓同一次列表與緊接著的
    自動刷新共用結果。
    """
    key = (node, pci_path)
    now = time.time()
    with _mdev_types_cache_lock:
        cached = _mdev_types_cache.get(key)
        if cached and now - cached[0] < _MDEV_TYPES_CACHE_TTL_SECONDS:
            return dict(cached[1]) if cached[1] is not None else None

    result = _fetch_mdev_types(node, pci_path)
    with _mdev_types_cache_lock:
        _mdev_types_cache[key] = (time.time(), result)
    return dict(result) if result is not None else None


def _prefetch_mdev_types(maps_per_mapping: list[list[GPUDeviceMap]]) -> None:
    """並行預熱 mdev 探測快取，讓後續逐 mapping 的解析不再各自等待 HTTP。"""
    targets = {
        (maps[0].node, maps[0].path)
        for maps in maps_per_mapping
        if maps and maps[0].node and maps[0].path
    }
    if len(targets) <= 1:
        return
    with ThreadPoolExecutor(max_workers=min(len(targets), _VM_CONFIG_FETCH_WORKERS)) as pool:
        list(pool.map(lambda t: _get_mdev_types(*t), targets))


class MappingVramInfo(NamedTuple):
    """VRAM / capacity accounting for a PCI resource mapping."""

    total_vram_mb: int
    used_vram_mb: int
    used_vram_known: bool       # False 表示部分 running profile 規格未知，used 為低估
    instance_capacity: int      # max assignable vGPU instances (0 = unknown / N/A)
    per_instance_vram_mb: int   # framebuffer per vGPU instance (0 = unknown / 混合)
    mdev_profile: str           # 顯示用 profile 名稱；混合 profile 時為空字串
    has_mdev: bool              # True if this mapping operates in vGPU/mdev mode
    available_override: int | None  # 混合 profile 時以剩餘 VRAM 推得的可用數
    profiles: list[GPUProfileOption]  # 可選 profile 清單（creatable 標記即時性）


def _resolve_vram_for_mapping(
    maps: list[GPUDeviceMap],
    physical_gpu_count: int,
    description: str,
    mapping_id: str,
    used_by: list[GPUUsageInfo],
) -> MappingVramInfo:
    """Calculate VRAM totals and vGPU instance capacity for a mapping.

    For passthrough: total = physical_count × per_card_vram, used = used_count × per_card_vram
    For vGPU/mdev:   capacity 以 framebuffer 為準——
                     total = 參考 profile 的 framebuffer × max-instances × physical_count,
                     used  = sum of each VM's mdev profile VRAM。
                     參考 profile 優先取「已掛載 VM 正在用的 profile」（NVIDIA 同一張
                     實體卡上 profile 必須一致），沒有已掛載 VM 時取 framebuffer 最大者。
    Also sets allocated_vram_mb on each used_by entry.

    mdev 模式判定：PVE 的 map entry 通常**不帶** mdev 旗標，必須實際查詢
    mdev endpoint（回非空清單即為 vGPU 裝置），或看已掛載 VM 是否帶 mdev type。
    """
    _, _, per_card_vram_mb = _extract_gpu_info(description, mapping_id)

    # Query mdev types from the first available device on the first node
    probed: dict[str, MdevProfile] | None = None
    if maps:
        first_map = maps[0]
        probed = _get_mdev_types(first_map.node, first_map.path)
    profiles = probed or {}

    has_mdev = (
        bool(profiles)
        or any(m.is_mdev for m in maps)
        or any(u.mdev_type for u in used_by)
    )

    if has_mdev and maps:
        # 累積目錄補足規格：creatable 清單可能已隱藏被佔滿的大 profile
        catalog = {**_mdev_profile_catalog, **profiles}

        # 逐 VM 標記配置量；used VRAM 只累計 running（關機設定不佔 framebuffer）
        used_vram_mb = 0
        used_vram_known = True
        for u in used_by:
            profile = catalog.get(u.mdev_type) if u.mdev_type else None
            if profile and profile.vram_mb > 0:
                u.allocated_vram_mb = profile.vram_mb
            if u.status == "running":
                if u.mdev_type and u.allocated_vram_mb <= 0:
                    used_vram_known = False
                used_vram_mb += u.allocated_vram_mb

        # 顯示用 profile：所有已掛載 VM 同一種 → 用它；沒掛載且目錄唯一 → 用它；
        # 混合 profile → 不顯示單一規格
        used_types = {u.mdev_type for u in used_by if u.mdev_type}
        display: MdevProfile | None = None
        if len(used_types) == 1:
            display = catalog.get(next(iter(used_types)))
            if display is not None and display.vram_mb <= 0:
                display = None
        elif not used_types and len(catalog) == 1:
            display = next(iter(catalog.values()))

        # 總可切 VRAM：目錄中最大的 framebuffer × max-instances（≈ 卡的可用量，
        # 例如 H200 141GB 標稱 → 140GB 可切）
        sized = [p for p in catalog.values() if p.vram_mb > 0 and p.max_instances > 0]
        if sized:
            total_vram_mb = (
                max(p.vram_mb * p.max_instances for p in sized) * physical_gpu_count
            )
        elif display and display.vram_mb > 0:
            total_vram_mb = display.vram_mb * physical_gpu_count
        elif per_card_vram_mb:
            total_vram_mb = per_card_vram_mb * physical_gpu_count
        else:
            total_vram_mb = 0

        # 實例數上限：同 profile 情境用該 profile 的 max-instances；
        # 未掛載時用目錄中最寬鬆者（最小 profile）當樂觀上限
        capacity_profile = display if display and display.max_instances > 0 else None
        if capacity_profile is None and sized:
            capacity_profile = max(sized, key=lambda p: p.max_instances)

        if probed is not None and not probed:
            # 查詢成功但驅動回報無任何可建立的 profile：
            # 記憶體已滿（NVIDIA 只列出「還放得下」的 profile），不可再指派
            instance_capacity = max(len(used_by), 1)
        elif capacity_profile is not None:
            instance_capacity = capacity_profile.max_instances * physical_gpu_count
        else:
            instance_capacity = 0

        # 混合 profile：實例上限無單一答案，改以剩餘 VRAM ÷ 最小可建 profile 估可用數
        available_override: int | None = None
        if len(used_types) > 1 and used_vram_known and total_vram_mb > 0:
            creatable_sizes = [p.vram_mb for p in profiles.values() if p.vram_mb > 0]
            if creatable_sizes:
                smallest = min(creatable_sizes)
                remaining = max(0, total_vram_mb - used_vram_mb)
                free_vf = max(0, len(maps) - len(used_by))
                available_override = min(free_vf, remaining // smallest)

        # 可選 profile 清單：目錄全集，creatable 以本次探測結果即時標記
        profile_options = sorted(
            (
                GPUProfileOption(
                    mdev_type=t,
                    name=p.name,
                    vram_mb=p.vram_mb,
                    max_instances=p.max_instances,
                    creatable=t in profiles,
                )
                for t, p in catalog.items()
            ),
            key=lambda o: (o.vram_mb, o.mdev_type),
        )

        return MappingVramInfo(
            total_vram_mb=total_vram_mb,
            used_vram_mb=used_vram_mb,
            used_vram_known=used_vram_known,
            instance_capacity=instance_capacity,
            per_instance_vram_mb=display.vram_mb if display else 0,
            mdev_profile=display.name if display else "",
            has_mdev=True,
            available_override=available_override,
            profiles=profile_options,
        )

    # Passthrough: each used device consumes the full card VRAM
    total_vram_mb = per_card_vram_mb * physical_gpu_count
    used_vram_mb = per_card_vram_mb * len(used_by)
    for u in used_by:
        u.allocated_vram_mb = per_card_vram_mb
    return MappingVramInfo(
        total_vram_mb=total_vram_mb,
        used_vram_mb=used_vram_mb,
        used_vram_known=True,
        instance_capacity=0,
        per_instance_vram_mb=0,
        mdev_profile="",
        has_mdev=False,
        available_override=None,
        profiles=[],
    )


def list_gpu_mappings() -> list[GPUMappingDetail]:
    """List all PCI hardware mappings across all Proxmox connections."""
    raw_mappings: list[dict] = []
    reached_any = False
    for _key, proxmox in iter_connection_clients():
        try:
            raw_mappings.extend(proxmox.cluster.mapping.pci.get())
            reached_any = True
        except Exception as e:
            logger.warning("Failed to list PCI mappings on connection %s: %s", _key, e)
    if not reached_any:
        logger.error("Failed to list PCI mappings: no reachable connection")
        raise ProxmoxError("Failed to list GPU mappings from Proxmox")

    # Get all VM configs to find GPU usage
    usage_map = _build_usage_map()
    managed_vmids = _get_managed_vmids()

    parsed: list[tuple[dict, list[GPUDeviceMap]]] = []
    for mapping in raw_mappings:
        raw_maps = mapping.get("map", [])
        if isinstance(raw_maps, str):
            raw_maps = [raw_maps]
        parsed.append(
            (mapping, [_parse_map_entry(m) for m in raw_maps if isinstance(m, str)])
        )

    # 逐 mapping 探測 mdev 會串成一長串 HTTP，先併發預熱共用快取。
    _prefetch_mdev_types([maps for _mapping, maps in parsed])

    results: list[GPUMappingDetail] = []
    for mapping, maps in parsed:
        mapping_id = mapping.get("id", "")
        description = mapping.get("description", "")

        used_by = usage_map.get(mapping_id, [])
        physical_gpu_count, is_sriov = _count_physical_gpus(maps)
        device_count = len(maps)
        used_count = len(used_by)

        # Compute VRAM totals based on the FULL usage list (so counts stay correct
        # regardless of whether VMs are SkyLab-managed or external).
        vram_info = _resolve_vram_for_mapping(
            maps, physical_gpu_count, description, mapping_id, used_by,
        )
        has_mdev = vram_info.has_mdev
        if vram_info.available_override is not None:
            available_count = vram_info.available_override
            capacity_count = used_count + available_count
        else:
            capacity_count = _effective_capacity(device_count, vram_info)
            available_count = max(0, capacity_count - used_count)

        # Only expose SkyLab-managed VMs in the UI list.
        visible_used_by = [u for u in used_by if u.vmid in managed_vmids]

        results.append(
            GPUMappingDetail(
                id=mapping_id,
                description=description,
                maps=maps,
                physical_gpu_count=physical_gpu_count,
                device_count=device_count,
                capacity_count=capacity_count,
                used_count=used_count,
                available_count=available_count,
                is_sriov=is_sriov,
                has_mdev=has_mdev,
                total_vram_mb=vram_info.total_vram_mb,
                used_vram_mb=vram_info.used_vram_mb,
                used_vram_known=vram_info.used_vram_known,
                per_instance_vram_mb=vram_info.per_instance_vram_mb,
                mdev_profile=vram_info.mdev_profile,
                profiles=vram_info.profiles,
                used_by=visible_used_by,
            )
        )

    return results


def _effective_capacity(device_count: int, vram_info: MappingVramInfo) -> int:
    """實際可指派上限：SR-IOV vGPU 受 framebuffer（max-instances）限制。

    VF 插槽數只是掛載點上限；vGPU 實例數同時受 profile 的 max-instances
    （= 卡上 framebuffer ÷ profile 大小）限制，取兩者較小值。
    查不到 profile 資訊時退回 VF 數。
    """
    if vram_info.has_mdev and vram_info.instance_capacity > 0:
        return min(device_count, vram_info.instance_capacity)
    return device_count


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

    raw_mappings = []
    for _key, proxmox in iter_connection_clients():
        try:
            if mapping_id:
                raw_mappings.append(proxmox.cluster.mapping.pci(str(mapping_id)).get())
            else:
                raw_mappings.extend(proxmox.cluster.mapping.pci.get())
        except Exception as e:
            logger.debug(
                "GPU mapping node counts unavailable on connection %s: %s", _key, e
            )
    if not raw_mappings:
        logger.warning("Failed to load GPU mapping node counts: no data")
        return {}

    counts: dict[str, int] = {}
    for mapping in raw_mappings:
        raw_maps = mapping.get("map", []) if isinstance(mapping, dict) else []
        if isinstance(raw_maps, str):
            raw_maps = [raw_maps]

        parsed_maps = [
            _parse_map_entry(raw_map)
            for raw_map in raw_maps
            if isinstance(raw_map, str)
        ]

        # 各節點的 VF/裝置插槽數與實體卡數（同 bus = 同一張卡）
        node_slots: dict[str, int] = {}
        node_buses: dict[str, set[str]] = {}
        for parsed in parsed_maps:
            node = str(parsed.node or "").strip()
            if not node:
                continue
            node_slots[node] = node_slots.get(node, 0) + 1
            path_parts = parsed.path.split(":")
            bus_key = (
                f"{path_parts[0]}:{path_parts[1]}"
                if len(path_parts) >= 2
                else parsed.path
            )
            node_buses.setdefault(node, set()).add(bus_key)

        # SR-IOV vGPU：插槽數再受 profile max-instances × 實體卡數限制。
        # map entry 不帶 mdev 旗標，一律探測 mdev endpoint（非 vGPU 裝置回空清單）
        max_instances = 0
        if parsed_maps:
            first = parsed_maps[0]
            profiles = _get_mdev_types(first.node, first.path) or {}
            usable = [p for p in profiles.values() if p.max_instances > 0]
            if usable:
                # 樂觀上限：取實例數最多（最小）的 profile
                max_instances = max(p.max_instances for p in usable)

        for node, slots in node_slots.items():
            capacity = slots
            if max_instances > 0:
                capacity = min(slots, max_instances * len(node_buses[node]))
            counts[node] = counts.get(node, 0) + capacity

    with _gpu_node_counts_cache_lock:
        _gpu_node_counts_cache[cache_key] = (now, dict(counts))

    return counts


def get_gpu_mapping(mapping_id: str) -> GPUMappingDetail:
    """Get a single PCI mapping by ID (searched across all connections)."""
    mapping = None
    for _key, proxmox in iter_connection_clients():
        try:
            mapping = proxmox.cluster.mapping.pci(mapping_id).get()
            break
        except Exception as e:
            logger.debug(
                "PCI mapping '%s' not found on connection %s: %s", mapping_id, _key, e
            )
    if mapping is None:
        logger.error("Failed to get PCI mapping '%s'", mapping_id)
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

    vram_info = _resolve_vram_for_mapping(
        maps, physical_gpu_count, description, mapping_id, used_by,
    )
    has_mdev = vram_info.has_mdev
    if vram_info.available_override is not None:
        available_count = vram_info.available_override
        capacity_count = used_count + available_count
    else:
        capacity_count = _effective_capacity(device_count, vram_info)
        available_count = max(0, capacity_count - used_count)

    managed_vmids = _get_managed_vmids()
    visible_used_by = [u for u in used_by if u.vmid in managed_vmids]

    return GPUMappingDetail(
        id=mapping_id,
        description=description,
        maps=maps,
        physical_gpu_count=physical_gpu_count,
        device_count=device_count,
        capacity_count=capacity_count,
        used_count=used_count,
        available_count=available_count,
        is_sriov=is_sriov,
        has_mdev=has_mdev,
        total_vram_mb=vram_info.total_vram_mb,
        used_vram_mb=vram_info.used_vram_mb,
        used_vram_known=vram_info.used_vram_known,
        per_instance_vram_mb=vram_info.per_instance_vram_mb,
        mdev_profile=vram_info.mdev_profile,
        profiles=vram_info.profiles,
        used_by=visible_used_by,
    )


def _mapping_target_node(map_entries: list[str]) -> str | None:
    """從 map entry（``node=pve1,path=...``）解析出目標節點名稱。"""
    for entry in map_entries:
        for part in str(entry).split(","):
            key, _, value = part.partition("=")
            if key.strip() == "node" and value.strip():
                return value.strip()
    return None


def create_gpu_mapping(
    *, mapping_id: str, description: str = "", map_entries: list[str]
) -> None:
    """Create a new PCI resource mapping（在 map entry 指定節點所屬的連線上建立）。"""
    try:
        node = _mapping_target_node(map_entries)
        proxmox = (
            get_proxmox_api_for_node(node) if node else next(
                client for _key, client in iter_connection_clients()
            )
        )
        proxmox.cluster.mapping.pci.post(
            id=mapping_id, description=description, **{"map": map_entries}
        )
    except Exception as e:
        logger.error("Failed to create PCI mapping '%s': %s", mapping_id, e)
        raise ProxmoxError(f"Failed to create GPU mapping: {e}")


def delete_gpu_mapping(mapping_id: str) -> None:
    """Delete a PCI resource mapping（逐一連線尋找並刪除）。"""
    deleted = False
    last_error: Exception | None = None
    for _key, proxmox in iter_connection_clients():
        try:
            proxmox.cluster.mapping.pci(mapping_id).delete()
            deleted = True
        except Exception as e:
            last_error = e
            logger.debug(
                "PCI mapping '%s' delete skipped on connection %s: %s",
                mapping_id, _key, e,
            )
    if not deleted:
        logger.error("Failed to delete PCI mapping '%s': %s", mapping_id, last_error)
        raise ProxmoxError(f"Failed to delete GPU mapping: {last_error}")


def _cluster_nodes_for_node(node: str | None) -> set[str] | None:
    """回傳指定節點所屬連線（叢集）的節點集合；無法判定時回 None（不過濾）。

    clone 不可跨連線：範本在哪個連線，GPU 也必須掛在同連線的節點上。
    節點未登錄在 proxmox_nodes（例如舊版單連線環境未同步）時保守地
    不過濾，避免把所有 GPU 藏起來。
    """
    node = str(node or "").strip()
    if not node:
        return None
    cluster_nodes = get_nodes_for_connection(get_connection_id_for_node(node))
    # 節點不在自己連線的節點集合裡代表映射資料不可用/未登錄 → 不過濾
    if node not in cluster_nodes:
        return None
    return cluster_nodes


def list_gpu_options(node: str | None = None) -> list[GPUSummary]:
    """Return a simplified list of available GPUs for form selection.

    Cross-references mappings with current VM assignments to determine
    availability.  ``node`` 指定範本所在節點時，只回傳同一個 PVE
    連線（叢集）上的 GPU mapping。
    """
    mappings = list_gpu_mappings()
    allowed_nodes = _cluster_nodes_for_node(node)
    options: list[GPUSummary] = []

    for mapping in mappings:
        # Build node list from maps
        nodes = list({m.node for m in mapping.maps if m.node})
        if allowed_nodes is not None and nodes and not (set(nodes) & allowed_nodes):
            continue

        model, vram, _ = _extract_gpu_info(mapping.description, mapping.id)
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
                capacity_count=mapping.capacity_count,
                used_count=mapping.used_count,
                available_count=mapping.available_count,
                is_sriov=mapping.is_sriov,
                has_mdev=mapping.has_mdev,
                total_vram_mb=mapping.total_vram_mb,
                used_vram_mb=mapping.used_vram_mb,
                used_vram_known=mapping.used_vram_known,
                per_instance_vram_mb=mapping.per_instance_vram_mb,
                mdev_profile=mapping.mdev_profile,
                profiles=mapping.profiles,
            )
        )

    return options


def _scan_usage_map() -> dict[str, list[GPUUsageInfo]]:
    """Scan all VMs to find which ones are using PCI resource mappings.

    Returns a dict mapping mapping_id → list of GPUUsageInfo.
    """
    usage: dict[str, list[GPUUsageInfo]] = {}

    # (resource, 所屬連線 client)：VM 設定必須用資源所在連線查詢，
    # 不能沿用迴圈跑完後殘留的最後一個 client
    all_resources: list[tuple[dict[str, Any], Any]] = []
    for _key, proxmox in iter_connection_clients():
        try:
            all_resources.extend(
                (r, proxmox) for r in proxmox.cluster.resources.get(type="vm")
            )
        except Exception as e:
            logger.warning(
                "Failed to scan VM resources for GPU usage on connection %s: %s",
                _key, e,
            )
    if not all_resources:
        return usage

    targets: list[tuple[dict[str, Any], Any]] = []
    for resource, proxmox in all_resources:
        if resource.get("type") != "qemu":
            continue
        if not resource.get("vmid") or not resource.get("node"):
            continue
        targets.append((resource, proxmox))
    if not targets:
        return usage

    def fetch(item: tuple[dict[str, Any], Any]) -> tuple[dict[str, Any], dict | None]:
        resource, proxmox = item
        try:
            config = proxmox.nodes(resource["node"]).qemu(resource["vmid"]).config.get()
        except Exception:
            return resource, None
        return resource, config

    # 每台 VM 各一次 HTTP，序列跑會讓整頁隨叢集規模線性變慢，改為併發送出。
    workers = min(len(targets), _VM_CONFIG_FETCH_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(fetch, targets))

    for resource, config in fetched:
        if not config:
            continue

        vmid = resource["vmid"]
        node = resource["node"]
        vm_name = resource.get("name", "")
        status = resource.get("status", "")

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


def _build_usage_map() -> dict[str, list[GPUUsageInfo]]:
    """TTL 快取版的 VM → mapping 使用狀況掃描。

    掃描成本正比於叢集 VM 數，而列表頁與 /gpu/options 會在短時間內重複要同
    一份資料（自動刷新、申請表單），因此快取數秒。回傳深拷貝，避免呼叫端
    在 ``_resolve_vram_for_mapping`` 內寫入 allocated_vram_mb 污染快取。
    """
    global _usage_map_cache

    now = time.time()
    with _usage_map_cache_lock:
        cached = _usage_map_cache
    if cached and now - cached[0] < _USAGE_MAP_CACHE_TTL_SECONDS:
        return {mid: [u.model_copy() for u in items] for mid, items in cached[1].items()}

    usage = _scan_usage_map()
    with _usage_map_cache_lock:
        _usage_map_cache = (time.time(), usage)
    return {mid: [u.model_copy() for u in items] for mid, items in usage.items()}
