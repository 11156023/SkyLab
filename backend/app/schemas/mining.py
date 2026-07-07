"""反挖礦事件 API schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import MiningIncidentStatus


class MiningIncidentPublic(BaseModel):
    """疑似挖礦事件（管理員視圖）。"""

    id: uuid.UUID
    vmid: int
    user_id: uuid.UUID
    node: str
    resource_type: str
    avg_cpu: float
    window_hours: int
    snapshot_name: str | None = None
    status: MiningIncidentStatus
    detected_at: datetime
    suspended_at: datetime | None = None
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None


class MiningDismissRequest(BaseModel):
    """誤判解除：可一併將資源加入豁免。"""

    exempt: bool = False
    note: str | None = Field(default=None, max_length=1024)


class MiningExemptRequest(BaseModel):
    """設定/解除資源的挖礦偵測豁免。"""

    exempt: bool


class MiningExemptResponse(BaseModel):
    vmid: int
    exempt: bool
