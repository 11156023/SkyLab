"""PVE API 資料模型

涵蓋從 Proxmox VE REST API 可取得的所有主要資料結構。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 節點層級
# ---------------------------------------------------------------------------


class NodeInfo(BaseModel):
    """單一 PVE 節點摘要（來自 GET /nodes）"""

    node: str = Field(description="節點名稱")
    status: str = Field(description="狀態：online / offline / unknown")
    cpu_usage: float = Field(description="CPU 使用率 (0.0 ~ 1.0)")
    cpu_cores: int = Field(description="總 CPU 核心數")
    mem_used_bytes: int = Field(description="已用記憶體（bytes）")
    mem_total_bytes: int = Field(description="總記憶體（bytes）")
    mem_used_pct: float = Field(description="記憶體使用率 (0.0 ~ 1.0)")
    disk_used_bytes: int = Field(description="根磁碟已用（bytes）")
    disk_total_bytes: int = Field(description="根磁碟總量（bytes）")
    disk_used_pct: float = Field(description="根磁碟使用率 (0.0 ~ 1.0)")
    uptime_seconds: int | None = Field(default=None, description="節點已運行秒數")


# ---------------------------------------------------------------------------
# 儲存空間層級
# ---------------------------------------------------------------------------


class StorageInfo(BaseModel):
    """節點上的一個儲存空間資訊（來自 GET /nodes/{node}/storage）"""

    node: str = Field(description="所屬節點名稱")
    storage: str = Field(description="儲存空間 ID（例如 local、local-lvm）")
    storage_type: str = Field(description="儲存類型：dir / lvmthin / nfs / zfs 等")
    content: str = Field(description="支援的內容類型：images,rootdir,iso,backup 等")
    avail_bytes: int = Field(description="可用空間（bytes）")
    used_bytes: int = Field(description="已用空間（bytes）")
    total_bytes: int = Field(description="總空間（bytes）")
    used_pct: float = Field(description="使用率 (0.0 ~ 1.0)")
    active: bool = Field(description="是否啟用中")
    enabled: bool = Field(description="是否已設定啟用")
    shared: bool = Field(description="是否為共享儲存（NFS/Ceph 等）")


# ---------------------------------------------------------------------------
# VM / LXC 層級
# ---------------------------------------------------------------------------


class ResourceSummary(BaseModel):
    """VM 或 LXC 的摘要資料（來自 GET /cluster/resources?type=vm）"""

    vmid: int = Field(description="VM/容器 ID")
    name: str = Field(description="名稱（hostname）")
    resource_type: str = Field(description="類型：qemu（VM）或 lxc（容器）")
    node: str = Field(description="所在節點名稱")
    status: str = Field(description="狀態：running / stopped / paused")
    pool: str | None = Field(default=None, description="資源池名稱")
    cpu_usage: float = Field(description="CPU 使用率 (0.0 ~ 1.0)")
    cpu_cores: int = Field(description="分配的 CPU 核心數")
    mem_used_bytes: int = Field(description="已用記憶體（bytes）")
    mem_total_bytes: int = Field(description="分配的記憶體（bytes）")
    mem_used_pct: float = Field(description="記憶體使用率 (0.0 ~ 1.0)")
    disk_used_bytes: int = Field(description="磁碟已用（bytes）")
    disk_total_bytes: int = Field(description="磁碟總量（bytes）")
    disk_used_pct: float = Field(description="磁碟使用率 (0.0 ~ 1.0)")
    net_in_bytes: int = Field(description="累計網路流入（bytes）")
    net_out_bytes: int = Field(description="累計網路流出（bytes）")
    uptime_seconds: int | None = Field(
        default=None, description="已運行秒數（停機時為 None）"
    )
    is_template: bool = Field(description="是否為模板")


class ResourceStatus(BaseModel):
    """VM 或 LXC 的即時詳細狀態（來自 GET /nodes/{node}/{type}/{vmid}/status/current）"""

    vmid: int = Field(description="VM/容器 ID")
    node: str = Field(description="所在節點名稱")
    resource_type: str = Field(description="qemu 或 lxc")
    status: str = Field(description="狀態：running / stopped / paused")
    cpu_usage: float = Field(
        description="當前 CPU 使用率 (0.0 ~ N.0，超過 1.0 代表多核使用)"
    )
    cpu_cores: int = Field(description="分配的 CPU 核心數")
    mem_used_bytes: int = Field(description="當前已用記憶體（bytes）")
    mem_total_bytes: int = Field(description="分配的記憶體（bytes）")
    mem_used_pct: float = Field(description="記憶體使用率 (0.0 ~ 1.0)")
    disk_read_bytes: int = Field(description="磁碟累計讀取（bytes）")
    disk_write_bytes: int = Field(description="磁碟累計寫入（bytes）")
    disk_total_bytes: int = Field(description="磁碟總量（bytes）")
    net_in_bytes: int = Field(description="累計網路流入（bytes）")
    net_out_bytes: int = Field(description="累計網路流出（bytes）")
    uptime_seconds: int | None = Field(default=None, description="已運行秒數")
    pid: int | None = Field(
        default=None, description="QEMU/LXC 在宿主機的 PID（僅 running 時有值）"
    )


class ResourceConfig(BaseModel):
    """VM 或 LXC 的設定（來自 GET /nodes/{node}/{type}/{vmid}/config）"""

    vmid: int = Field(description="VM/容器 ID")
    node: str = Field(description="所在節點名稱")
    resource_type: str = Field(description="qemu 或 lxc")
    name: str | None = Field(default=None, description="名稱（hostname）")
    cpu_cores: int | None = Field(default=None, description="CPU 核心數設定值")
    cpu_type: str | None = Field(
        default=None, description="QEMU CPU 類型（host/kvm64 等）"
    )
    memory_mb: int | None = Field(default=None, description="記憶體大小（MB）")
    disk_info: str | None = Field(
        default=None, description="主磁碟設定字串（scsi0 / rootfs）"
    )
    disk_size_gb: int | None = Field(default=None, description="主磁碟大小（GB）")
    os_type: str | None = Field(
        default=None, description="作業系統類型（l26/win10/ubuntu 等）"
    )
    net0: str | None = Field(default=None, description="第一張網卡設定字串")
    description: str | None = Field(default=None, description="備註說明")
    tags: str | None = Field(default=None, description="標籤（逗號分隔）")
    onboot: bool = Field(default=False, description="是否隨節點開機自動啟動")
    protection: bool = Field(
        default=False, description="是否開啟保護模式（防止意外刪除）"
    )
    raw: dict[str, Any] = Field(
        default_factory=dict, description="完整原始設定（所有欄位）"
    )


class NetworkInterface(BaseModel):
    """LXC 容器的網路介面（來自 GET /nodes/{node}/lxc/{vmid}/interfaces）"""

    vmid: int = Field(description="容器 ID")
    name: str = Field(description="網卡名稱（eth0 / lo 等）")
    inet: str | None = Field(
        default=None, description="IPv4 位址（含遮罩，例如 192.168.1.10/24）"
    )
    inet6: str | None = Field(default=None, description="IPv6 位址（含遮罩）")
    hwaddr: str | None = Field(default=None, description="MAC 位址")


# ---------------------------------------------------------------------------
# Cluster 層級
# ---------------------------------------------------------------------------


class ClusterInfo(BaseModel):
    """PVE 叢集整體資訊（來自 GET /cluster/status）"""

    cluster_name: str | None = Field(
        default=None, description="叢集名稱（單機模式為 None）"
    )
    is_cluster: bool = Field(description="是否為多節點叢集")
    node_count: int = Field(description="節點總數")
    quorate: bool = Field(description="叢集是否達到 quorum（多數決有效）")
    cluster_version: int | None = Field(default=None, description="叢集設定版本號")


# ---------------------------------------------------------------------------
# 完整快照（批量分析主結構）
# ---------------------------------------------------------------------------


class SystemSnapshot(BaseModel):
    """一次完整的系統資料快照，包含所有節點、VM/LXC 的最新資料"""

    collected_at: datetime = Field(description="資料收集時間（UTC）")
    collection_duration_seconds: float = Field(description="收集耗時（秒）")
    cluster: ClusterInfo = Field(description="叢集概覽")
    nodes: list[NodeInfo] = Field(description="所有節點清單")
    storages: list[StorageInfo] = Field(description="所有節點的儲存空間清單")
    resources: list[ResourceSummary] = Field(description="所有 VM/LXC 摘要清單")
    resource_statuses: list[ResourceStatus] = Field(
        description="所有 running 狀態資源的即時詳細數值"
    )
    resource_configs: list[ResourceConfig] = Field(
        description="所有資源的設定檔（如啟用）"
    )
    network_interfaces: list[NetworkInterface] = Field(
        description="LXC 容器的網路介面資料"
    )
    errors: list[str] = Field(
        default_factory=list, description="收集過程中的非致命錯誤訊息"
    )

    # 統計摘要（方便直接看）
    total_nodes: int = Field(description="節點總數")
    online_nodes: int = Field(description="上線節點數")
    total_vms: int = Field(description="QEMU VM 總數（不含模板）")
    total_lxc: int = Field(description="LXC 容器總數（不含模板）")
    running_vms: int = Field(description="運行中 VM 數")
    running_lxc: int = Field(description="運行中 LXC 數")


# ---------------------------------------------------------------------------
# PVE API 資料欄位參考表
# ---------------------------------------------------------------------------


class FieldReference(BaseModel):
    field: str = Field(description="欄位名稱")
    type: str = Field(description="資料型態")
    description: str = Field(description="說明")
    example: str | None = Field(default=None, description="範例值")


class ApiEndpointReference(BaseModel):
    endpoint: str = Field(description="PVE API 路徑")
    method: str = Field(description="HTTP 方法")
    category: str = Field(description="分類")
    description: str = Field(description="說明")
    fields: list[FieldReference] = Field(description="可取得的欄位")


PVE_API_REFERENCE: list[ApiEndpointReference] = [
    ApiEndpointReference(
        endpoint="/nodes",
        method="GET",
        category="節點",
        description="取得所有 PVE 節點的摘要清單",
        fields=[
            FieldReference(
                field="node", type="string", description="節點名稱", example="pve"
            ),
            FieldReference(
                field="status", type="string", description="節點狀態", example="online"
            ),
            FieldReference(
                field="cpu",
                type="float",
                description="CPU 使用率 (0.0~1.0)",
                example="0.15",
            ),
            FieldReference(
                field="maxcpu", type="int", description="總 CPU 核心數", example="8"
            ),
            FieldReference(
                field="mem",
                type="int",
                description="已用記憶體 bytes",
                example="8589934592",
            ),
            FieldReference(
                field="maxmem",
                type="int",
                description="總記憶體 bytes",
                example="17179869184",
            ),
            FieldReference(
                field="disk",
                type="int",
                description="根磁碟已用 bytes",
                example="10737418240",
            ),
            FieldReference(
                field="maxdisk",
                type="int",
                description="根磁碟總量 bytes",
                example="107374182400",
            ),
            FieldReference(
                field="uptime",
                type="int",
                description="節點已運行秒數",
                example="86400",
            ),
            FieldReference(
                field="level", type="string", description="Proxmox 支援等級", example=""
            ),
            FieldReference(
                field="id", type="string", description="資源唯一 ID", example="node/pve"
            ),
            FieldReference(
                field="type",
                type="string",
                description="資源類型（固定為 node）",
                example="node",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/storage",
        method="GET",
        category="儲存空間",
        description="取得指定節點上所有儲存空間清單",
        fields=[
            FieldReference(
                field="storage",
                type="string",
                description="儲存空間 ID",
                example="local-lvm",
            ),
            FieldReference(
                field="type", type="string", description="儲存類型", example="lvmthin"
            ),
            FieldReference(
                field="content",
                type="string",
                description="支援的內容類型（逗號分隔）",
                example="images,rootdir",
            ),
            FieldReference(
                field="avail",
                type="int",
                description="可用空間 bytes",
                example="50000000000",
            ),
            FieldReference(
                field="used",
                type="int",
                description="已用空間 bytes",
                example="10000000000",
            ),
            FieldReference(
                field="total",
                type="int",
                description="總空間 bytes",
                example="60000000000",
            ),
            FieldReference(
                field="used_fraction",
                type="float",
                description="使用率 (0.0~1.0)",
                example="0.166",
            ),
            FieldReference(
                field="active", type="int", description="是否啟用 (1/0)", example="1"
            ),
            FieldReference(
                field="enabled",
                type="int",
                description="是否設定啟用 (1/0)",
                example="1",
            ),
            FieldReference(
                field="shared",
                type="int",
                description="是否為共享儲存 (1/0)",
                example="0",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/cluster/resources?type=vm",
        method="GET",
        category="VM/LXC 摘要",
        description="批量取得叢集中所有 VM 與 LXC 的摘要資料（最常用）",
        fields=[
            FieldReference(
                field="vmid", type="int", description="VM/容器 ID", example="100"
            ),
            FieldReference(
                field="name", type="string", description="名稱", example="ubuntu-01"
            ),
            FieldReference(
                field="type",
                type="string",
                description="qemu（VM）或 lxc（容器）",
                example="qemu",
            ),
            FieldReference(
                field="node", type="string", description="所在節點", example="pve"
            ),
            FieldReference(
                field="status",
                type="string",
                description="running / stopped / paused",
                example="running",
            ),
            FieldReference(
                field="pool",
                type="string",
                description="資源池名稱",
                example="SkyLab",
            ),
            FieldReference(
                field="cpu",
                type="float",
                description="CPU 使用率 (0.0~1.0)",
                example="0.05",
            ),
            FieldReference(
                field="maxcpu", type="int", description="分配 CPU 核心數", example="2"
            ),
            FieldReference(
                field="mem",
                type="int",
                description="已用記憶體 bytes",
                example="2147483648",
            ),
            FieldReference(
                field="maxmem",
                type="int",
                description="分配記憶體 bytes",
                example="4294967296",
            ),
            FieldReference(
                field="disk",
                type="int",
                description="磁碟已用 bytes",
                example="10737418240",
            ),
            FieldReference(
                field="maxdisk",
                type="int",
                description="磁碟總量 bytes",
                example="32212254720",
            ),
            FieldReference(
                field="netin",
                type="int",
                description="累計網路流入 bytes",
                example="1234567",
            ),
            FieldReference(
                field="netout",
                type="int",
                description="累計網路流出 bytes",
                example="987654",
            ),
            FieldReference(
                field="uptime", type="int", description="已運行秒數", example="3600"
            ),
            FieldReference(
                field="template",
                type="int",
                description="是否為模板 (1/0)",
                example="0",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/qemu/{vmid}/status/current",
        method="GET",
        category="VM 即時狀態",
        description="取得單一 QEMU VM 的即時詳細狀態",
        fields=[
            FieldReference(
                field="status",
                type="string",
                description="running / stopped",
                example="running",
            ),
            FieldReference(
                field="cpu", type="float", description="CPU 使用率", example="0.05"
            ),
            FieldReference(
                field="cpus", type="int", description="分配核心數", example="2"
            ),
            FieldReference(
                field="mem",
                type="int",
                description="已用記憶體 bytes",
                example="2147483648",
            ),
            FieldReference(
                field="maxmem",
                type="int",
                description="分配記憶體 bytes",
                example="4294967296",
            ),
            FieldReference(
                field="diskread",
                type="int",
                description="磁碟累計讀取 bytes",
                example="123456789",
            ),
            FieldReference(
                field="diskwrite",
                type="int",
                description="磁碟累計寫入 bytes",
                example="987654321",
            ),
            FieldReference(
                field="maxdisk",
                type="int",
                description="磁碟總量 bytes",
                example="32212254720",
            ),
            FieldReference(
                field="netin",
                type="int",
                description="累計流入 bytes",
                example="1234567",
            ),
            FieldReference(
                field="netout",
                type="int",
                description="累計流出 bytes",
                example="987654",
            ),
            FieldReference(
                field="uptime", type="int", description="已運行秒數", example="3600"
            ),
            FieldReference(
                field="pid",
                type="int",
                description="宿主機上的 QEMU PID",
                example="12345",
            ),
            FieldReference(
                field="qmpstatus",
                type="string",
                description="QEMU 詳細狀態（running/paused）",
                example="running",
            ),
            FieldReference(
                field="ha",
                type="object",
                description="HA 狀態資訊",
                example="{managed:0}",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/lxc/{vmid}/status/current",
        method="GET",
        category="LXC 即時狀態",
        description="取得單一 LXC 容器的即時詳細狀態",
        fields=[
            FieldReference(
                field="status",
                type="string",
                description="running / stopped",
                example="running",
            ),
            FieldReference(
                field="cpu", type="float", description="CPU 使用率", example="0.02"
            ),
            FieldReference(
                field="cpus", type="int", description="分配核心數", example="1"
            ),
            FieldReference(
                field="mem",
                type="int",
                description="已用記憶體 bytes",
                example="536870912",
            ),
            FieldReference(
                field="maxmem",
                type="int",
                description="分配記憶體 bytes",
                example="1073741824",
            ),
            FieldReference(
                field="diskread",
                type="int",
                description="磁碟累計讀取 bytes",
                example="12345678",
            ),
            FieldReference(
                field="diskwrite",
                type="int",
                description="磁碟累計寫入 bytes",
                example="98765432",
            ),
            FieldReference(
                field="maxdisk",
                type="int",
                description="磁碟總量 bytes",
                example="10737418240",
            ),
            FieldReference(
                field="netin",
                type="int",
                description="累計流入 bytes",
                example="123456",
            ),
            FieldReference(
                field="netout",
                type="int",
                description="累計流出 bytes",
                example="98765",
            ),
            FieldReference(
                field="uptime", type="int", description="已運行秒數", example="7200"
            ),
            FieldReference(
                field="pid",
                type="int",
                description="LXC 在宿主機的 PID",
                example="23456",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/qemu/{vmid}/config",
        method="GET",
        category="VM 設定",
        description="取得 QEMU VM 的完整設定檔",
        fields=[
            FieldReference(
                field="name", type="string", description="VM 名稱", example="ubuntu-01"
            ),
            FieldReference(
                field="cores", type="int", description="CPU 核心數", example="2"
            ),
            FieldReference(
                field="sockets", type="int", description="CPU Socket 數", example="1"
            ),
            FieldReference(
                field="cpu", type="string", description="CPU 類型", example="host"
            ),
            FieldReference(
                field="memory", type="int", description="記憶體 MB", example="4096"
            ),
            FieldReference(
                field="scsi0",
                type="string",
                description="主磁碟設定",
                example="local-lvm:vm-100-disk-0,size=32G",
            ),
            FieldReference(
                field="net0",
                type="string",
                description="第一張網卡設定",
                example="virtio=AA:BB:CC:DD:EE:FF,bridge=vmbr0",
            ),
            FieldReference(
                field="ostype", type="string", description="作業系統類型", example="l26"
            ),
            FieldReference(
                field="boot",
                type="string",
                description="開機設定",
                example="order=scsi0",
            ),
            FieldReference(
                field="onboot",
                type="int",
                description="是否開機自動啟動 (1/0)",
                example="1",
            ),
            FieldReference(
                field="protection",
                type="int",
                description="保護模式 (1/0)",
                example="0",
            ),
            FieldReference(
                field="description",
                type="string",
                description="備註說明",
                example="Web 伺服器",
            ),
            FieldReference(
                field="tags",
                type="string",
                description="標籤（分號分隔）",
                example="web;production",
            ),
            FieldReference(
                field="agent",
                type="string",
                description="QEMU Guest Agent 設定",
                example="enabled=1",
            ),
            FieldReference(
                field="balloon",
                type="int",
                description="記憶體氣球設備大小 MB",
                example="0",
            ),
            FieldReference(
                field="numa", type="int", description="NUMA 設定 (1/0)", example="0"
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/lxc/{vmid}/config",
        method="GET",
        category="LXC 設定",
        description="取得 LXC 容器的完整設定檔",
        fields=[
            FieldReference(
                field="hostname",
                type="string",
                description="容器 hostname",
                example="my-container",
            ),
            FieldReference(
                field="cores", type="int", description="CPU 核心數", example="1"
            ),
            FieldReference(
                field="cpulimit",
                type="float",
                description="CPU 限制值（0=不限制）",
                example="0",
            ),
            FieldReference(
                field="memory", type="int", description="記憶體 MB", example="1024"
            ),
            FieldReference(
                field="swap", type="int", description="Swap MB", example="512"
            ),
            FieldReference(
                field="rootfs",
                type="string",
                description="根目錄磁碟設定",
                example="local-lvm:vm-101-disk-0,size=10G",
            ),
            FieldReference(
                field="net0",
                type="string",
                description="第一張網卡設定（含 IP）",
                example="name=eth0,bridge=vmbr0,ip=dhcp",
            ),
            FieldReference(
                field="ostype",
                type="string",
                description="作業系統類型",
                example="ubuntu",
            ),
            FieldReference(
                field="onboot",
                type="int",
                description="開機自動啟動 (1/0)",
                example="0",
            ),
            FieldReference(
                field="protection",
                type="int",
                description="保護模式 (1/0)",
                example="0",
            ),
            FieldReference(
                field="unprivileged",
                type="int",
                description="是否為非特權容器 (1/0)",
                example="1",
            ),
            FieldReference(
                field="description",
                type="string",
                description="備註說明",
                example="開發環境",
            ),
            FieldReference(
                field="tags",
                type="string",
                description="標籤（分號分隔）",
                example="dev;lxc",
            ),
            FieldReference(
                field="features",
                type="string",
                description="特殊功能（nesting/keyctl 等）",
                example="nesting=1",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/lxc/{vmid}/interfaces",
        method="GET",
        category="LXC 網路",
        description="取得 LXC 容器的網路介面清單（含 IP，無需 guest agent）",
        fields=[
            FieldReference(
                field="name", type="string", description="網卡名稱", example="eth0"
            ),
            FieldReference(
                field="inet",
                type="string",
                description="IPv4 位址（含 CIDR）",
                example="192.168.1.10/24",
            ),
            FieldReference(
                field="inet6",
                type="string",
                description="IPv6 位址（含 CIDR）",
                example="fe80::1/64",
            ),
            FieldReference(
                field="hwaddr",
                type="string",
                description="MAC 位址",
                example="AA:BB:CC:DD:EE:FF",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
        method="GET",
        category="QEMU Guest Agent（需安裝）",
        description="透過 QEMU Guest Agent 取得 VM 內部網路介面（需 VM 安裝 qemu-guest-agent）",
        fields=[
            FieldReference(
                field="result[].name",
                type="string",
                description="網卡名稱",
                example="ens3",
            ),
            FieldReference(
                field="result[].hardware-address",
                type="string",
                description="MAC 位址",
                example="AA:BB:CC:DD:EE:FF",
            ),
            FieldReference(
                field="result[].ip-addresses[].ip-address",
                type="string",
                description="IP 位址",
                example="192.168.1.10",
            ),
            FieldReference(
                field="result[].ip-addresses[].ip-address-type",
                type="string",
                description="ipv4 或 ipv6",
                example="ipv4",
            ),
            FieldReference(
                field="result[].ip-addresses[].prefix",
                type="int",
                description="CIDR 遮罩長度",
                example="24",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/nodes/{node}/qemu/{vmid}/rrddata",
        method="GET",
        category="歷史效能資料",
        description="取得 VM/LXC 的 RRD 歷史效能時序資料",
        fields=[
            FieldReference(
                field="time",
                type="int",
                description="Unix 時間戳",
                example="1700000000",
            ),
            FieldReference(
                field="cpu", type="float", description="CPU 使用率", example="0.12"
            ),
            FieldReference(
                field="maxcpu", type="int", description="最大 CPU 核心數", example="2"
            ),
            FieldReference(
                field="mem",
                type="int",
                description="記憶體用量 bytes",
                example="2147483648",
            ),
            FieldReference(
                field="maxmem",
                type="int",
                description="記憶體上限 bytes",
                example="4294967296",
            ),
            FieldReference(
                field="disk",
                type="int",
                description="磁碟用量 bytes",
                example="10737418240",
            ),
            FieldReference(
                field="maxdisk",
                type="int",
                description="磁碟上限 bytes",
                example="32212254720",
            ),
            FieldReference(
                field="netin",
                type="float",
                description="網路流入速率 bytes/s",
                example="12345.6",
            ),
            FieldReference(
                field="netout",
                type="float",
                description="網路流出速率 bytes/s",
                example="9876.5",
            ),
        ],
    ),
    ApiEndpointReference(
        endpoint="/cluster/status",
        method="GET",
        category="叢集",
        description="取得叢集整體狀態與各節點的 quorum 資訊",
        fields=[
            FieldReference(
                field="type",
                type="string",
                description="資料類型：cluster 或 node",
                example="cluster",
            ),
            FieldReference(
                field="name",
                type="string",
                description="叢集或節點名稱",
                example="campus-cluster",
            ),
            FieldReference(
                field="nodes",
                type="int",
                description="節點總數（type=cluster 時）",
                example="2",
            ),
            FieldReference(
                field="quorate",
                type="int",
                description="是否達到 quorum (1/0)",
                example="1",
            ),
            FieldReference(
                field="version", type="int", description="叢集設定版本號", example="5"
            ),
            FieldReference(
                field="online",
                type="int",
                description="節點是否上線 (1/0)（type=node 時）",
                example="1",
            ),
            FieldReference(
                field="local",
                type="int",
                description="是否為本機節點 (1/0)（type=node 時）",
                example="1",
            ),
            FieldReference(
                field="ip",
                type="string",
                description="節點 IP（type=node 時）",
                example="192.168.1.1",
            ),
            FieldReference(
                field="nodeid",
                type="int",
                description="節點 ID（type=node 時）",
                example="1",
            ),
        ],
    ),
]
