from __future__ import annotations

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


class NodeCapacity(BaseModel):
    node: str
    status: str
    gpu_count: int = Field(default=0, ge=0)
    running_resources: int = Field(default=0, ge=0)
    guest_soft_limit: int = Field(default=0, ge=0)
    guest_pressure_ratio: float = Field(default=0.0, ge=0.0)
    guest_overloaded: bool = False
    candidate: bool = True
    cpu_ratio: float = Field(default=0.0, ge=0.0)
    memory_ratio: float = Field(default=0.0, ge=0.0)
    disk_ratio: float = Field(default=0.0, ge=0.0)
    total_cpu_cores: float = Field(default=0.0, ge=0.0)
    allocatable_cpu_cores: float = Field(default=0.0, ge=0.0)
    total_memory_bytes: int = Field(default=0, ge=0)
    allocatable_memory_bytes: int = Field(default=0, ge=0)
    total_disk_bytes: int = Field(default=0, ge=0)
    allocatable_disk_bytes: int = Field(default=0, ge=0)
    current_loadavg_1: float | None = Field(default=None, ge=0.0)
    average_loadavg_1: float | None = Field(default=None, ge=0.0)


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
