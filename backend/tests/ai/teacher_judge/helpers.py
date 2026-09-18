"""Shared fixtures for teacher-judge tests (extracted from oversized modules)."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import service as teacher_judge_service
from app.models.teacher_judge_file import TeacherJudgeFile


def patch_teacher_judge_vllm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teacher_judge_service,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=60,
            VLLM_MAX_TOKENS=4096,
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_CHAT_TEMPERATURE=0.2,
            VLLM_TOP_P=1.0,
            VLLM_TOP_K=20,
            VLLM_REPETITION_PENALTY=1.0,
            VLLM_CHAT_MAX_TOOL_ROUNDS=6,
        ),
    )


def tool_call_message(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Assistant message that invokes one checklist proposal tool."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }


def reply_message(
    reply: str, status: str, focus: dict[str, object] | None = None
) -> str:
    """Final assistant JSON reply without any tool calls."""
    payload: dict[str, object] = {"reply": reply, "proposal_status": status}
    if focus is not None:
        payload["conversation_focus"] = focus
    return json.dumps(payload, ensure_ascii=False)


def requirement_focus(status: str, *, key: str, title: str) -> dict[str, object]:
    return {
        "turn_kind": "requirement",
        "requirements": [
            {
                "focus_key": key,
                "status": status,
                "known_information": [title],
                "missing_information": [],
                "target_item_id": None,
            }
        ],
    }


def scripted_vllm(responses: list[object]):
    """Build a fake `_call_vllm_message`; steps may carry (response, metrics)."""
    calls: list[dict[str, object]] = []

    async def fake_call(payload, timeout=60.0):
        calls.append(payload)
        step = responses.pop(0)
        if isinstance(step, tuple):
            return step
        return step, {}

    return calls, fake_call


def make_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def make_teacher_judge_file(db: Session, class_id: uuid.UUID) -> TeacherJudgeFile:
    item = TeacherJudgeFile(
        teaching_class_id=class_id,
        original_filename="rubric.pdf",
        file_hash="a" * 64,
        template_key="linux",
        analysis_json={"items": [], "summary": "rubric"},
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
