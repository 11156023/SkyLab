import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.ai_api_request import AIAPIRequestStatus


class AIAPIRequestCreate(BaseModel):
    purpose: str = Field(min_length=10, max_length=2000)
    api_key_name: str = Field(default="test", min_length=1, max_length=20)
    duration: str = Field(default="never", max_length=20)


class AIAPIRequestReview(BaseModel):
    status: AIAPIRequestStatus
    review_comment: str | None = Field(default=None, max_length=2000)


class AIAPIRequestPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    purpose: str
    api_key_name: str
    duration: str
    rate_limit: int | None = None
    status: AIAPIRequestStatus
    reviewer_id: uuid.UUID | None = None
    reviewer_email: str | None = None
    review_comment: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class AIAPIRequestsPublic(BaseModel):
    data: list[AIAPIRequestPublic]
    count: int


class AIAPICredentialPublic(BaseModel):
    id: uuid.UUID
    request_id: uuid.UUID
    base_url: str
    api_key: str
    api_key_prefix: str
    api_key_name: str
    rate_limit: int | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class AIAPICredentialsPublic(BaseModel):
    data: list[AIAPICredentialPublic]
    count: int


AIAPICredentialStatus = Literal["active", "inactive"]
AIAPICredentialInactiveReason = Literal["revoked", "expired"]


class AIAPICredentialAdminPublic(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str | None = None
    user_full_name: str | None = None
    user_role: str | None = None
    request_id: uuid.UUID
    base_url: str
    api_key_prefix: str
    api_key_name: str
    rate_limit: int | None = None
    status: AIAPICredentialStatus
    inactive_reason: AIAPICredentialInactiveReason | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    request_purpose: str | None = None
    reviewer_email: str | None = None
    reviewer_full_name: str | None = None
    reviewed_at: datetime | None = None
    last_used_at: datetime | None = None


class AIAPICredentialsAdminPublic(BaseModel):
    data: list[AIAPICredentialAdminPublic]
    count: int
    total_count: int = 0
    active_count: int = 0
    inactive_count: int = 0


class AIAPICredentialUpdate(BaseModel):
    api_key_name: str = Field(min_length=1, max_length=20)
