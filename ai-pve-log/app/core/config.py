from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# parents[3] = SkyLab/ (全局根目錄，含共用 .env)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        populate_by_name=True,
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8012, alias="pve_log_port", ge=1, le=65535)
    api_v1_str: str = Field(default="/api/v1")

    # Proxmox 連線
    proxmox_host: str = Field(default="localhost")
    proxmox_user: str = Field(default="")
    proxmox_password: str = Field(default="")
    proxmox_verify_ssl: bool = Field(default=False)
    proxmox_api_timeout: int = Field(default=30, ge=3, le=300)

    # 批量收集行為
    collector_max_workers: int = Field(default=8, ge=1, le=32)
    collector_fetch_config: bool = Field(default=True)
    collector_fetch_lxc_interfaces: bool = Field(default=True)
    collector_retry_attempts: int = Field(default=3, ge=1, le=10)
    collector_retry_backoff: float = Field(default=0.3, ge=0.0, le=10.0)

    # vLLM / AI 設定（共用全局 VLLM_* 變數）
    vllm_base_url: str = Field(
        default="http://localhost:8000/v1",
        alias="vllm_base_url",
    )
    vllm_api_key: str = Field(
        default="",
        alias="vllm_api_key",
    )
    vllm_model_name: str = Field(
        default="",
        alias="vllm_model_name",
    )
    # AI 對話逾時（秒），預留給快照收集 + LLM 兩次呼叫
    chat_timeout: int = Field(default=120, ge=10, le=600)

    # ── SkyLab 後端 API（用於取得 SSH key）──────────────────────────
    # 重用 AI_API_PUBLIC_BASE_URL（已在 .env 設定），補上 /api/v1 suffix
    skylab_api_public_base: str = Field(
        default="http://localhost:8000",
        alias="ai_api_public_base_url",
    )
    # 使用 FIRST_SUPERUSER / FIRST_SUPERUSER_PASSWORD 作為 SkyLab 登入憑證
    skylab_api_user: str = Field(
        default="",
        alias="first_superuser",
    )
    skylab_api_password: str = Field(
        default="",
        alias="first_superuser_password",
    )

    # ── SSH 行為設定 ──────────────────────────────────────────────────────
    # 是否停用 SSH host key 驗證（內網環境建議 true）
    ssh_insecure_host_key: bool = Field(default=True)
    # SSH 預設登入使用者
    ssh_default_user: str = Field(default="root")
    # SSH / HTTP 請求逾時（秒）
    ssh_timeout: int = Field(default=30, ge=5, le=120)

    @property
    def skylab_api_base(self) -> str:
        """SkyLab 後端 API base URL（含 /api/v1）"""
        return self.skylab_api_public_base.rstrip("/") + "/api/v1"


settings = Settings()
