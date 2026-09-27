from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.system_config import system_ai_config
from app.ai.vllm_settings import VLLMSectionSettings

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(VLLMSectionSettings, BaseSettings):
    """PVE Log 設定：collector／SSH 參數來自 .env，vLLM 參數來自 system-ai.json。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    collector_max_workers: int = Field(default=8, ge=1, le=32)
    collector_fetch_config: bool = Field(default=True)
    collector_fetch_lxc_interfaces: bool = Field(default=True)
    collector_retry_attempts: int = Field(default=3, ge=1, le=10)
    collector_retry_backoff: float = Field(default=0.3, ge=0.0, le=10.0)

    ssh_default_user: str = Field(default="root")
    ssh_timeout: int = Field(default=30, ge=5, le=120)

    @property
    def section(self) -> Any:
        return system_ai_config.pve_log


settings = Settings()
