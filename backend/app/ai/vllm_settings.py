"""各 AI 功能共用的 vLLM 參數讀取（system-ai.json 的 ``<feature>.vllm`` 區段）。"""

from __future__ import annotations

from typing import Any

from app.ai.system_config import system_ai_env


class VLLMSectionSettings:
    """子類別只要提供 ``section``（該功能在 system-ai.json 的區段）即可。"""

    @property
    def section(self) -> Any:
        raise NotImplementedError

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
    def VLLM_ENABLE_THINKING(self) -> bool:
        return bool(self.section.vllm.enable_thinking)

    @property
    def VLLM_TIMEOUT(self) -> int:
        return int(self.section.vllm.timeout)

    @property
    def VLLM_TEMPERATURE(self) -> float:
        return float(self.section.vllm.temperature)

    @property
    def VLLM_CHAT_TEMPERATURE(self) -> float:
        if self.section.vllm.chat_temperature is not None:
            return float(self.section.vllm.chat_temperature)
        return self.VLLM_TEMPERATURE

    @property
    def VLLM_TOP_P(self) -> float:
        return float(self.section.vllm.top_p)

    @property
    def VLLM_TOP_K(self) -> int:
        return int(self.section.vllm.top_k)

    @property
    def VLLM_MAX_TOKENS(self) -> int:
        return int(self.section.vllm.max_tokens)

    @property
    def VLLM_CHAT_MAX_TOKENS(self) -> int:
        if self.section.vllm.chat_max_tokens is not None:
            return int(self.section.vllm.chat_max_tokens)
        return self.VLLM_MAX_TOKENS

    @property
    def VLLM_REPETITION_PENALTY(self) -> float:
        return float(self.section.vllm.repetition_penalty)
