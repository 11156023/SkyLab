"""平台入口設定資料庫操作"""

from datetime import datetime, timezone

from sqlmodel import Session

from app.models.platform_entry_config import PlatformEntryConfig

_SINGLETON_ID = 1


def get_platform_entry_config(session: Session) -> PlatformEntryConfig | None:
    return session.get(PlatformEntryConfig, _SINGLETON_ID)


def upsert_platform_entry_config(
    session: Session,
    *,
    enabled: bool,
    domain: str,
    upstream_host: str,
    upstream_port: int,
    enable_https: bool,
) -> PlatformEntryConfig:
    config = session.get(PlatformEntryConfig, _SINGLETON_ID)
    if config is None:
        config = PlatformEntryConfig(id=_SINGLETON_ID)
    config.enabled = enabled
    config.domain = domain
    config.upstream_host = upstream_host
    config.upstream_port = upstream_port
    config.enable_https = enable_https
    config.updated_at = datetime.now(timezone.utc)
    session.add(config)
    session.commit()
    session.refresh(config)
    return config


__all__ = [
    "get_platform_entry_config",
    "upsert_platform_entry_config",
]
