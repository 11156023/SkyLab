from __future__ import annotations

from typing import Any

from app.ai.system_config import system_ai_config
from app.ai.vllm_settings import VLLMSectionSettings


class TemplateRecommendationSettings(VLLMSectionSettings):
    @property
    def section(self) -> Any:
        return system_ai_config.template_recommendation

    @property
    def parsed_backend_node_gpu_map(self) -> dict[str, int]:
        parsed: dict[str, int] = {}
        for key, value in self.section.backend_node_gpu_map.items():
            try:
                parsed[str(key)] = max(int(value), 0)
            except (TypeError, ValueError):
                continue
        return parsed

    @property
    def VLLM_MIN_P(self) -> float:
        return float(self.section.vllm.min_p)

    @property
    def VLLM_PRESENCE_PENALTY(self) -> float:
        if self.section.vllm.presence_penalty is not None:
            return float(self.section.vllm.presence_penalty)
        return 0.0


settings = TemplateRecommendationSettings()
