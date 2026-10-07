"""平台政策查詢 API：目前提供 VM 登入帳號（cloud-init ciuser）命名規則。"""

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.domain import username_policy
from app.schemas.username_policy import (
    UsernameCheckRequest,
    UsernameCheckResult,
    UsernameRulesPublic,
    UsernameViolationPublic,
)

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get("/username", response_model=UsernameRulesPublic)
def get_username_rules(_: CurrentUser) -> UsernameRulesPublic:
    return UsernameRulesPublic(**username_policy.rules())


@router.post("/username/check", response_model=UsernameCheckResult)
def check_username(body: UsernameCheckRequest, _: CurrentUser) -> UsernameCheckResult:
    violations = username_policy.validate_username(body.username)
    return UsernameCheckResult(
        ok=not username_policy.errors_of(violations),
        violations=[UsernameViolationPublic(**v.to_dict()) for v in violations],
    )
