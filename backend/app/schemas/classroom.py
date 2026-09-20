"""虛擬教室互動相關 schemas"""

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# ===== Request Schemas =====


class ClassroomSessionCreate(BaseModel):
    """建立班級教室 session（monitor=觀看學生 VM；broadcast=直播示範）。"""

    vmid: int
    mode: Literal["monitor", "broadcast"]
    class_id: uuid.UUID


class ClassroomControlRequest(BaseModel):
    """接管 / 釋放學生 VM 控制權（僅 monitor session 發起者）"""

    action: Literal["take", "release"]


# ===== Response Schemas =====


class ClassroomVm(BaseModel):
    """學生的 VM 摘要"""

    vmid: int
    node_key: str | None = None
    display_label: str | None = None
    name: str | None = None
    status: str | None = None  # "running" | "stopped" | None（叢集查不到）
    vm_type: str | None = None  # "qemu" | "lxc"（教室觀看僅支援 qemu）


class ClassroomStudent(BaseModel):
    """教室學生卡片資料"""

    user_id: uuid.UUID
    email: EmailStr
    full_name: str | None = None
    vms: list[ClassroomVm] = Field(default_factory=list)
    online: bool = False


class ClassroomSessionPublic(BaseModel):
    """教室 session 公開資料"""

    id: str
    vmid: int
    mode: str
    class_id: uuid.UUID
    started_by: uuid.UUID
    controller_user_id: uuid.UUID | None = None
    subscriber_count: int = 0


class ClassroomLivePublic(BaseModel):
    """學生查詢自己班級進行中的直播（無直播時 session 為 null）。"""

    session: ClassroomSessionPublic | None = None
