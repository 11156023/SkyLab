"""Deletion request API schemas."""

import uuid

from pydantic import BaseModel, Field

from app.models.deletion_request import DeletionRequestStatus


class DeletionRequestCreated(BaseModel):
    """Response when accepting a delete request (HTTP 202)."""

    id: uuid.UUID
    vmid: int
    status: DeletionRequestStatus
    message: str = Field(default="Deletion request queued")
