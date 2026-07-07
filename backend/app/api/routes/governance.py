"""治理設定 API（僅管理員）。"""

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.models import AuditAction
from app.repositories import governance as governance_repo
from app.schemas.monitoring import GovernanceConfigPublic, GovernanceConfigUpdate
from app.services.user import audit_service

router = APIRouter(prefix="/governance", tags=["governance"])


@router.get("/config", response_model=GovernanceConfigPublic)
def get_config(session: SessionDep, _: AdminUser) -> GovernanceConfigPublic:
    config = governance_repo.get_governance_config(session=session)
    return GovernanceConfigPublic.model_validate(config, from_attributes=True)


@router.put("/config", response_model=GovernanceConfigPublic)
def update_config(
    session: SessionDep,
    current_user: AdminUser,
    config_in: GovernanceConfigUpdate,
) -> GovernanceConfigPublic:
    config = governance_repo.update_governance_config(
        session=session,
        data=config_in.model_dump(exclude_unset=True),
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details="Updated governance config",
    )
    return GovernanceConfigPublic.model_validate(config, from_attributes=True)
