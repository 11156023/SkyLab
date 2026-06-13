from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = BACKEND_ROOT / "config" / "system-ai.json"


class SystemAIEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_api_key: str = "vllm-secret-key-change-me"
    vllm_model_name: str = ""


class SystemAIVLLMConfig(BaseModel):
    enable_thinking: bool = False
    timeout: int = 30
    temperature: float = 0.6
    chat_temperature: float | None = None
    top_p: float = 0.95
    top_k: int = 20
    min_p: float = 0.0
    max_tokens: int = 1600
    chat_max_tokens: int | None = None
    presence_penalty: float | None = None
    repetition_penalty: float = 1.0


class TemplateRecommendationConfig(BaseModel):
    templates_dir: str = "../frontend/src/json"
    backend_node_gpu_map: dict[str, int] = Field(default_factory=dict)
    vllm: SystemAIVLLMConfig = Field(default_factory=SystemAIVLLMConfig)


class PVELogConfig(BaseModel):
    vllm: SystemAIVLLMConfig = Field(default_factory=SystemAIVLLMConfig)


class TeacherJudgeConfig(BaseModel):
    max_upload_size_mb: int = 10
    vllm: SystemAIVLLMConfig = Field(default_factory=SystemAIVLLMConfig)


class SystemAIConfig(BaseModel):
    template_recommendation: TemplateRecommendationConfig = Field(
        default_factory=TemplateRecommendationConfig
    )
    pve_log: PVELogConfig = Field(default_factory=PVELogConfig)
    teacher_judge: TeacherJudgeConfig = Field(default_factory=TeacherJudgeConfig)


def load_system_ai_config() -> SystemAIConfig:
    if not CONFIG_FILE.exists():
        return SystemAIConfig()

    payload = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("system-ai.json must be a JSON object")

    return SystemAIConfig.model_validate(payload)


system_ai_env = SystemAIEnvSettings()
system_ai_config = load_system_ai_config()
