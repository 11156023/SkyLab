"""Coverage for app.ai.teacher_judge.check_plan_contract edge branches."""

from __future__ import annotations

import copy
from typing import Any

from app.ai.teacher_judge.check_plan_contract import (
    analysis_write_issues,
    reconcile_finalizer_locations,
    typed_step_issues,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
)


def _version_item(item_id: str = "item-1") -> dict[str, Any]:
    return {
        "id": item_id,
        "title": "Python版本檢查",
        "detectable": "auto",
        "judgement_mode": "ai",
        "detection_method": "Python 版本包含 3.12",
        "target_node_key": "web",
        "check_steps": [
            {
                "id": "python.version",
                "title": "Python 版本",
                "collector": {"type": "command", "argv": ["python3", "--version"]},
                "assertion": {"type": "text_contains", "expected": "3.12"},
            }
        ],
    }


def test_typed_step_issues_rejects_non_list() -> None:
    issues = typed_step_issues({"id": "x"}, item_id="i")
    assert issues == [
        {"item_id": "i", "field": "check_steps", "message": "check_steps 必須是陣列"}
    ]


def test_reconcile_skips_new_steps_and_keeps_non_list_gaps() -> None:
    current = _version_item()
    candidate = copy.deepcopy(current)
    candidate["check_steps"][0]["id"] = "python.version.new"
    candidate["missing_information"] = "not-a-list"
    result, issues = reconcile_finalizer_locations(current, candidate)
    assert issues == []
    assert result["missing_information"] == "not-a-list"
    assert result["check_steps"][0]["id"] == "python.version.new"


def test_analysis_write_issues_duplicates_and_invalid_previous() -> None:
    analysis = TeacherJudgeRubricAnalysis.model_validate(
        {"items": [_version_item("a"), _version_item("a"), _version_item("b")]}
    )
    issues = analysis_write_issues(analysis, {"items": [{"broken": True}]})
    messages = [issue["message"] for issue in issues]
    assert "檢查項目 id 重複" in messages
    assert "同一 node 內 check step id 重複" in messages
