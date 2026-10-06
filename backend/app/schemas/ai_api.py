import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.ai_api_request import AIAPIRequestStatus

# 與 ai_gateway_service.review_request 換算 expires_at 的期限一一對應
AIAPIKeyDuration = Literal["1d", "7d", "30d", "90d", "never"]
AIAPIReviewDecision = Literal[
    AIAPIRequestStatus.approved,
    AIAPIRequestStatus.rejected,
]


class AIAPIRequestCreate(BaseModel):
    purpose: str = Field(min_length=10, max_length=2000)
    api_key_name: str = Field(default="test", min_length=1, max_length=20)
    # 教師／管理員維持永久預設；學生必須明確提供有限期限，由服務層驗證。
    duration: AIAPIKeyDuration = "never"


class AIAPIRequestReview(BaseModel):
    """審核 AI API 申請：結果只能是核准或駁回"""

    status: AIAPIReviewDecision
    review_comment: str | None = Field(default=None, max_length=2000)


class AIAPIRequestBulkReject(BaseModel):
    """管理員一次駁回多筆待審 AI API 申請。"""

    request_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    review_comment: str = Field(min_length=1, max_length=2000)

    @field_validator("request_ids")
    @classmethod
    def _unique_request_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("request_ids 不可重複")
        return value

    @field_validator("review_comment")
    @classmethod
    def _non_blank_comment(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("駁回理由不可為空白")
        return value


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
    """擁有者的單把金鑰詳細資料及輪替回應。

    ``api_key`` 只對擁有者本人提供明文；管理員代操輪替時留 None，
    只回前綴——代操的目的是撤換，不是取得別人的金鑰。
    """

    api_key: str | None = None


class AIAPICredentialsPublic(BaseModel):
    data: list[AIAPICredentialPublic]
    count: int
    # Base URL 不是機密：還沒有核發金鑰的人也要能在 Quick Start 看到要連哪裡。
    # 設定留空時為 None，前端改用金鑰上的快照或顯示尚未設定。
    public_base_url: str | None = None


AIAPICredentialStatus = Literal["active", "inactive"]
AIAPICredentialInactiveReason = Literal["deleted", "revoked", "expired"]


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
    deleted_at: datetime | None = None
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
