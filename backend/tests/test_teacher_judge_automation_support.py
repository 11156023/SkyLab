from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge.automation_support import (
    ensure_script_generation_supported,
    get_script_generation_blockers,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


def _command(command_key: str = "python.run_entrypoint") -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="python",
        command_key=command_key,
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="argv + cwd + timeout",
        description="受控執行並收集客觀證據",
        risk_level="executes_code",
        requires_confirmation=True,
    )


def _item(*, detectable: str = "auto", parameters: dict | None = None) -> TeacherJudgeRubricItem:
    return TeacherJudgeRubricItem(
        id="item-1",
        title="main.py 執行結果",
        description="執行 main.py，確認無錯誤並輸出整數 20。",
        detectable=detectable,
        detection_method="依 exit code 與 stdout 精確判定",
        check_steps=[
            TeacherJudgeRubricCheckStep(
                template_key="python",
                command_key="python.run_entrypoint",
                parameters=parameters or {},
            )
        ],
    )


def test_missing_python_working_directory_blocks_script_generation() -> None:
    item = _item(
        parameters={
            "argv": ["python3", "main.py"],
            "timeout_seconds": 30,
            "success_criteria": "exit code 為 0 且 stdout 等於 20",
        }
    )
    analysis = TeacherJudgeRubricAnalysis(items=[item])

    blockers = get_script_generation_blockers(analysis, [_command()])

    assert blockers[0]["status"] == "missing_info"
    assert blockers[0]["missing_information"] == ["main.py 所在的工作目錄"]


def test_empty_rubric_blocks_script_generation() -> None:
    blockers = get_script_generation_blockers(TeacherJudgeRubricAnalysis(), [])

    assert blockers[0]["reason_code"] == "automatic_detection_items_missing"


def test_manual_item_blocks_the_whole_script() -> None:
    analysis = TeacherJudgeRubricAnalysis(items=[_item(detectable="manual")])

    with pytest.raises(HTTPException) as exc_info:
        ensure_script_generation_supported(analysis, [_command()])

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "teacher_judge_script_not_ready"
    assert exc_info.value.detail["items"][0]["status"] == "manual"


def test_all_items_with_complete_supported_steps_allow_script_generation() -> None:
    item = _item(
        parameters={
            "cwd": "/home/student/project",
            "argv": ["python3", "main.py"],
            "timeout_seconds": 30,
            "success_criteria": "exit code 為 0 且 stdout 等於 20",
        }
    )
    analysis = TeacherJudgeRubricAnalysis(items=[item])

    ensure_script_generation_supported(analysis, [_command()])


def test_generic_command_timeout_is_platform_owned_not_teacher_missing_info() -> None:
    item = TeacherJudgeRubricItem(
        id="item-1",
        title="讀取環境設定",
        description="在指定工作目錄讀取 .env。",
        detectable="auto",
        detection_method="以 exit code 判定檔案是否可讀",
        check_steps=[
            TeacherJudgeRubricCheckStep(
                template_key="python",
                command_key="system.run_command",
                parameters={
                    "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                    "argv": ["cat", ".env"],
                    "success_criteria": "exit code 為 0",
                },
            )
        ],
    )

    assert get_script_generation_blockers(
        TeacherJudgeRubricAnalysis(items=[item]),
        [_command("system.run_command")],
    ) == []


def test_generic_command_reports_target_instead_of_internal_argv_or_timeout() -> None:
    item = TeacherJudgeRubricItem(
        id="item-1",
        title="讀取資料",
        description="讀取尚未指定的資料。",
        detectable="auto",
        detection_method="以 exit code 判定",
        check_steps=[
            TeacherJudgeRubricCheckStep(
                template_key="python",
                command_key="system.run_command",
                parameters={"success_criteria": "exit code 為 0"},
            )
        ],
    )

    blockers = get_script_generation_blockers(
        TeacherJudgeRubricAnalysis(items=[item]),
        [_command("system.run_command")],
    )

    assert blockers[0]["missing_information"] == [
        "要檢查的檔案、服務或記錄範圍"
    ]


def test_stale_automation_support_blocks_script_generation() -> None:
    analysis = TeacherJudgeRubricAnalysis(
        items=[
            _item(
                parameters={
                    "cwd": "/home/student/project",
                    "argv": ["python3", "main.py"],
                    "timeout_seconds": 30,
                    "success_criteria": "exit code 為 0 且 stdout 等於 20",
                }
            )
        ],
        detectability_needs_review=True,
    )

    blockers = get_script_generation_blockers(analysis, [_command()])

    assert blockers[0]["reason_code"] == "automation_support_needs_review"
