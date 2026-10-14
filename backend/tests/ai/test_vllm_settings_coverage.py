"""Coverage tests for app.ai.vllm_settings section-backed properties."""

from types import SimpleNamespace

import pytest

from app.ai.vllm_settings import VLLMSectionSettings


class _Settings(VLLMSectionSettings):
    def __init__(self, **vllm) -> None:
        self._section = SimpleNamespace(vllm=SimpleNamespace(**vllm))

    @property
    def section(self):
        return self._section


def test_base_section_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        _ = VLLMSectionSettings().section


def test_overrides_and_fallbacks() -> None:
    explicit = _Settings(
        temperature=0.3,
        chat_temperature=0.9,
        top_p=0.8,
        max_tokens=100,
        chat_max_tokens=200,
    )
    assert explicit.VLLM_TEMPERATURE == 0.3
    assert explicit.VLLM_CHAT_TEMPERATURE == 0.9
    assert explicit.VLLM_TOP_P == 0.8
    assert explicit.VLLM_MAX_TOKENS == 100
    assert explicit.VLLM_CHAT_MAX_TOKENS == 200

    fallback = _Settings(
        temperature=0.4, chat_temperature=None, max_tokens=50, chat_max_tokens=None
    )
    assert fallback.VLLM_CHAT_TEMPERATURE == 0.4
    assert fallback.VLLM_CHAT_MAX_TOKENS == 50
