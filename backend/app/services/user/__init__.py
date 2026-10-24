from __future__ import annotations

from importlib import import_module

__all__ = [
    "audit_service",
    "auth_service",
    "avatar_service",
    "ldap_auth_service",
    "roster_csv",
    "totp_service",
    "turnstile_service",
    "user_group_service",
    "user_service",
]

_MODULES = {
    "audit_service": "app.services.user.audit_service",
    "auth_service": "app.services.user.auth_service",
    "avatar_service": "app.services.user.avatar_service",
    "ldap_auth_service": "app.services.user.ldap_auth_service",
    "roster_csv": "app.services.user.roster_csv",
    "totp_service": "app.services.user.totp_service",
    "turnstile_service": "app.services.user.turnstile_service",
    "user_group_service": "app.services.user.user_group_service",
    "user_service": "app.services.user.user_service",
}


def __getattr__(name: str):
    if name in _MODULES:
        return import_module(_MODULES[name])
    raise AttributeError(name)
