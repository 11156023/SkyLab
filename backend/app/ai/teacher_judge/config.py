from __future__ import annotations

from typing import Any

from app.ai.system_config import system_ai_config
from app.ai.vllm_settings import VLLMSectionSettings


class TeacherJudgeSettings(VLLMSectionSettings):
    @property
    def section(self) -> Any:
        return system_ai_config.teacher_judge

    @property
    def VLLM_CHAT_MAX_TOOL_ROUNDS(self) -> int:
        if self.section.vllm.chat_max_tool_rounds is not None:
            return int(self.section.vllm.chat_max_tool_rounds)
        return 6

    @property
    def VLLM_MAX_UPLOAD_SIZE_MB(self) -> int:
        return int(self.section.max_upload_size_mb)


settings = TeacherJudgeSettings()
