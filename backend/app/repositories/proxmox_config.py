"""放置／排程策略 singleton（proxmox_config）的資料庫操作"""

from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.models.proxmox_config import ProxmoxConfig

_SINGLETON_ID = 1


def get_proxmox_config(session: Session) -> ProxmoxConfig | None:
    return session.get(ProxmoxConfig, _SINGLETON_ID)


def upsert_proxmox_config(session: Session, **fields: Any) -> ProxmoxConfig:
    """寫入策略欄位；尚無設定列時以 model 預設值建立後再套用。"""
    config = session.get(ProxmoxConfig, _SINGLETON_ID)
    if config is None:
        config = ProxmoxConfig(id=_SINGLETON_ID)
    for name, value in fields.items():
        if name not in ProxmoxConfig.model_fields or name in {"id", "updated_at"}:
            raise ValueError(f"unknown proxmox_config field: {name}")
        setattr(config, name, value)
    config.updated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


__all__ = [
    "get_proxmox_config",
    "upsert_proxmox_config",
]
