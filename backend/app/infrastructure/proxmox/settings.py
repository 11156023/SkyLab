from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_PROXMOX_POOL_NAME = "SkyLab"


@dataclass
class ProxmoxSettings:
    host: str
    user: str
    password: str
    verify_ssl: bool
    iso_storage: str
    data_storage: str
    api_timeout: int
    task_check_interval: int
    pool_name: str
    ca_cert: str | None = None
    local_subnet: str | None = None
    default_node: str | None = None


def get_proxmox_settings() -> ProxmoxSettings:
    """Load Proxmox settings from DB. Raises RuntimeError if not configured."""
    from cryptography.fernet import InvalidToken
    from sqlmodel import Session

    from app.core.db import engine
    from app.repositories.proxmox_config import (
        get_decrypted_password,
        get_proxmox_config,
    )

    with Session(engine) as session:
        config = get_proxmox_config(session)

    if config is None:
        raise RuntimeError(
            "Proxmox 尚未設定，請至管理員介面完成 Proxmox 連線設定。"
        )

    try:
        password = get_decrypted_password(config)
    except InvalidToken as exc:
        raise RuntimeError(
            "Proxmox 密碼解密失敗，SECRET_KEY 可能已變更。"
            " 請至管理員介面重新儲存 Proxmox 連線設定以更新加密密碼。"
        ) from exc

    return ProxmoxSettings(
        host=config.host,
        user=config.user,
        password=password,
        verify_ssl=config.verify_ssl,
        iso_storage=config.iso_storage,
        data_storage=config.data_storage,
        api_timeout=config.api_timeout,
        task_check_interval=config.task_check_interval,
        pool_name=config.pool_name,
        ca_cert=config.ca_cert,
        local_subnet=config.local_subnet,
        default_node=config.default_node,
    )
