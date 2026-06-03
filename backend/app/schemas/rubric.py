"""Backward-compatible rubric schema exports."""

from __future__ import annotations

from app.ai.teacher_judge.schemas import (
    ChatMessage,
    RubricAnalysis,
    RubricChatRequest,
    RubricChatResponse,
    RubricCheckStep,
    RubricExportRequest,
    RubricItem,
    RubricUploadResponse,
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricChatMessage,
    TeacherJudgeRubricChatRequest,
    TeacherJudgeRubricChatResponse,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricExportRequest,
    TeacherJudgeRubricItem,
    TeacherJudgeRubricUploadResponse,
)

__all__ = [
    "ChatMessage",
    "RubricAnalysis",
    "RubricChatRequest",
    "RubricChatResponse",
    "RubricCheckStep",
    "RubricExportRequest",
    "RubricItem",
    "RubricUploadResponse",
    "TeacherJudgeRubricAnalysis",
    "TeacherJudgeRubricChatMessage",
    "TeacherJudgeRubricChatRequest",
    "TeacherJudgeRubricChatResponse",
    "TeacherJudgeRubricCheckStep",
    "TeacherJudgeRubricExportRequest",
    "TeacherJudgeRubricItem",
    "TeacherJudgeRubricUploadResponse",
]
