from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.ai.template_recommendation import recommendation_service
from app.ai.template_recommendation.recommendation_service import (
    _extract_user_signal_flags,
    infer_intent_from_chat,
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


def test_fast_intent_seed_keeps_recent_conversation_and_flags() -> None:
    result = infer_intent_from_chat(
        ChatRequest(
            messages=[
                ChatMessage(role="user", content="我要做課程網站"),
                ChatMessage(role="assistant", content="是否需要 GPU？"),
                ChatMessage(role="user", content="要用 PyTorch CUDA 做推論"),
            ]
        )
    )

    assert "課程網站" in result.goal_summary
    assert "PyTorch CUDA" in result.goal_summary
    assert result.requires_gpu is True


def test_fast_intent_seed_honors_latest_gpu_negation() -> None:
    result = infer_intent_from_chat(
        ChatRequest(
            messages=[
                ChatMessage(role="user", content="原本要用 GPU 訓練"),
                ChatMessage(role="user", content="後來改成不用 GPU"),
            ]
        )
    )

    assert result.requires_gpu is False
