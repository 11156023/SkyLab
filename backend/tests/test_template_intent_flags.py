from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.ai.template_recommendation import recommendation_service
from app.ai.template_recommendation.recommendation_service import (
    _extract_user_signal_flags,
)
from app.ai.template_recommendation.schemas import ChatMessage, ChatRequest


def _flags(text: str) -> dict[str, bool]:
    return _extract_user_signal_flags([ChatMessage(role="user", content=text)])


def test_windows_remote_desktop_sets_windows_flag() -> None:
    assert _flags("我要 Windows 遠端桌面")["needs_windows"] is True


def test_pytorch_cuda_sets_gpu_flag() -> None:
    assert _flags("我要跑 PyTorch / CUDA")["requires_gpu"] is True


def test_mysql_postgresql_sets_database_flag() -> None:
    assert _flags("我要 MySQL / PostgreSQL")["needs_database"] is True


def test_intent_extraction_can_override_gpu_keyword_hint(monkeypatch) -> None:
    captured_prompt = ""

    async def fake_create_chat_completion(payload):
        nonlocal captured_prompt
        captured_prompt = payload["messages"][0]["content"]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "goal_summary": "使用者已取消 GPU 需求，改以一般服務部署為主。",
                                "role": "student",
                                "course_context": "coursework",
                                "budget_mode": "balanced",
                                "needs_public_web": False,
                                "needs_database": False,
                                "requires_gpu": False,
                                "needs_windows": False,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        recommendation_service,
        "settings",
        SimpleNamespace(VLLM_MODEL_NAME="test-model", VLLM_ENABLE_THINKING=False),
    )
    monkeypatch.setattr(
        recommendation_service.client,
        "create_chat_completion",
        fake_create_chat_completion,
    )

    result = asyncio.run(
        recommendation_service.extract_intent_from_chat(
            ChatRequest(messages=[ChatMessage(role="user", content="不用 GPU 了")])
        )
    )

    assert "- Requires GPU: True" in captured_prompt
    assert "you MUST output false" in captured_prompt
    assert result.requires_gpu is False
