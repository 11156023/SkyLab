from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str | None = Field(
        default=None, description="使用者輸入的自然語言問題", max_length=2000
    )
    group_id: uuid.UUID = Field(description="目前所在群組 ID")
    messages: list[dict] | None = Field(
        default=None, description="完整的對話歷史（用於中斷與接續對話）"
    )


class ToolCallRecord(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = Field(default=None)


class ChatResponse(BaseModel):
    reply: str
    tools_called: list[ToolCallRecord] = Field(default_factory=list)
    needs_confirmation: bool = Field(default=False)
    messages: list[dict] = Field(default_factory=list)
    error: str | None = None


class NodeInfo(BaseModel):
    node: str
    status: str
    cpu_usage: float
    cpu_cores: int
    mem_used_bytes: int
    mem_total_bytes: int
    mem_used_pct: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_used_pct: float
    uptime_seconds: int | None = None


class StorageInfo(BaseModel):
    node: str
    storage: str
    storage_type: str
    content: str
    avail_bytes: int
    used_bytes: int
    total_bytes: int
    used_pct: float
    active: bool
    enabled: bool
    shared: bool


class ResourceSummary(BaseModel):
    vmid: int
    name: str
    resource_type: str
    node: str
    status: str
    pool: str | None = None
    cpu_usage: float
    cpu_cores: int
    mem_used_bytes: int
    mem_total_bytes: int
    mem_used_pct: float
    disk_used_bytes: int
    disk_total_bytes: int
    disk_used_pct: float
    net_in_bytes: int
    net_out_bytes: int
    uptime_seconds: int | None = None
    is_template: bool


class ResourceStatus(BaseModel):
    vmid: int
    node: str
    resource_type: str
    status: str
    cpu_usage: float
    cpu_cores: int
    mem_used_bytes: int
    mem_total_bytes: int
    mem_used_pct: float
    disk_read_bytes: int
    disk_write_bytes: int
    disk_total_bytes: int
    net_in_bytes: int
    net_out_bytes: int
    uptime_seconds: int | None = None
    pid: int | None = None


class ResourceConfig(BaseModel):
    vmid: int
    node: str
    resource_type: str
    name: str | None = None
    cpu_cores: int | None = None
    cpu_type: str | None = None
    memory_mb: int | None = None
    disk_info: str | None = None
    disk_size_gb: int | None = None
    os_type: str | None = None
    net0: str | None = None
    description: str | None = None
    tags: str | None = None
    onboot: bool = False
    protection: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class NetworkInterface(BaseModel):
    vmid: int
    name: str
    inet: str | None = None
    inet6: str | None = None
    hwaddr: str | None = None


class ClusterInfo(BaseModel):
    cluster_name: str | None = None
    is_cluster: bool
    node_count: int
    quorate: bool
    cluster_version: int | None = None


class SystemSnapshot(BaseModel):
    collected_at: datetime
    collection_duration_seconds: float
    cluster: ClusterInfo
    nodes: list[NodeInfo]
    storages: list[StorageInfo]
    resources: list[ResourceSummary]
    resource_statuses: list[ResourceStatus]
    resource_configs: list[ResourceConfig]
    network_interfaces: list[NetworkInterface]
    errors: list[str] = Field(default_factory=list)
    total_nodes: int
    online_nodes: int
    total_vms: int
    total_lxc: int
    running_vms: int
    running_lxc: int


class SSHExecRequest(BaseModel):
    vmid: int = Field(description="目標 VM 或 LXC 的 ID")
    command: str = Field(description="要執行的指令內容", min_length=1)
    ssh_user: str = Field(default="root", description="SSH 登入帳號（預設 root）")
    ssh_port: int = Field(default=22, ge=1, le=65535, description="SSH 埠號（預設 22）")
    require_confirm: bool = Field(
        default=False, description="是否需要等待使用者確認（回傳 pending 狀態）"
    )


class SSHExecResult(BaseModel):
    vmid: int = Field(default=0)
    host: str = Field(default="")
    ssh_user: str = Field(default="")
    command: str = Field(default="")
    stdout: str = Field(default="")
    stderr: str = Field(default="")
    exit_code: int = Field(default=0)
    error: str | None = Field(default=None)
    blocked: bool = Field(
        default=False, description="是否因安全黑名單被攔截"
    )
    block_reason: str | None = Field(default=None)
    pending: bool = Field(
        default=False, description="是否在等待使用者確認"
    )
    confirm_token: str | None = Field(
        default=None, description="等待確認時的 Token（TTL 5 分鐘）"
    )


class SSHConfirmRequest(BaseModel):
    token: str = Field(description="執行時取得的 confirm_token")
    confirm_token: str | None = Field(
        default=None, description="相容欄位：可改以 confirm_token 傳入"
    )
    approved: bool = Field(description="是否允許執行")
    command: str | None = Field(
        default=None, description="可選：允許前覆寫要執行的指令內容"
    )
