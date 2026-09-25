"""首次安裝初始化精靈的 API schemas。

精靈端點在 `completed` 之前全部免登入，所以回應只放布林進度，
不帶任何既有帳號或連線的細節。
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.proxmox_config import ProxmoxConnectionCreate, ProxmoxNodePublic


class SetupStepsPublic(BaseModel):
    """各步驟是否已完成（供精靈略過已設定的步驟）"""

    admin: bool
    proxmox: bool
    subnet: bool


class SetupStatusPublic(BaseModel):
    """`GET /setup/status`：公開端點，前端據此決定要不要導去精靈"""

    completed: bool
    completed_at: datetime | None = None
    steps: SetupStepsPublic


class SetupAdminCreate(BaseModel):
    """步驟一：建立（或接管）系統管理員"""

    email: EmailStr = Field(max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    # .env 的 FIRST_SUPERUSER 是安裝時就存在的預設帳號，密碼通常人人知道；
    # 精靈建立的管理員不是同一個信箱時，預設把它停用。
    disable_default_admin: bool = True


class SetupAdminResult(BaseModel):
    email: EmailStr
    full_name: str | None
    created: bool  # False 表示接管了既有的超級使用者（例如 .env 預設帳號）
    default_admin_disabled: bool


class SetupProxmoxTestRequest(BaseModel):
    """步驟二的「測試連線」：不落 DB，只回節點與 storage 供表單選用"""

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=8006, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1)
    verify_ssl: bool = False
    ca_cert: str | None = None
    api_timeout: int = Field(default=30, ge=1, le=300)


class SetupStoragePublic(BaseModel):
    """測試連線時順帶列出的 storage（同名共享 storage 只列一筆）"""

    storage: str
    storage_type: str | None = None
    nodes: list[str]
    can_iso: bool
    can_vm: bool
    can_lxc: bool
    is_shared: bool
    avail_gb: float


class SetupProxmoxTestResult(BaseModel):
    success: bool
    is_cluster: bool = False
    nodes: list[ProxmoxNodePublic] = []
    storages: list[SetupStoragePublic] = []
    error: str | None = None


class SetupProxmoxCreate(ProxmoxConnectionCreate):
    """步驟二：建立第一組 PVE 連線；第一筆永遠是預設連線"""

    is_default: bool = True


class SetupProxmoxResult(BaseModel):
    connection_id: int
    name: str
    host: str
    nodes: list[ProxmoxNodePublic]
    storage_count: int
    # 連線本身存好了，但同步節點／storage 失敗時帶回原因（不擋精靈繼續）
    sync_error: str | None = None


class SetupSubnetResult(BaseModel):
    """步驟三寫入後的摘要（不含 IP 管理頁那份完整統計）"""

    cidr: str
    gateway: str
    bridge_name: str
    gateway_vm_ip: str
    dns_servers: str | None = None
    total_ips: int
    available_ips: int


class SetupCompleteResult(BaseModel):
    completed: bool
    completed_at: datetime | None
