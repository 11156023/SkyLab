"""Coverage for app.ai.teacher_judge.automation_support readiness branches."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.ai.teacher_judge import automation_support as svc
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)


def _typed_step(collector: dict[str, Any]) -> Any:
    return SimpleNamespace(
        collector=SimpleNamespace(model_dump=lambda mode="json": dict(collector))
    )


def test_missing_step_information_typed_collectors() -> None:
    command = svc.missing_step_information(
        _typed_step({"type": "command", "argv": [], "timeout_seconds": 0})
    )
    assert "collector.argv 必須是非空的字串陣列" in command
    assert "collector.timeout_seconds 必須是 1-300 的整數" in command

    file_text = svc.missing_step_information(
        _typed_step({"type": "file_text", "path": "  "})
    )
    assert "collector.path" in file_text

    http = svc.missing_step_information(_typed_step({"type": "localhost_http"}))
    assert http == ["collector.url"]

    ping = svc.missing_step_information(
        _typed_step({"type": "peer_ping", "timeout_seconds": True})
    )
    assert ping == ["collector.timeout_seconds 必須是 1-60 的整數"]


def test_item_missing_information_gaps() -> None:
    item = TeacherJudgeRubricItem(id="i1", title="t", detectable="auto")
    missing = svc._item_missing_information(
        item, valid_commands=set(), require_target_node=True
    )
    assert "檢測方式與結果解讀" in missing
    assert "平台支援的檢查步驟" in missing
    assert "target_node_key" in missing

    legacy = TeacherJudgeRubricItem(
        id="i2",
        title="t",
        detectable="auto",
        detection_method="x",
        target_node_key="web",
        check_steps=[
            TeacherJudgeRubricCheckStep(template_key="linux", command_key="gone")
        ],
    )
    missing_legacy = svc._item_missing_information(legacy, valid_commands=set())
    assert "有效的檢查能力：gone" in missing_legacy


def test_blockers_manual_teacher_and_partial_defaults() -> None:
    teacher_manual = TeacherJudgeRubricItem(
        id="m1",
        title="manual",
        detectable="manual",
        judgement_mode="teacher",
        check_steps=[TeacherJudgeRubricCheckStep(argv=["ls"])],
    )
    partial = TeacherJudgeRubricItem(id="p1", title="partial", detectable="partial")
    blockers = svc.get_script_generation_blockers(
        TeacherJudgeRubricAnalysis(items=[teacher_manual, partial]), []
    )
    assert blockers[0]["detail"].startswith(
        "目前的檢查方式尚未形成通過安全驗證的取證步驟"
    )
    assert blockers[1]["missing_information"] == [
        "完整的服務名稱、程式位置、連接埠或取證範圍"
    ]
