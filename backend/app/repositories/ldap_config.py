"""LDAP 設定 singleton 的 DB 存取。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.models import LdapConfig

LDAP_CONFIG_ID = 1

# 可以清空的欄位：表單清空時送 null（或空字串），要真的存成 NULL，
# 否則目錄群組 → 管理員／老師的對應永遠拿不掉。其他欄位維持「None＝不改」。
_CLEARABLE_FIELDS = frozenset({"teacher_group_dn", "admin_group_dn"})


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
        if not hasattr(config, key):
            continue
        if key in _CLEARABLE_FIELDS:
            setattr(config, key, value or None)
        elif value is not None:
            setattr(config, key, value)
    config.updated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config
