"""VM 登入帳號命名政策 API 的請求／回應。"""

from typing import Literal

from pydantic import BaseModel, Field


class UsernameRulesPublic(BaseModel):
    """前端即時驗證用的規則（來源 backend/config/username_policy.yaml）。"""

    pattern: str
    min_length: int
    max_length: int
    reserved: list[str]
    reserved_prefixes: list[str]
    platform_reserved: list[str]
    default_user_warn: list[str]


class UsernameCheckRequest(BaseModel):
    # 不設長度上限：超長本身就是要回報的違規（LNX_FORMAT）
    username: str = Field(max_length=256)


class UsernameViolationPublic(BaseModel):
    code: str
    message: str
    severity: Literal["error", "warning"]


class UsernameCheckResult(BaseModel):
    ok: bool
    violations: list[UsernameViolationPublic]
