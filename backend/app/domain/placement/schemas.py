from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

ResourceType = Literal["lxc", "vm"]


class PlacementRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resource_type: ResourceType = Field(
        default="vm",
        validation_alias=AliasChoices("resource_type", "container_type"),
    )
    cpu_cores: int = Field(
        default=2,
        ge=1,
        le=256,
        validation_alias=AliasChoices("cpu_cores", "cores", "cpu"),
    )
    memory_mb: int = Field(
        default=2048,
        ge=128,
        le=1048576,
        validation_alias=AliasChoices("memory_mb", "ram_mb"),
    )
    disk_gb: int = Field(
        default=20,
        ge=1,
        le=65536,
        validation_alias=AliasChoices("disk_gb", "disk"),
    )
    instance_count: int = Field(
        default=1,
        ge=1,
        le=100,
        validation_alias=AliasChoices("instance_count", "count", "machines"),
    )
    gpu_required: int = Field(default=0, ge=0, le=16)
    gpu_mapping_id: str | None = Field(default=None, max_length=128)
    # 模板節點約束：LXC 帶 vztmpl volid、VM 克隆帶範本 VMID。
    # placement 據此把候選節點限制在「拿得到模板」的節點上。
    ostemplate: str | None = Field(default=None, max_length=512)
    template_vmid: int | None = Field(default=None, ge=1)
    # 群組約束：同一組機器（快速練習 Session／班級／課程部署）共用此鍵。
    # placement 據此把整組釘在同一節點（同時也就同一叢集）。
    placement_group_id: uuid.UUID | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def normalize_compat_fields(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw

        data = dict(raw)
        memory_gb = data.get("memory_gb", data.get("ram_gb"))
        if memory_gb is not None and "memory_mb" not in data and "ram_mb" not in data:
            try:
                data["memory_mb"] = int(float(memory_gb) * 1024)
            except (TypeError, ValueError):
                # 無法換算時保留原始輸入，交由後續欄位驗證處理
                pass
        return data


class NodeSnapshot(BaseModel):
    node: str
    status: str = Field(default="unknown")
    cpu_ratio: float = Field(default=0.0, ge=0.0)
    maxcpu: int = Field(default=0, ge=0)
    mem_bytes: int = Field(default=0, ge=0)
    maxmem_bytes: int = Field(default=0, ge=0)
    disk_bytes: int = Field(default=0, ge=0)
    maxdisk_bytes: int = Field(default=0, ge=0)
    uptime: int | None = Field(default=None, ge=0)
    gpu_count: int = Field(default=0, ge=0)
    current_loadavg_1: float | None = Field(default=None, ge=0.0)
    average_loadavg_1: float | None = Field(default=None, ge=0.0)


class ResourceSnapshot(BaseModel):
    vmid: int
    name: str = Field(default="")
    resource_type: str = Field(default="unknown")
    node: str = Field(default="unknown")
    status: str = Field(default="unknown")
    # 分配給這台機器的規格（關機也算）；整班容量預檢以它計算已承諾的用量
    maxcpu: int = Field(default=0, ge=0)
    maxmem_bytes: int = Field(default=0, ge=0)


class NodeCapacity(BaseModel):
    node: str
    status: str
    gpu_count: int = Field(default=0, ge=0)
    # 扣掉同時段已預約的 GPU 之後，這個節點還剩幾個可指派的槽。
    # gpu_count 是節點上所有 mapping 的總槽數，因此這個計數也是總量：
    # 同節點不同卡之間會互相排擠，屬保守誤差（寧可拒絕也不超賣）。
    allocatable_gpu_slots: int = Field(default=0, ge=0)
    running_resources: int = Field(default=0, ge=0)
    guest_soft_limit: int = Field(default=0, ge=0)
    guest_pressure_ratio: float = Field(default=0.0, ge=0.0)
    guest_overloaded: bool = False
    candidate: bool = True
    cpu_ratio: float = Field(default=0.0, ge=0.0)
    memory_ratio: float = Field(default=0.0, ge=0.0)
    disk_ratio: float = Field(default=0.0, ge=0.0)
    # total_cpu_cores 已乘上 CPU 超配倍率；loadavg 換算每核負載要用節點實際的
    # CPU 數（PVE maxcpu）。0 表示呼叫端沒提供，scorer 退回 total_cpu_cores
    physical_cpu_cores: int = Field(default=0, ge=0)
    total_cpu_cores: float = Field(default=0.0, ge=0.0)
    allocatable_cpu_cores: float = Field(default=0.0, ge=0.0)
    total_memory_bytes: int = Field(default=0, ge=0)
    allocatable_memory_bytes: int = Field(default=0, ge=0)
    total_disk_bytes: int = Field(default=0, ge=0)
    allocatable_disk_bytes: int = Field(default=0, ge=0)
    current_loadavg_1: float | None = Field(default=None, ge=0.0)
    average_loadavg_1: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def default_gpu_slots_to_capacity(self) -> NodeCapacity:
        """未明確指定可用槽數時，視為「整個節點的 GPU 都還沒被預約」。

        避免呼叫端只設 gpu_count 就意外得到 0 個可用槽而被擋下。
        """
        if "allocatable_gpu_slots" not in self.model_fields_set:
            self.allocatable_gpu_slots = self.gpu_count
        return self


class PlacementDecision(BaseModel):
    node: str
    instance_count: int = Field(default=0, ge=0)
    cpu_cores_reserved: float = Field(default=0.0, ge=0.0)
    memory_bytes_reserved: int = Field(default=0, ge=0)
    disk_bytes_reserved: int = Field(default=0, ge=0)
    remaining_cpu_cores: float = Field(default=0.0, ge=0.0)
    remaining_memory_bytes: int = Field(default=0, ge=0)
    remaining_disk_bytes: int = Field(default=0, ge=0)


class PlacementPlan(BaseModel):
    feasible: bool = False
    requested_resource_type: ResourceType
    effective_resource_type: ResourceType
    resource_type_reason: str
    assigned_instances: int = Field(default=0, ge=0)
    unassigned_instances: int = Field(default=0, ge=0)
    recommended_node: str | None = None
    summary: str
    rationale: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    placements: list[PlacementDecision] = Field(default_factory=list)
    candidate_nodes: list[NodeCapacity] = Field(default_factory=list)
