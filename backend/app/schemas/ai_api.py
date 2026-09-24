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
    """金鑰的一般呈現：只有前綴，永遠不含明文。

    清單類端點（``GET /credentials/my``）一律用這個 schema——把明文金鑰放進
    列表回應，等於每次開頁都把所有金鑰再散佈一次（瀏覽器快取、日誌、截圖）。
    """

    id: uuid.UUID
    request_id: uuid.UUID
    base_url: str
    api_key_prefix: str
    api_key_name: str
    rate_limit: int | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime


class AIAPICredentialWithSecret(AIAPICredentialPublic):
    """核發／輪替當下的一次性回應。

    ``api_key`` 只在操作者本人輪替自己的金鑰時帶明文；管理員代操時留 None，
    只回前綴——代操的目的是撤換，不是取得別人的金鑰。
    """

    api_key: str | None = None


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
