"""登入安全政策 API：所有登入者可讀（前端據此顯示強制綁定畫面）、管理員可改。"""

from fastapi import APIRouter

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.models import AuditAction, AuthPolicy
from app.repositories import auth_policy as auth_policy_repo
from app.schemas.auth_policy import AuthPolicyPublic, AuthPolicyUpdate
from app.services.user import audit_service

router = APIRouter(tags=["auth-policy"])


def _to_public(policy: AuthPolicy) -> AuthPolicyPublic:
    return AuthPolicyPublic(
        totp_required=policy.totp_required, updated_at=policy.updated_at
    )


@router.get("/auth-policy", response_model=AuthPolicyPublic)
def get_auth_policy(session: SessionDep, _: CurrentUser) -> AuthPolicyPublic:
    return _to_public(auth_policy_repo.get_auth_policy(session=session))


@router.put("/admin/auth-policy", response_model=AuthPolicyPublic)
def update_auth_policy(
    session: SessionDep, current_user: AdminUser, body: AuthPolicyUpdate
) -> AuthPolicyPublic:
    """開關「強制所有使用者啟用兩步驟驗證」。

    開啟後尚未綁定的使用者（包含管理員自己）登入後只能進入綁定畫面；
    使用者手機遺失時由管理員用 ``DELETE /users/{id}/totp`` 重設。
    """
    previous = auth_policy_repo.get_auth_policy(session=session).totp_required
    policy = auth_policy_repo.update_auth_policy(
        session=session, totp_required=body.totp_required
    )
    if previous != policy.totp_required:
        audit_service.log_action(
            session=session,
            user_id=current_user.id,
            action=AuditAction.auth_policy_update,
            details=(
                f"Admin {current_user.email} set totp_required={policy.totp_required}"
            ),
        )
    return _to_public(policy)
