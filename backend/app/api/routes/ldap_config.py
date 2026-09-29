"""LDAP 連線設定管理 API（僅管理員）。"""

from fastapi import APIRouter

from app.api.deps import AdminUser, SessionDep
from app.models import LdapConfig
from app.repositories import ldap_config as ldap_config_repo
from app.schemas.ldap import LdapConfigPublic, LdapConfigUpdate, LdapTestResult
from app.services.user import ldap_auth_service

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


@router.get("", response_model=LdapConfigPublic)
def get_config(session: SessionDep, _: AdminUser) -> LdapConfigPublic:
    return _to_public(ldap_config_repo.get_ldap_config(session=session))


@router.put("", response_model=LdapConfigPublic)
def update_config(
    session: SessionDep,
    current_user: AdminUser,
    config_in: LdapConfigUpdate,
) -> LdapConfigPublic:
    return _to_public(
        ldap_auth_service.update_config(session, config_in, current_user)
    )


@router.post("/test", response_model=LdapTestResult)
def test_connection(
    session: SessionDep,
    _: AdminUser,
    config_in: LdapConfigUpdate | None = None,
) -> LdapTestResult:
    """測試 service bind。可帶欄位覆寫（不落 DB）測試尚未儲存的設定。"""
    return ldap_auth_service.test_config(session, config_in)
