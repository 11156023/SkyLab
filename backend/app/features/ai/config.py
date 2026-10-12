from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import settings as core_settings

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env"

# .env.example 出貨的 AI_API_API_KEY 範本值；仍是它（或空白）代表還沒設定真正的 key。
AI_API_KEY_TEMPLATE_VALUE = "ai-api-secret-key-change-me"


class AIAPIEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    ai_api_base_url: str = "http://litellm:4000"
    ai_api_api_key: str = AI_API_KEY_TEMPLATE_VALUE
    ai_api_timeout: int = 120
    ai_api_max_request_body_bytes: int = 1_048_576

    # Admin-only runtime observation uses the same restricted Campus service
    # identity. Leave the key unset to disable the snapshot endpoint.
    litellm_runtime_base_url: str = "http://litellm:4000"
    litellm_runtime_api_key: str | None = None

    ai_api_rate_limit_per_minute: int = 20
    ai_api_rate_limit_window_seconds: int = 60

    ai_api_public_base_url: str = "http://localhost:5000"

    # Redis 的開關與連線字串一律以 core settings 為準（見 core/config.py）。
    # 這裡若再讀一次 .env，兩份設定就可能不一致：限流以為關著、arq 以為開著。
    @property
    def redis_enabled(self) -> bool:
        return core_settings.REDIS_ENABLED

    @property
    def redis_url(self) -> str:
        return core_settings.REDIS_URL

    @property
    def ai_api_key_is_placeholder(self) -> bool:
        key = self.ai_api_api_key.strip()
        return not key or key == AI_API_KEY_TEMPLATE_VALUE

    @property
    def ai_api_enabled(self) -> bool:
        """local 以外的環境，AI_API_API_KEY 仍是空白或範本值時停用 AI API。

        只停用 AI API（回「未設定」錯誤、不送去 LiteLLM），backend 照常啟動；
        系統健康卡與啟動日誌會提示。local 開發照舊放行。
        """
        return core_settings.ENVIRONMENT == "local" or not self.ai_api_key_is_placeholder

    @property
    def resolved_public_base_url(self) -> str:
        return self.ai_api_public_base_url.strip()

    @property
    def resolved_upstream_base_url(self) -> str:
        """AI proxy 的上游位址（預設是 LiteLLM gateway，不是直連 vLLM）。"""
        return self.ai_api_base_url.strip()


settings = AIAPIEnvSettings()
