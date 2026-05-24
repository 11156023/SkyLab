"""Gateway VM 設定資料庫操作"""

from datetime import datetime, timezone

from cryptography.fernet import InvalidToken
from sqlmodel import Session

from app.core.security import decrypt_value, encrypt_value
from app.exceptions import AppError
from app.models.gateway_config import GatewayConfig

_SINGLETON_ID = 1


def get_gateway_config(session: Session) -> GatewayConfig | None:
    return session.get(GatewayConfig, _SINGLETON_ID)


def upsert_connection_settings(
    session: Session,
    host: str,
    ssh_port: int,
    ssh_user: str,
) -> GatewayConfig:
    config = session.get(GatewayConfig, _SINGLETON_ID)
    if config is None:
        config = GatewayConfig(
            id=_SINGLETON_ID,
            host=host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
        )
        session.add(config)
    else:
        config.host = host
        config.ssh_port = ssh_port
        config.ssh_user = ssh_user
        config.updated_at = datetime.now(timezone.utc)
        session.add(config)
    session.commit()
    session.refresh(config)
    return config


def save_keypair(
    session: Session,
    private_key_pem: str,
    public_key: str,
) -> GatewayConfig:
    config = session.get(GatewayConfig, _SINGLETON_ID)
    if config is None:
        config = GatewayConfig(
            id=_SINGLETON_ID,
            encrypted_private_key=encrypt_value(private_key_pem),
            public_key=public_key,
        )
        session.add(config)
    else:
        config.encrypted_private_key = encrypt_value(private_key_pem)
        config.public_key = public_key
        config.updated_at = datetime.now(timezone.utc)
        session.add(config)
    session.commit()
    session.refresh(config)
    return config


def get_decrypted_private_key(config: GatewayConfig) -> str:
    try:
        return decrypt_value(config.encrypted_private_key)
    except InvalidToken as e:
        raise AppError(
            "無法解密儲存的 SSH 私鑰：加密金鑰已變更，請重新產生並儲存 Gateway SSH 金鑰對",
            status_code=400,
        ) from e


__all__ = [
    "get_gateway_config",
    "upsert_connection_settings",
    "save_keypair",
    "get_decrypted_private_key",
]
