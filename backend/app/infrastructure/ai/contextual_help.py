from __future__ import annotations

from app.ai.system_config import system_ai_env
from app.infrastructure.ai.vllm_client import VLLMClient

client = VLLMClient(
    base_url=system_ai_env.vllm_base_url,
    api_key=system_ai_env.vllm_api_key,
    default_timeout=15.0,
)
