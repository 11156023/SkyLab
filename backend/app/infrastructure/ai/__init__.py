"""AI infrastructure adapters."""

from app.infrastructure.ai.vllm_client import (
    VLLMCapability as VLLMCapability,
)
from app.infrastructure.ai.vllm_client import (
    VLLMClient as VLLMClient,
)
from app.infrastructure.ai.vllm_client import (
    VLLMProfileError as VLLMProfileError,
)
from app.infrastructure.ai.vllm_client import (
    VLLMRequestProfile as VLLMRequestProfile,
)
from app.infrastructure.ai.vllm_client import (
    close_all_vllm_clients as close_ai_clients,
)
from app.infrastructure.ai.vllm_client import (
    required_capabilities as required_capabilities,
)
from app.infrastructure.ai.vllm_client import (
    validate_request_profile as validate_request_profile,
)

__all__ = [
    "VLLMCapability",
    "VLLMClient",
    "VLLMProfileError",
    "VLLMRequestProfile",
    "close_ai_clients",
    "required_capabilities",
    "validate_request_profile",
]
