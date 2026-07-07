"""LDAP 連線設定管理 API（僅管理員）。"""

import logging

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.core.security import encrypt_value
from app.exceptions import AppError
from app.infrastructure import ldap as ldap_client
from app.models import AuditAction, LdapConfig
from app.repositories import ldap_config as ldap_config_repo
from app.schemas.ldap import LdapConfigPublic, LdapConfigUpdate, LdapTestResult
from app.services.user import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ldap-config", tags=["ldap-config"])


def _to_public(config: LdapConfig) -> LdapConfigPublic:
    return LdapConfigPublic(
        enabled=config.enabled,
        server_uri=config.server_uri,
        use_starttls=config.use_starttls,
        bind_dn=config.bind_dn,
        bind_password_set=bool(config.encrypted_bind_password),
        user_search_base=config.user_search_base,
        user_filter_template=config.user_filter_template,
        email_attribute=config.email_attribute,
        name_attribute=config.name_attribute,
        teacher_group_dn=config.teacher_group_dn,
        admin_group_dn=config.admin_group_dn,
        auto_create_users=config.auto_create_users,
        connect_timeout_seconds=config.connect_timeout_seconds,
        updated_at=config.updated_at,
    )


def _update_data(config_in: LdapConfigUpdate) -> dict[str, object]:
    data = config_in.model_dump(exclude_unset=True, exclude={"bind_password"})
    if config_in.bind_password:
        data["encrypted_bind_password"] = encrypt_value(config_in.bind_password)
    return data


@router.get("", response_model=LdapConfigPublic)
def get_config(session: SessionDep, _: AdminUser) -> LdapConfigPublic:
    return _to_public(ldap_config_repo.get_ldap_config(session=session))


@router.put("", response_model=LdapConfigPublic)
def update_config(
    session: SessionDep,
    current_user: AdminUser,
    config_in: LdapConfigUpdate,
) -> LdapConfigPublic:
    config = ldap_config_repo.update_ldap_config(
        session=session, data=_update_data(config_in)
    )
    audit_service.log_action(
        session=session,
        user_id=current_user.id,
        action=AuditAction.config_update,
        details="Updated LDAP config",
    )
    return _to_public(config)


@router.post("/test", response_model=LdapTestResult)
def test_connection(
    session: SessionDep,
    _: AdminUser,
    config_in: LdapConfigUpdate | None = None,
) -> LdapTestResult:
    """測試 service bind。可帶欄位覆寫（不落 DB）測試尚未儲存的設定。"""
    config = ldap_config_repo.get_ldap_config(session=session)
    if config_in is not None:
        # 覆寫測試用複本（不加入 session、不落 DB）
        test_config = LdapConfig(**config.model_dump())
        for key, value in _update_data(config_in).items():
            if hasattr(test_config, key):
                setattr(test_config, key, value)
        config = test_config
    try:
        ldap_client.test_bind(config)
    except AppError as exc:
        return LdapTestResult(ok=False, message=exc.message)
    return LdapTestResult(ok=True, message="LDAP service bind 成功")
