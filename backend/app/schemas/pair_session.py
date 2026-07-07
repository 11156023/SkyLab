"""Pair Mode API schemas。"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class PairSessionCreate(BaseModel):
    vmid: int
    invitee_user_id: uuid.UUID


class PairSessionPublic(BaseModel):
    id: str
    vmid: int
    owner_id: uuid.UUID
    invitee_id: uuid.UUID
    owner_name: str | None = None
    invitee_name: str | None = None
    created_at: datetime
