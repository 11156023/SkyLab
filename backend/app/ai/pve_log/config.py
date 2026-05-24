from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.system_config import system_ai_config, system_ai_env

PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    proxmox_host: str = Field(default="localhost")
    proxmox_user: str = Field(default="")
    proxmox_password: str = Field(default="")
    proxmox_verify_ssl: bool = Field(default=False)
    proxmox_api_timeout: int = Field(default=30, ge=3, le=300)

    collector_max_workers: int = Field(default=8, ge=1, le=32)
    collector_fetch_config: bool = Field(default=True)
    collector_fetch_lxc_interfaces: bool = Field(default=True)
    collector_retry_attempts: int = Field(default=3, ge=1, le=10)
    collector_retry_backoff: float = Field(default=0.3, ge=0.0, le=10.0)

    skylab_api_public_base: str = Field(
        default="http://localhost:8000",
        alias="ai_api_public_base_url",
        description="僅獨立 ai-pve-log 子服務使用；主後端內嵌模組走內部 DB 查詢",
    )
    skylab_api_user: str = Field(
        default="",
        alias="first_superuser",
    )
    skylab_api_password: str = Field(
        default="",
        alias="first_superuser_password",
    )
    ssh_insecure_host_key: bool = Field(default=True)
    ssh_default_user: str = Field(default="root")
    ssh_timeout: int = Field(default=30, ge=5, le=120)

    @property
    def section(self):
        return system_ai_config.pve_log

    @property
    def VLLM_BASE_URL(self) -> str:
        return system_ai_env.vllm_base_url

    @property
    def VLLM_API_KEY(self) -> str:
        return system_ai_env.vllm_api_key

    @property
    def VLLM_MODEL_NAME(self) -> str:
        return system_ai_env.vllm_model_name.strip()

    @property
    def VLLM_TIMEOUT(self) -> int:
        return int(self.section.vllm.timeout)

    @property
    def VLLM_TEMPERATURE(self) -> float:
        return float(self.section.vllm.temperature)

    @property
    def VLLM_MAX_TOKENS(self) -> int:
        return int(self.section.vllm.max_tokens)

    @property
    def skylab_api_base(self) -> str:
        return self.skylab_api_public_base.rstrip("/") + "/api/v1"


settings = Settings()
