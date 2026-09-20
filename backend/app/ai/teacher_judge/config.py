from __future__ import annotations

from app.ai.system_config import system_ai_config, system_ai_env


class TeacherJudgeSettings:
    @property
    def section(self):
        return system_ai_config.teacher_judge

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
    def VLLM_CHAT_MAX_TOOL_ROUNDS(self) -> int:
        if self.section.vllm.chat_max_tool_rounds is not None:
            return int(self.section.vllm.chat_max_tool_rounds)
        return 6

    @property
    def VLLM_REPETITION_PENALTY(self) -> float:
        return float(self.section.vllm.repetition_penalty)

    @property
    def VLLM_MAX_UPLOAD_SIZE_MB(self) -> int:
        return int(self.section.max_upload_size_mb)


settings = TeacherJudgeSettings()
