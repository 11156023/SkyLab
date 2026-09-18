"""Split from tests/test_teacher_judge_script_artifacts.py: managed-script security policy.

Shared fixtures live in tests.ai.teacher_judge.helpers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from textwrap import dedent
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401
from app.ai.teacher_judge import (
    automation_support,
    script_artifact_service,
    script_executor_service,
    script_run_service,
    target_ip_resolver,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricItem,
)
from app.ai.teacher_judge.script_policy import (
    check_script_policy,
    validate_managed_script_output,
)
from app.ai.teacher_judge.template_command_service import GENERAL_COMMAND
from app.api.routes.teacher_judge_scripts import _normalize_supported_template_key
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptStatus
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.repositories import resource as resource_repo
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    requirement_focus,
    scripted_vllm,
    tool_call_message,
)

SAFE_SCRIPT = """
import json
import platform
from datetime import datetime, timezone

print(json.dumps({
    "schema_version": "teacher_judge_result.v1",
    "metadata": {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
    },
    "summary": "ok",
    "checks": [],
    "errors": [],
}, ensure_ascii=False))
""".strip()


@pytest.mark.asyncio
async def test_generate_script_content_sends_commands_feedback_and_safety_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (json.dumps({"script_content": SAFE_SCRIPT}), {"total_tokens": 1})

    monkeypatch.setattr(script_artifact_service, "_call_vllm", fake_call_vllm)
    monkeypatch.setattr(
        script_artifact_service,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_TOP_P=1.0,
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=60,
        ),
    )

    await script_artifact_service.generate_script_content(
        rubric_snapshot={
            "template_key": "n8n",
            "template_commands": [
                {
                    "command_key": "n8n.port_check",
                    "command_template": "ss -lntp | grep ':5678'",
                }
            ],
            "previous_review_feedback": {
                "policy_issues": ["禁止使用 shell=True 執行指令"],
                "quality_issues": ["工具缺失時應回傳 unknown，不可使用 warning"],
                "available_check_ids": ["service.n8n_port"],
            },
        },
        template_key="n8n",
    )

    system_prompt = captured_payload["messages"][0]["content"]
    user_payload = json.loads(captured_payload["messages"][1]["content"])
    assert "subprocess.run([...]" in system_prompt
    assert "shell=True" in system_prompt
    assert "record_check" in system_prompt
    assert "run_command" in system_prompt
    assert "ensure_ascii=False" in system_prompt
    assert "metadata" in system_prompt
    assert "truncate_output" in system_prompt
    assert "quality validator" in system_prompt
    assert "簡潔程式碼骨架" in system_prompt
    assert "run_command()" in system_prompt
    assert "只負責" in system_prompt
    assert '{"stdout": "", "stderr": str(exc), "returncode": None}' in system_prompt
    assert "不要在 helper 內操作 `errors` 或 `checks`" in system_prompt
    assert "不要建立 class" in system_prompt
    assert "python.run_entrypoint" in system_prompt
    assert "不得搜尋檔案系統或猜路徑" in system_prompt
    assert "exit code、stdout、stderr" in system_prompt
    assert "熟悉 Linux、Windows 系統管理與常見 CLI 工具" in system_prompt
    assert "外部指令只用於取得 rubric 所需的唯讀診斷資訊" in system_prompt
    assert "只收集足以回答問題的資訊" in system_prompt
    assert "不要只複製 raw 輸出" in system_prompt
    assert '["cat", "<相對檔名或路徑>"]' not in system_prompt
    assert '["systemctl", "--failed"]' not in system_prompt
    assert '["journalctl", "--since", "1 hour ago"' not in system_prompt
    assert "只有明確要求完全相等時才比較整份 stdout" in system_prompt
    assert "設定行如 `web_URL=True`" in system_prompt
    assert "不得要求整份輸出只有該字串" in system_prompt
    assert user_payload["template_commands"][0]["command_key"] == "n8n.port_check"
    assert user_payload["previous_review_feedback"]["policy_issues"] == [
        "禁止使用 shell=True 執行指令"
    ]
    assert user_payload["previous_review_feedback"]["quality_issues"] == [
        "工具缺失時應回傳 unknown，不可使用 warning"
    ]
    assert user_payload["previous_review_feedback"]["available_check_ids"] == [
        "service.n8n_port"
    ]


@pytest.mark.asyncio
async def test_fix_script_content_applies_line_replacements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}
    source = "\n".join(
        [
            "errors: list[str] = []",
            "try:",
            "    collect()",
            "except Exception:",
            "    pass",
        ]
    )

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "line_replacements": [
                        {
                            "start_line": 4,
                            "end_line": 5,
                            "replacement": "\n".join(
                                [
                                    "except Exception as exc:",
                                    '    errors.append(f"runtime.python_version: 未預期錯誤: {str(exc)[:200]}")',
                                ]
                            ),
                        }
                    ],
                    "changes_summary": "補上 errors.append。",
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(script_artifact_service, "_call_vllm", fake_call_vllm)
    monkeypatch.setattr(
        script_artifact_service,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_TOP_P=1.0,
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=60,
        ),
    )

    fixed, _metrics = await script_artifact_service.fix_script_content(
        script_content=source,
        fix_hints=[
            {
                "type": "add_errors_append_in_except",
                "lineno": 4,
                "end_lineno": 5,
                "snippet": "0004|except Exception:\n0005|    pass",
                "required_pattern": "except Exception as exc:\n    errors.append(...)",
            }
        ],
    )

    user_payload = json.loads(captured_payload["messages"][1]["content"])
    repair_system_prompt = captured_payload["messages"][0]["content"]
    repair_instruction = user_payload["repair_instructions"][0]
    assert repair_instruction["line_range"] == [4, 5]
    assert repair_instruction["snippet"] == "0004|except Exception:\n0005|    pass"
    assert "errors.append" in repair_instruction["required_pattern"]
    assert "fix_instructions" in user_payload
    assert (
        '{"stdout": "", "stderr": str(exc), "returncode": None}' in repair_system_prompt
    )
    assert "target 是 `run_command_exception_handler`" in repair_system_prompt
    assert "except Exception as exc:" in fixed
    assert "errors.append" in fixed
    assert "    collect()" in fixed


def test_truncate_repair_instruction_targets_record_check_definition() -> None:
    instructions = script_artifact_service._repair_instructions(
        [
            {
                "type": "add_truncate_in_record_check",
                "function": "record_check",
                "field": "raw",
                "target": "record_check_definition",
                "lineno": 3,
                "end_lineno": 8,
                "snippet": "0003|def record_check(...):",
                "required_pattern": (
                    "raw_text = raw if isinstance(raw, str) else json.dumps(raw, "
                    'ensure_ascii=False, default=str); "raw": truncate_output(raw_text)'
                ),
                "description": "raw 必須在 helper 定義內截斷",
            }
        ]
    )

    assert instructions == [
        {
            "issue": "raw 必須在 helper 定義內截斷",
            "fix_goal": '只修改 record_check 函式定義；將非字串 raw payload 先序列化，再將回傳物件的 "raw" 欄位交給一次 truncate_output(raw_text)，呼叫端保持傳入原始 raw，不要只修改呼叫端。',
            "target": "record_check_definition",
            "line_range": [3, 8],
            "snippet": "0003|def record_check(...):",
            "required_pattern": (
                "raw_text = raw if isinstance(raw, str) else json.dumps(raw, "
                'ensure_ascii=False, default=str); "raw": truncate_output(raw_text)'
            ),
        }
    ]


def test_run_command_repair_instruction_preserves_helper_boundary() -> None:
    instructions = script_artifact_service._repair_instructions(
        [
            {
                "type": "normalize_run_command_error_contract",
                "function": "run_command",
                "target": "run_command_exception_handler",
                "lineno": 48,
                "end_lineno": 49,
                "snippet": "0048|except Exception as exc:\n0049|    raise Exception(str(exc))",
                "required_pattern": (
                    "except Exception as exc:\n"
                    '    return {"stdout": "", "stderr": str(exc), "returncode": None}'
                ),
                "description": "run_command 例外應回傳結構化錯誤",
            }
        ]
    )

    assert instructions == [
        {
            "issue": "run_command 例外應回傳結構化錯誤",
            "fix_goal": (
                "只替換 run_command 指定 except 區塊；回傳 "
                '{"stdout": "", "stderr": str(exc), "returncode": None}，'
                "不要在 helper 內操作 errors 或 checks，由呼叫端處理 returncode=None。"
            ),
            "target": "run_command_exception_handler",
            "line_range": [48, 49],
            "snippet": "0048|except Exception as exc:\n0049|    raise Exception(str(exc))",
            "required_pattern": (
                "except Exception as exc:\n"
                '    return {"stdout": "", "stderr": str(exc), "returncode": None}'
            ),
        }
    ]


def test_previous_review_feedback_keeps_coverage_repair_guidance() -> None:
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(),
        name="coverage-failed.pdf",
        template_key="linux",
        script_content="print('candidate')",
        policy_check_result_json={
            "safety_approved": True,
            "safety_issues": [],
            "quality_approved": True,
            "quality_issues": [],
            "coverage": {
                "approved": False,
                "issues": ["coverage 引用不存在的 check id：model.id"],
                "uncovered_items": [],
                "available_check_ids": ["runtime.python_version"],
            },
            "review_attempts": [
                {
                    "phase": "coverage",
                    "fix_hints": [
                        {
                            "type": "fix_coverage_refs",
                            "description": "coverage check_id 必須使用實際 ID",
                        }
                    ],
                }
            ],
        },
        ai_review_result_json={
            "approved": True,
            "issues": [],
            "suggested_fix": None,
        },
    )

    feedback = script_artifact_service._previous_review_feedback(artifact)

    assert feedback is not None
    assert feedback["available_check_ids"] == ["runtime.python_version"]
    assert feedback["repair_guidance"][0]["target"] == "fix_coverage_refs"


def test_script_route_rejects_unknown_template_key() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _normalize_supported_template_key("unknown")

    assert exc_info.value.status_code == 400


def test_script_policy_blocks_destructive_commands() -> None:
    result = check_script_policy(
        "import subprocess\nsubprocess.run('rm -rf /', shell=True)"
    )

    assert result["approved"] is False
    assert result["blocked"] is True
    assert any("rm -rf" in issue for issue in result["issues"])


def test_script_policy_blocks_subprocess_from_import_alias() -> None:
    result = check_script_policy(
        """
