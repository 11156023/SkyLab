"""GPU resource mapping schemas."""

from pydantic import BaseModel, Field


class GPUProfileOption(BaseModel):
    """一種 vGPU profile 選項（供申請表單選擇規格）。"""

    mdev_type: str = Field(description="PVE mdev type，例如 'nvidia-1436'")
    name: str = Field(default="", description="profile 名稱，例如 'NVIDIA H200-4C'")
    vram_mb: int = Field(default=0, description="每 instance framebuffer (MB)")
    max_instances: int = Field(default=0, description="單張實體卡最大 instance 數")
    creatable: bool = Field(default=False, description="目前記憶體是否還放得下這個 profile")


class GPUDeviceMap(BaseModel):
    """A single node-level mapping entry for a GPU resource mapping."""

    node: str
    path: str
    id: str = ""
    subsystem_id: str | None = None
    iommu_group: int | None = None
    description: str | None = None
    is_mdev: bool = False


class GPUMappingPublic(BaseModel):
    """Public representation of a PVE PCI resource mapping (GPU)."""

    id: str = Field(description="Mapping logical ID (name)")
    description: str = ""
    maps: list[GPUDeviceMap] = Field(default_factory=list)


class GPUMappingDetail(GPUMappingPublic):
    """Detail view with usage information."""

    physical_gpu_count: int = Field(default=0, description="Estimated physical GPU count (by unique PCI bus)")
    device_count: int = Field(default=0, description="Total device/VF slots (掛載點上限)")
    capacity_count: int = Field(default=0, description="實際可指派上限：SR-IOV vGPU 為 min(VF 數, framebuffer 可切數)，passthrough 同 device_count")
    used_count: int = 0
    available_count: int = 0
    is_sriov: bool = Field(default=False, description="True if SR-IOV detected (multiple devices on same PCI bus)")
    has_mdev: bool = Field(default=False, description="True if any device uses mediated devices")
    total_vram_mb: int = Field(default=0, description="Total usable VRAM in MB (vGPU: framebuffer × max-instances × 卡數)")
    used_vram_mb: int = Field(default=0, description="Allocated VRAM in MB (sum of assigned vGPU/passthrough)")
    used_vram_known: bool = Field(default=True, description="False 表示部分已掛載 profile 規格查不到，used_vram_mb 為低估")
    per_instance_vram_mb: int = Field(default=0, description="每個 vGPU instance 的 framebuffer (MB)，非 vGPU 為 0")
    mdev_profile: str = Field(default="", description="參考 vGPU profile 名稱，例如 'NVIDIA H200-4C'")
    profiles: list[GPUProfileOption] = Field(default_factory=list, description="可選的 vGPU profile 清單（依 VRAM 排序）")
    used_by: list["GPUUsageInfo"] = Field(default_factory=list)


class GPUUsageInfo(BaseModel):
    """Information about a VM using a GPU mapping."""

    vmid: int
    vm_name: str = ""
    node: str = ""
    status: str = ""
    mdev_type: str = ""
    allocated_vram_mb: int = 0


class GPUMappingsPublic(BaseModel):
    """List of GPU mappings."""

    data: list[GPUMappingDetail]
    count: int


class GPUMappingCreate(BaseModel):
    """Create a new PCI resource mapping."""

    id: str = Field(min_length=1, max_length=128, description="Mapping name")
    description: str = ""
    map: list[str] = Field(
        min_length=1,
        description="List of map entries, e.g. 'node=pve1,path=0000:01:00.0'",
    )


class GPUSummary(BaseModel):
    """A simplified GPU option for the application form selector."""

    mapping_id: str
    description: str = ""
    model: str = ""
    vram: str = ""
    node: str = ""
    physical_gpu_count: int = 1
    device_count: int = 1
    capacity_count: int = 1
    used_count: int = 0
    available_count: int = 1
    is_sriov: bool = False
    has_mdev: bool = False
    total_vram_mb: int = 0
    used_vram_mb: int = 0
    used_vram_known: bool = True
    per_instance_vram_mb: int = 0
    mdev_profile: str = ""
    profiles: list[GPUProfileOption] = Field(default_factory=list)
