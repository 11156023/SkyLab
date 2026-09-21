"""Contracts for the typed Check Plan and deterministic script compiler."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ai.teacher_judge.deterministic_compiler import (
    CHECK_PLAN_SCHEMA_VERSION,
    DETERMINISTIC_COMPILER_VERSION,
    CheckPlanContractError,
    canonicalize_check_plan,
    compile_check_plan,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.ai.teacher_judge.script_policy import check_peer_runtime_policy


def _analysis(
    collector: dict[str, object],
    assertion: dict[str, object] | None,
    *,
    judgement_mode: str = "ai",
    peer_node_key: str | None = None,
) -> TeacherJudgeRubricAnalysis:
    return TeacherJudgeRubricAnalysis(
        items=[
            TeacherJudgeRubricItem(
                id="item-1",
                title="check item",
                detectable="auto",
                judgement_mode=judgement_mode,  # type: ignore[arg-type]
                detection_method="typed collector",
                target_node_key="web",
                peer_node_key=peer_node_key,
                check_steps=[
                    TeacherJudgeRubricCheckStep(
                        id="check-1",
                        title="check step",
                        collector=collector,
                        assertion=assertion,
                    )
                ],
            )
        ]
    )


def test_command_plan_compiles_deterministically_and_has_stable_contract() -> None:
    analysis = _analysis(
        {
            "type": "command",
            "argv": ["python3", "main.py"],
            "cwd": "/srv/student",
            "timeout_seconds": 30,
        },
        {"type": "returncode_equals", "expected": 0},
    )

    first = compile_check_plan(analysis, target_node_key="web")
    second = compile_check_plan(analysis, target_node_key="web")

    assert first[0] == second[0]
    assert first[3]["schema_version"] == CHECK_PLAN_SCHEMA_VERSION
    assert first[3]["compiler_version"] == DETERMINISTIC_COMPILER_VERSION
    assert first[1]["approved"] is True
    assert first[1]["quality_approved"] is True
    assert first[2]["mode"] == "deterministic_compiler"
    assert "vllm" not in first[0].lower()


@pytest.mark.parametrize(
    ("collector", "assertion", "judgement_mode", "peer_node_key"),
    [
        (
            {
                "type": "file_text",
                "path": "/var/log/app.log",
                "encoding": "utf-8",
                "read_mode": "tail",
                "lines": 20,
                "max_chars": 4000,
            },
            None,
            "teacher",
            None,
        ),
        (
            {"type": "file_stat", "path": "/tmp/ready.marker"},
            {"type": "exists", "expected": True},
            "ai",
            None,
        ),
        (
            {
                "type": "localhost_http",
                "method": "GET",
                "url": "http://127.0.0.1:8000/health",
                "timeout_seconds": 5,
                "max_chars": 2000,
            },
            {"type": "text_contains", "expected": "healthy"},
            "ai",
            None,
        ),
        (
            {"type": "peer_ping", "timeout_seconds": 5},
            {"type": "returncode_equals", "expected": 0},
            "ai",
            "db",
        ),
    ],
)
def test_each_v1_collector_compiles(
    collector: dict[str, object],
    assertion: dict[str, object] | None,
    judgement_mode: str,
    peer_node_key: str | None,
) -> None:
    script, policy, review, _ = compile_check_plan(
        _analysis(
            collector,
            assertion,
            judgement_mode=judgement_mode,
            peer_node_key=peer_node_key,
        ),
        target_node_key="web",
    )

    assert policy["approved"] is True
    assert review["approved"] is True
    if peer_node_key:
        peer_policy = check_peer_runtime_policy(
            script,
            {
                "items": [
                    {
                        "id": "item-1",
                        "target_node_key": "web",
                        "peer_node_key": peer_node_key,
                        "detectable": "auto",
                        "check_steps": [
                            {
                                "collector": {
                                    "type": "peer_ping",
                                }
                            }
                        ],
                    }
                ]
            },
        )
        assert peer_policy["approved"] is True


def test_command_plan_without_cwd_renders_python_none_and_executes(
    tmp_path: Path,
) -> None:
    """cwd 是 optional；缺漏時必須渲染成 Python None，而不是 JSON null。"""

    analysis = _analysis(
        {
            "type": "command",
            "argv": [sys.executable, "--version"],
            "timeout_seconds": 30,
        },
        {"type": "returncode_equals", "expected": 0},
    )

    script, policy, review, _ = compile_check_plan(analysis, target_node_key="web")

    assert "run_command(argv, None, 30)" in script
    assert policy["approved"] is True
    assert review["approved"] is True

    script_path = tmp_path / "compiled_script.py"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    check = payload["checks"][0]
    assert check["status"] == "pass"
    assert "collection exception" not in check["evidence"]


def _execute_compiled_script(script: str, tmp_path: Path) -> dict[str, object]:
    script_path = tmp_path / "compiled_script.py"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0
    return json.loads(completed.stdout)


def test_teacher_command_nonzero_is_reported_as_command_failure(tmp_path: Path) -> None:
    failing_script = tmp_path / "fail.py"
    failing_script.write_text(
        "import sys\nsys.stderr.write('missing file\\n')\nsys.exit(1)\n",
        encoding="utf-8",
    )
    analysis = _analysis(
        {
            "type": "command",
            "argv": [sys.executable, str(failing_script)],
            "timeout_seconds": 30,
        },
        None,
        judgement_mode="teacher",
    )

    script, _, _, _ = compile_check_plan(analysis, target_node_key="web")
    payload = _execute_compiled_script(script, tmp_path)
    check = payload["checks"][0]
    raw = json.loads(check["raw"])

    assert check["status"] == "unknown"
    assert "returncode 1" in check["evidence"]
    assert raw["error_code"] == "command_failed"
    assert raw["argv"] == [sys.executable, str(failing_script)]
    assert raw["cwd"] is None
    assert raw["timeout_seconds"] == 30
    assert raw["stderr"] == "missing file\n"
    assert payload["errors"] == ["check-1: command_failed"]


def test_missing_command_cwd_has_actionable_error_and_keeps_raw_log(tmp_path: Path) -> None:
    missing_cwd = tmp_path / "missing-project"
    analysis = _analysis(
        {
            "type": "command",
            "argv": [sys.executable, "main.py"],
            "cwd": str(missing_cwd),
            "timeout_seconds": 30,
        },
        None,
        judgement_mode="teacher",
    )

    script, _, _, _ = compile_check_plan(analysis, target_node_key="web")
    payload = _execute_compiled_script(script, tmp_path)
    check = payload["checks"][0]
    raw = json.loads(check["raw"])

    assert check["status"] == "unknown"
    assert check["evidence"] == f"工作目錄不存在：{missing_cwd}"
    assert raw["error_code"] == "working_directory_not_found"
    assert raw["argv"] == [sys.executable, "main.py"]
    assert raw["cwd"] == str(missing_cwd)
    assert raw["returncode"] is None
    assert raw["stderr"]


def test_returncode_assertion_failure_has_teacher_facing_summary(tmp_path: Path) -> None:
    failing_script = tmp_path / "fail.py"
    failing_script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    analysis = _analysis(
        {
            "type": "command",
            "argv": [sys.executable, str(failing_script)],
            "timeout_seconds": 30,
        },
        {"type": "returncode_equals", "expected": 0},
    )

    script, _, _, _ = compile_check_plan(analysis, target_node_key="web")
    payload = _execute_compiled_script(script, tmp_path)
    check = payload["checks"][0]
    raw = json.loads(check["raw"])

    assert check["status"] == "fail"
    assert check["evidence"] == "指令檢查未通過：預期 returncode 0，實際為 1"
    assert raw["error_code"] == "unexpected_returncode"
    assert raw["argv"] == [sys.executable, str(failing_script)]
    assert raw["returncode"] == 1


def test_compiler_rejects_legacy_or_inconsistent_steps() -> None:
    legacy = TeacherJudgeRubricAnalysis(
        items=[
            TeacherJudgeRubricItem(
                id="legacy",
                title="legacy",
                detectable="auto",
                detection_method="legacy",
                target_node_key="web",
                check_steps=[TeacherJudgeRubricCheckStep(argv=["true"])],
            )
        ]
    )
    with pytest.raises(CheckPlanContractError, match="flat legacy"):
        canonicalize_check_plan(legacy, target_node_key="web")

    with pytest.raises(CheckPlanContractError, match="必須提供 assertion"):
        canonicalize_check_plan(
            _analysis(
                {"type": "file_stat", "path": "/tmp/ready.marker"},
                None,
            ),
            target_node_key="web",
        )

    with pytest.raises(CheckPlanContractError, match="不可帶 assertion"):
        canonicalize_check_plan(
            _analysis(
                {"type": "file_text", "path": "/var/log/app.log"},
                {"type": "text_contains", "expected": "ready"},
                judgement_mode="teacher",
            ),
            target_node_key="web",
        )

    with pytest.raises(CheckPlanContractError, match="peer_ping 必須指定"):
        canonicalize_check_plan(
            _analysis(
                {"type": "peer_ping", "timeout_seconds": 5},
                {"type": "returncode_equals", "expected": 0},
            ),
            target_node_key="web",
        )

    with pytest.raises(CheckPlanContractError, match="file_stat collector 不支援 text_contains"):
        canonicalize_check_plan(
            _analysis(
                {"type": "file_stat", "path": "/tmp/ready.marker"},
                {"type": "text_contains", "expected": "ready"},
            ),
            target_node_key="web",
        )


def test_typed_step_cannot_mix_flat_fields() -> None:
    with pytest.raises(ValueError, match="cannot include legacy"):
        TeacherJudgeRubricCheckStep(
            id="mixed",
            title="mixed step",
            collector={"type": "file_stat", "path": "/tmp/ready.marker"},
            argv=["true"],
        )


def test_step_ids_are_unique_within_a_node_plan() -> None:
    analysis = _analysis(
        {"type": "file_stat", "path": "/tmp/ready.marker"},
        {"type": "exists", "expected": True},
    )
    second = analysis.items[0].model_copy(deep=True)
    second.id = "item-2"
    second.title = "second item"
    second.check_steps[0].id = "check-1"
    analysis.items.append(second)

    with pytest.raises(CheckPlanContractError, match="check step id 重複"):
        canonicalize_check_plan(analysis, target_node_key="web")


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "/tmp/student"],
        ["python3", "-c", "__import__('os').remove('/tmp/student')"],
        ["git", "reset", "--hard"],
    ],
)
def test_command_collector_rejects_dynamic_mutation_or_eval(argv: list[str]) -> None:
    with pytest.raises(CheckPlanContractError, match="禁止|唯讀|eval"):
        canonicalize_check_plan(
            _analysis(
                {"type": "command", "argv": argv, "timeout_seconds": 10},
                {"type": "returncode_equals", "expected": 0},
            ),
            target_node_key="web",
        )
