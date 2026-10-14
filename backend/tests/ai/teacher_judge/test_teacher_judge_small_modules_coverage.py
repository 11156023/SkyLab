"""Coverage for teacher_judge template_command_service / session_chat_service / config gaps."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import config as tj_config
from app.ai.teacher_judge import session_chat_service as chat
from app.ai.teacher_judge.template_command_service import (
    sanitize_check_step_parameters,
    validate_check_steps_with_issues,
)


def test_sanitize_check_step_parameters_passes_through_non_dict() -> None:
    assert sanitize_check_step_parameters(["argv"]) == ["argv"]
    assert sanitize_check_step_parameters(
        {"success_criteria": "x", "argv": ["ls"]}
    ) == {"argv": ["ls"]}


def test_validate_check_steps_rejects_non_dict_and_bad_argv() -> None:
    result = validate_check_steps_with_issues(
        "linux",
        [
            {
                "id": "i1",
                "check_steps": [
                    "not-a-step",
                    {"argv": []},
                    {"argv": ["ls", "  "]},
                    {"argv": ["ls"], "timeout_seconds": 5},
                    {"argv": ["pwd"], "cwd": " /srv "},
                ],
            }
        ],
        [],
    )
    codes = [issue.code for issue in result.issues]
    assert codes == ["invalid_step", "invalid_argv", "invalid_argv"]
    assert result.items[0]["check_steps"] == [
        {"argv": ["ls"], "timeout_seconds": 5},
        {"argv": ["pwd"], "timeout_seconds": 30, "cwd": "/srv"},
    ]


def test_proposal_machine_contract_issues_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat,
        "load_class_machine_nodes",
        lambda *_args: [SimpleNamespace(node_key="web")],
    )
    with pytest.raises(HTTPException) as exc:
        chat.validate_proposal_machine_nodes(
            None,  # type: ignore[arg-type]
            uuid.uuid4(),
            [
                {
                    "id": "a",
                    "detectable": "auto",
                    "target_node_key": "web",
                    "peer_node_key": "web",
                }
            ],
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "teacher_judge_machine_contract_invalid"
    assert "a" in exc.value.detail["items"]


def test_teacher_judge_settings_tool_rounds_default_and_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = SimpleNamespace(vllm=SimpleNamespace(chat_max_tool_rounds=None))
    monkeypatch.setattr(
        tj_config, "system_ai_config", SimpleNamespace(teacher_judge=section)
    )
    settings = tj_config.TeacherJudgeSettings()
    assert settings.VLLM_CHAT_MAX_TOOL_ROUNDS == 6
    section.vllm.chat_max_tool_rounds = 3
    assert settings.VLLM_CHAT_MAX_TOOL_ROUNDS == 3
