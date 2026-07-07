"""LDAP 設定 singleton 的 DB 存取。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.models import LdapConfig

LDAP_CONFIG_ID = 1


def get_ldap_config(*, session: Session) -> LdapConfig:
    """取得 LDAP 設定 singleton；不存在則以預設值（disabled）建立。"""
    config = session.get(LdapConfig, LDAP_CONFIG_ID)
    if config is None:
        config = LdapConfig(id=LDAP_CONFIG_ID)
        session.add(config)
        session.commit()
        session.refresh(config)
    return config


def update_ldap_config(*, session: Session, data: dict[str, Any]) -> LdapConfig:
    config = get_ldap_config(session=session)
    for key, value in data.items():
        if value is not None and hasattr(config, key):
            setattr(config, key, value)
    config.updated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config