import json
from subprocess import run as sprun

sprun(["rm", "-rf", "/tmp/campus-cloud-judge"], timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "checks": [], "errors": []}))
""".strip()
    )

    assert result["approved"] is False
    assert any("rm" in issue for issue in result["issues"])


def test_script_policy_blocks_subprocess_module_alias_shell_true() -> None:
    result = check_script_policy(
        """
import json
import subprocess as sp

sp.run("echo hi", shell=True, timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is False
    assert any("shell=True" in issue for issue in result["issues"])


def test_script_policy_blocks_destructive_subprocess_argv() -> None:
    result = check_script_policy(
        """
import json
import subprocess

subprocess.run(["rm", "-rf", "/tmp/campus-cloud-judge"], timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "checks": [], "errors": []}))
""".strip()
    )

    assert result["approved"] is False
    assert any("rm" in issue for issue in result["issues"])


def test_script_policy_requires_timeout_for_subprocess_run() -> None:
    result = check_script_policy(
        "import subprocess\nsubprocess.run(['python3', '--version'])"
    )

    assert result["approved"] is False
    assert any("timeout" in issue for issue in result["issues"])


def test_script_policy_blocks_file_writes() -> None:
    result = check_script_policy(
        """
import json
from pathlib import Path

Path("/tmp/result.txt").write_text("changed")
print(json.dumps({"schema_version": "teacher_judge_result.v1", "checks": [], "errors": []}))
""".strip()
    )

    assert result["approved"] is False
    assert any("寫入檔案" in issue for issue in result["issues"])


def test_script_policy_allows_sensitive_redaction_patterns() -> None:
    result = check_script_policy(
        """
import json
import re

def redact_sensitive_text(text: str) -> str:
    patterns = [
        (
            r"(?i)(password|passwd|secret|token|api_key|bearer|auth_token|access_token|private_key|ssh-rsa|id_rsa)\\s*[:=]\\s*[^\\s]+",
            r"\\1: [REDACTED]",
        ),
        (r"([a-f0-9]{32,})", "[REDACTED_HASH]"),
        (r"(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})", r"\\1"),
    ]
    redacted = text
    for pattern, replacement in patterns:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted

print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


def test_script_policy_allows_reading_env_file_without_masking() -> None:
    result = check_script_policy(
        """
import json

with open("/srv/student/project/.env", "r", encoding="utf-8") as env_file:
    raw = env_file.read()

print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


def test_script_policy_allows_cat_env_with_timeout() -> None:
    result = check_script_policy(
        """
import json
import subprocess

subprocess.run(["cat", "/srv/student/project/.env"], timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


@pytest.mark.parametrize(
    "argv",
    [
        '["bash", "-c", "echo hello"]',
        '["bash", "-lc", "history"]',
        '["sh", "-c", "cat .env"]',
        '["git", "commit", "-m", "change"]',
    ],
)
def test_script_policy_blocks_shell_launchers_and_writing_git(argv: str) -> None:
    result = check_script_policy(
        f"""\nimport json\nimport subprocess\n\nsubprocess.run({argv}, timeout=5)\nprint(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))\n""".strip()
    )

    assert result["approved"] is False


@pytest.mark.parametrize(
    "argv",
    [
        '["echo", "hello"]',
        '["git", "status", "--short"]',
        '["ping", "-c", "1", "127.0.0.1"]',
        '["systemctl", "list-units", "--type=service", "--all"]',
        '["systemctl", "--failed"]',
        '["journalctl", "--since", "1 hour ago", "-p", "err", "--no-pager", "-n", "50"]',
    ],
)
def test_script_policy_allows_generic_read_only_commands(argv: str) -> None:
    result = check_script_policy(
        f"""\nimport json\nimport subprocess\n\nsubprocess.run({argv}, cwd="/srv/student/project", timeout=5)\nprint(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))\n""".strip()
    )

    assert result["approved"] is True


def test_script_policy_blocks_external_network_requests() -> None:
    result = check_script_policy(
        """
import json
import requests

requests.get("https://example.com/collect", timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "checks": [], "errors": []}))
""".strip()
    )

    assert result["approved"] is False
    assert any("localhost" in issue for issue in result["issues"])


def test_script_policy_allows_localhost_get_with_timeout() -> None:
    result = check_script_policy(
        """
import json
import requests

requests.get("http://127.0.0.1:5678/health", timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is True


def test_validate_managed_script_output_contract() -> None:
    valid = validate_managed_script_output(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "now", "platform": "test"},
            "summary": "ok",
            "checks": [
                {
                    "id": "service",
                    "title": "Service check",
                    "status": "pass",
                    "evidence": "running",
                    "raw": "",
                }
            ],
            "errors": [],
        }
    )
    invalid = validate_managed_script_output(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "now", "platform": "test"},
            "checks": [{"id": "service", "title": "Service check", "status": "done"}],
            "errors": [],
        }
    )

    assert valid["valid"] is True
    assert valid["checks_count"] == 1
    assert invalid["valid"] is False
