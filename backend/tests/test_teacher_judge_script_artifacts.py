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
    script_result_analysis_service,
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

SCRIPT_WITH_RECORD_CHECK = """
import json
import platform
from datetime import datetime, timezone

def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id, "title": title, "status": status, "evidence": evidence, "raw": raw}

checks = [record_check("runtime.python_version", "收集 Python 版本", "unknown", "n/a")]
print(json.dumps({
    "schema_version": "teacher_judge_result.v1",
    "metadata": {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
    },
    "summary": "ok",
    "checks": checks,
    "errors": [],
}, ensure_ascii=False))
""".strip()

SCRIPT_WITH_RENAMED_CHECK = SCRIPT_WITH_RECORD_CHECK.replace(
    "runtime.python_version", "new.check.id"
)


def _session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _analysis() -> TeacherJudgeRubricAnalysis:
    return TeacherJudgeRubricAnalysis(
        items=[
            TeacherJudgeRubricItem(
                id="item-1",
                title="n8n Web UI",
                checked=False,
                detectable="auto",
                detection_method="檢查 localhost 5678",
                fallback=None,
                check_steps=[
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["ss", "-lnt"],
                            "timeout_seconds": 10,
                            "success_criteria": "stdout 包含 5678 的 listening socket",
                        },
                    }
                ],
            )
        ],
        total_items=1,
        auto_count=1,
        summary="n8n rubric",
    )


@pytest.mark.asyncio
async def test_create_artifact_blocks_non_auto_item_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    model_called = False

    async def unexpected_build(**_kwargs):
        nonlocal model_called
        model_called = True
        raise AssertionError("blocked rubric must not call the model")

    monkeypatch.setattr(
        script_artifact_service, "_build_reviewed_script_for_artifact", unexpected_build
    )
    analysis = _analysis()
    analysis.items[0].detectable = "partial"
    analysis.items[0].missing_information = ["n8n 服務的實際 Port"]

    with pytest.raises(HTTPException) as exc_info:
        await script_artifact_service.create_artifact(
            session=session,
            teaching_class_id=uuid.uuid4(),
            name="blocked",
            template_key="linux",
            rubric_analysis=analysis,
            created_by=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "teacher_judge_script_not_ready"
    assert model_called is False


def test_teacher_judgement_item_is_script_ready_without_objective_answer() -> None:
    analysis = _analysis()
    item = analysis.items[0]
    item.judgement_mode = "teacher"
    item.check_steps[0].parameters.pop("success_criteria")

    assert automation_support.get_script_generation_blockers(
        analysis,
        [GENERAL_COMMAND],
    ) == []


def test_ai_judgement_item_is_script_ready_without_success_criteria() -> None:
    analysis = _analysis()
    analysis.items[0].check_steps[0].parameters.pop("success_criteria")

    assert automation_support.get_script_generation_blockers(
        analysis,
        [GENERAL_COMMAND],
    ) == []


def _resource(*, vmid: int, user_id: uuid.UUID) -> models.Resource:
    return models.Resource(
        vmid=vmid,
        user_id=user_id,
        environment_type="linux",
        ssh_private_key_encrypted="encrypted-key",
        created_at=datetime.now(timezone.utc),
    )


def _add_resource(
    session: Session, *, vmid: int, user_id: uuid.UUID, ip: str | None = "10.0.0.10"
) -> models.Resource:
    """Add a resource, plus its cached IP row when the test expects a cache hit."""
    resource = _resource(vmid=vmid, user_id=user_id)
    session.add(resource)
    if ip is not None:
        session.add(
            models.ResourceNetwork(
                resource_vmid=vmid,
                ip_address=ip,
                source="proxmox",
                cached_at=datetime.now(timezone.utc),
            )
        )
    return resource


def _valid_result_json() -> str:
    return json.dumps(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "platform": "linux",
            },
            "summary": "ok",
            "checks": [
                {
                    "id": "runtime.python",
                    "title": "Python runtime",
                    "status": "pass",
                    "evidence": "python3 exists",
                    "raw": "Python 3.11",
                }
            ],
            "errors": [],
        }
    )


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
async def test_create_artifact_auto_approves_passed_managed_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        assert rubric_snapshot["template_key"] == "n8n"
        assert template_key == "n8n"
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    artifact = await script_artifact_service.create_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="n8n",
        rubric_analysis=_analysis(),
        created_by=user_id,
    )

    assert artifact.status == "approved"
    assert artifact.script_language == "python"
    assert artifact.source == "ai_generated"
    assert artifact.rubric_snapshot_json["template_key"] == "n8n"
    assert artifact.approved_by is None
    assert artifact.approved_at is not None


@pytest.mark.asyncio
async def test_build_reviewed_script_retries_with_quality_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_snapshots: list[dict] = []

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        seen_snapshots.append(dict(rubric_snapshot))
        return "bad-script", [], {}

    review_call_count = [0]

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_call_count[0] += 1
        return {
            "approved": True,
            "risk_level": "low",
            "issues": [],
            "suggested_fix": None,
        }

    monkeypatch.setattr(
        script_artifact_service,
        "generate_script_content",
        fake_generate_script_content,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "review_script_with_ai",
        fake_review_script_with_ai,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": script_content != "bad-script",
            "blocked": script_content == "bad-script",
            "issues": ["工具缺失時應回傳 unknown，不可使用 warning"]
            if script_content == "bad-script"
            else [],
            "fix_hints": [
                {
                    "type": "fix_status_semantics",
                    "description": "工具缺失時應回傳 unknown，不可使用 warning",
                }
            ]
            if script_content == "bad-script"
            else [],
        },
    )

    async def fake_fix_script_content(*, script_content, fix_hints):
        assert fix_hints
        return SAFE_SCRIPT

    monkeypatch.setattr(
        script_artifact_service,
        "fix_script_content",
        fake_fix_script_content,
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.approved
    assert policy_check["quality_approved"] is True
    assert len(policy_check["review_attempts"]) == 1
    assert policy_check["review_attempts"][0]["quality_issues"] == [
        "工具缺失時應回傳 unknown，不可使用 warning"
    ]
    assert ai_review["approved"] is True
    assert len(seen_snapshots) == 1
    assert "previous_review_feedback" not in seen_snapshots[0]
    assert review_call_count[0] == 1


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
    repair_instruction = user_payload["repair_instructions"][0]
    assert repair_instruction["line_range"] == [4, 5]
    assert repair_instruction["snippet"] == "0004|except Exception:\n0005|    pass"
    assert "errors.append" in repair_instruction["required_pattern"]
    assert "fix_instructions" in user_payload
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
                    'raw_text = raw if isinstance(raw, str) else json.dumps(raw, '
                    'ensure_ascii=False, default=str); "raw": truncate_output(raw_text)'
                ),
                "description": "raw 必須在 helper 定義內截斷",
            }
        ]
    )

    assert instructions == [
        {
            "issue": "raw 必須在 helper 定義內截斷",
            "fix_goal": "只修改 record_check 函式定義；將非字串 raw payload 先序列化，再將回傳物件的 \"raw\" 欄位交給一次 truncate_output(raw_text)，呼叫端保持傳入原始 raw，不要只修改呼叫端。",
            "target": "record_check_definition",
            "line_range": [3, 8],
            "snippet": "0003|def record_check(...):",
            "required_pattern": (
                'raw_text = raw if isinstance(raw, str) else json.dumps(raw, '
                'ensure_ascii=False, default=str); "raw": truncate_output(raw_text)'
            ),
        }
    ]


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_on_noncanonical_record_check_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = dedent(
        """
        import json
        import platform

        def truncate_output(text: str, limit: int = 400) -> str:
            return text[:limit]

        def record_check(check_id: str, title: str, status: str, evidence: str,
                         raw_stdout: str, raw_stderr: str,
                         returncode: int | None) -> dict[str, object]:
            return {
                "id": check_id,
                "title": title,
                "status": status,
                "evidence": evidence,
                "raw": {
                    "stdout": truncate_output(raw_stdout),
                    "stderr": truncate_output(raw_stderr),
                    "returncode": returncode,
                },
            }

        checks = [record_check("runtime.python", "收集 Python", "unknown", "n/a", "", "", None)]
        print(json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "now", "platform": platform.platform()},
            "summary": "checked",
            "checks": checks,
            "errors": [],
        }, ensure_ascii=False))
        """
    ).strip()

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return source, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )

    script_content, policy_check, ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert policy_check["approved"] is False
    assert policy_check["retry_summary"]["stop_reason"] == "same_failure_limit"
    assert policy_check["review_attempts"][0]["repair_mode"] == "fresh_generation"
    assert policy_check["review_attempts"][0]["fix_hints"][0]["type"] == (
        "normalize_record_check_contract"
    )
    assert ai_review["approved"] is False


@pytest.mark.asyncio
async def test_build_reviewed_script_re_reviews_after_ai_feedback_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai_review_results = [
        {
            "approved": False,
            "risk_level": "medium",
            "issues": ["except Exception 後未將錯誤記錄到 errors"],
            "suggested_fix": "在 except 中追加 errors.append()",
        },
        {
            "approved": True,
            "risk_level": "low",
            "issues": [],
            "suggested_fix": None,
        },
    ]
    reviewed_scripts: list[str] = []

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        reviewed_scripts.append(script_content)
        return ai_review_results.pop(0)

    async def fake_fix_script_content(*, script_content, fix_hints):
        assert fix_hints[0]["type"] == "ai_reviewer_feedback"
        return SAFE_SCRIPT.replace('"summary": "ok"', '"summary": "fixed"')

    monkeypatch.setattr(
        script_artifact_service,
        "generate_script_content",
        fake_generate_script_content,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "review_script_with_ai",
        fake_review_script_with_ai,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "fix_script_content",
        fake_fix_script_content,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert '"summary": "fixed"' in script_content
    assert policy_check["approved"] is True
    assert ai_review["approved"] is True
    assert status == TeacherJudgeScriptStatus.approved
    assert len(reviewed_scripts) == 2


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_after_two_retries_of_same_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_calls = [0]
    generate_calls = [0]
    generate_snapshots = []
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        generate_snapshots.append(rubric_snapshot)
        return "same-error"

    async def fake_fix_script_content(*, script_content, fix_hints):
        fix_calls[0] += 1
        return "same-error"

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(script_artifact_service, "generate_script_content", fake_generate_script_content)
    monkeypatch.setattr(script_artifact_service, "fix_script_content", fake_fix_script_content)
    monkeypatch.setattr(script_artifact_service, "review_script_with_ai", fake_review_script_with_ai)
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": False,
            "blocked": True,
            "risk_level": "high",
            "issues": ["固定錯誤"],
            "fix_hints": [{"type": "fixed_failure", "description": "固定錯誤"}],
        },
    )

    _script, policy_check, _ai_review, status = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert generate_calls[0] == 2
    assert fix_calls[0] == 1
    assert review_calls[0] == 0
    assert policy_check["retry_summary"]["retry_count"] == 2
    assert policy_check["retry_summary"]["stop_reason"] == "same_failure_limit"
    assert len(policy_check["review_attempts"]) == 3
    assert [attempt["repair_mode"] for attempt in policy_check["review_attempts"]] == [
        "line_patch",
        "fresh_generation",
        "stop",
    ]
    assert generate_snapshots[1]["previous_review_feedback"]["repair_guidance"]


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_after_two_retries_of_same_ai_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fix_calls = [0]
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return "static-safe", [], {}

    async def fake_fix_script_content(*, script_content, fix_hints):
        fix_calls[0] += 1
        return "static-safe"

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        return {
            "approved": False,
            "risk_level": "high",
            "issues": ["固定 AI 錯誤"],
            "suggested_fix": "固定修正建議",
        }

    monkeypatch.setattr(
        script_artifact_service,
        "generate_script_content",
        fake_generate_script_content,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "fix_script_content",
        fake_fix_script_content,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "review_script_with_ai",
        fake_review_script_with_ai,
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )

    _script, policy_check, ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert ai_review["approved"] is False
    assert fix_calls[0] == 2
    assert review_calls[0] == 3
    assert policy_check["retry_summary"]["retry_count"] == 2
    assert policy_check["retry_summary"]["stop_reason"] == "same_failure_limit"
    assert len(policy_check["review_attempts"]) == 3


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_after_four_total_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_script = ["candidate-0"]
    fix_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return current_script[0]

    async def fake_fix_script_content(*, script_content, fix_hints):
        fix_calls[0] += 1
        current_script[0] = f"candidate-{fix_calls[0]}"
        return current_script[0]

    monkeypatch.setattr(script_artifact_service, "generate_script_content", fake_generate_script_content)
    monkeypatch.setattr(script_artifact_service, "fix_script_content", fake_fix_script_content)
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": False,
            "blocked": True,
            "risk_level": "high",
            "issues": [f"錯誤 {script_content}"],
            "fix_hints": [{"type": f"failure_{script_content}"}],
        },
    )

    _script, policy_check, _ai_review, status = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert fix_calls[0] == 4
    assert policy_check["retry_summary"]["retry_count"] == 4
    assert policy_check["retry_summary"]["stop_reason"] == "total_retry_limit"
    assert len(policy_check["review_attempts"]) == 5


@pytest.mark.asyncio
async def test_build_reviewed_script_retries_initial_generation_format_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        if generate_calls[0] == 1:
            raise HTTPException(status_code=502, detail="AI 產生腳本格式不是 JSON。")
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.approved
    assert generate_calls[0] == 2
    assert len(policy_check["review_attempts"]) == 1
    assert policy_check["review_attempts"][0]["phase"] == "generation"
    assert policy_check["review_attempts"][0]["generation_issues"] == [
        "AI 產生腳本格式不是 JSON。"
    ]
    assert "generation_error" not in policy_check
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_after_repeated_generation_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        raise HTTPException(status_code=504, detail="AI 服務回應超時，請稍後再試。")

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )

    (
        script_content,
        policy_check,
        _ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == ""
    assert status == TeacherJudgeScriptStatus.review_failed
    assert generate_calls[0] == 3
    assert policy_check["retry_summary"]["retry_count"] == 2
    assert policy_check["retry_summary"]["stop_reason"] == "same_failure_limit"
    assert len(policy_check["review_attempts"]) == 3
    assert all(
        attempt["phase"] == "generation"
        for attempt in policy_check["review_attempts"]
    )
    assert policy_check["generation_error"] == "AI 服務回應超時，請稍後再試。"
    assert "AI 服務回應超時，請稍後再試。" in policy_check["issues"]


@pytest.mark.asyncio
async def test_build_reviewed_script_propagates_non_retryable_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        raise HTTPException(status_code=503, detail="模型未設定")

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )

    with pytest.raises(HTTPException) as error:
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )

    assert error.value.status_code == 503
    assert generate_calls[0] == 1


@pytest.mark.asyncio
async def test_build_reviewed_script_recovers_after_fallback_generation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        if generate_calls[0] == 1:
            return "bad-script", [], {}
        if generate_calls[0] == 2:
            raise HTTPException(status_code=502, detail="AI 產生腳本格式不是 JSON。")
        return SAFE_SCRIPT, [], {}

    async def fake_fix_script_content(*, script_content, fix_hints):
        raise HTTPException(status_code=502, detail="AI 修正輸出格式不是 JSON。")

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "fix_script_content", fake_fix_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": script_content != "bad-script",
            "blocked": script_content == "bad-script",
            "risk_level": "high" if script_content == "bad-script" else "low",
            "issues": ["品質錯誤"] if script_content == "bad-script" else [],
            "fix_hints": [{"type": "fixed_failure", "description": "品質錯誤"}]
            if script_content == "bad-script"
            else [],
        },
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.approved
    assert generate_calls[0] == 3
    assert [attempt["phase"] for attempt in policy_check["review_attempts"]] == [
        "static",
        "generation",
    ]
    assert "generation_error" not in policy_check
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_build_reviewed_script_retries_ai_review_call_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        if review_calls[0] == 1:
            raise HTTPException(status_code=504, detail="AI 服務回應超時，請稍後再試。")
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.approved
    assert review_calls[0] == 2
    assert len(policy_check["review_attempts"]) == 1
    assert policy_check["review_attempts"][0]["phase"] == "ai_review_call"
    assert policy_check["review_attempts"][0]["ai_review_issues"] == [
        "AI 複核呼叫失敗：AI 服務回應超時，請稍後再試。"
    ]
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_build_reviewed_script_stops_after_repeated_ai_review_call_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        raise HTTPException(status_code=502, detail="AI 服務異常（狀態碼 502）")

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.review_failed
    assert review_calls[0] == 3
    assert policy_check["retry_summary"]["retry_count"] == 2
    assert policy_check["retry_summary"]["stop_reason"] == "same_failure_limit"
    assert all(
        attempt["phase"] == "ai_review_call"
        for attempt in policy_check["review_attempts"]
    )
    assert "AI 複核呼叫失敗：AI 服務異常（狀態碼 502）" in policy_check["issues"]
    assert ai_review["approved"] is False


@pytest.mark.asyncio
async def test_build_reviewed_script_propagates_non_retryable_ai_review_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        raise HTTPException(status_code=503, detail="模型未設定")

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    with pytest.raises(HTTPException) as error:
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )

    assert error.value.status_code == 503
    assert review_calls[0] == 1


@pytest.mark.asyncio
async def test_build_reviewed_script_retries_when_coverage_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        if generate_calls[0] == 1:
            return SAFE_SCRIPT
        return SAFE_SCRIPT, [], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        _script,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.approved
    assert generate_calls[0] == 2
    assert [attempt["phase"] for attempt in policy_check["review_attempts"]] == [
        "coverage"
    ]
    assert policy_check["review_attempts"][0]["coverage_issues"] == [
        "模型未提供 rubric 覆蓋映射（coverage）"
    ]
    assert policy_check["retry_summary"]["retry_count"] == 1
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_build_reviewed_script_blocks_uncovered_rubric_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        if generate_calls[0] == 1:
            return SCRIPT_WITH_RECORD_CHECK, [
                {"check_id": "runtime.python_version", "rubric_item_ids": []}
            ], {}
        return SCRIPT_WITH_RECORD_CHECK, [
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]}
        ], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        _script,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={
            "template_key": "linux",
            "items": [{"id": "item-1", "title": "輸出整數 20"}],
        },
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.approved
    assert generate_calls[0] == 2
    assert [attempt["phase"] for attempt in policy_check["review_attempts"]] == [
        "coverage"
    ]
    first_attempt = policy_check["review_attempts"][0]
    assert first_attempt["uncovered_rubric_items"] == [
        {"id": "item-1", "title": "輸出整數 20"}
    ]
    assert any("item-1" in issue for issue in first_attempt["coverage_issues"])
    assert policy_check["coverage"]["approved"] is True
    assert policy_check["coverage"]["mappings"] == [
        {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]}
    ]
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_build_reviewed_script_persists_terminal_coverage_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]
    review_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        return SAFE_SCRIPT, [
            {"check_id": "missing.check", "rubric_item_ids": ["item-1"]}
        ], {}

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        review_calls[0] += 1
        return {"approved": True, "risk_level": "low", "issues": []}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )

    _script, policy_check, _ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={
                "template_key": "linux",
                "items": [{"id": "item-1", "title": "收集 Python 版本"}],
            },
            template_key="linux",
        )
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert generate_calls[0] == 3
    assert review_calls[0] == 0
    assert policy_check["approved"] is False
    assert policy_check["blocked"] is True
    assert policy_check["coverage"]["approved"] is False
    assert any("missing.check" in issue for issue in policy_check["coverage"]["issues"])
    assert len(policy_check["review_attempts"]) == 3


@pytest.mark.asyncio
async def test_build_reviewed_script_stabilizes_changing_coverage_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]
    snapshots: list[dict[str, object]] = []

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        snapshots.append(rubric_snapshot)
        return SAFE_SCRIPT, [
            {
                "check_id": f"model-invented-{generate_calls[0]}",
                "rubric_item_ids": ["item-1"],
            }
        ], {}

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
            "fix_hints": [],
        },
    )

    _script, policy_check, _ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={
                "template_key": "linux",
                "items": [{"id": "item-1", "title": "收集 Python 版本"}],
            },
            template_key="linux",
        )
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert generate_calls[0] == 3
    assert [attempt["same_failure_count"] for attempt in policy_check["review_attempts"]] == [
        1,
        2,
        3,
    ]
    assert all(
        attempt["available_check_ids"] == []
        for attempt in policy_check["review_attempts"]
    )
    feedback = snapshots[1]["previous_review_feedback"]
    assert feedback["available_check_ids"] == []
    assert feedback["repair_guidance"][0]["target"] == "fix_coverage_refs"


@pytest.mark.asyncio
async def test_build_reviewed_script_realigns_coverage_after_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_calls = [0]

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        generate_calls[0] += 1
        if generate_calls[0] == 1:
            return SCRIPT_WITH_RECORD_CHECK, [
                {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]}
            ], {}
        return SCRIPT_WITH_RENAMED_CHECK, [
            {"check_id": "new.check.id", "rubric_item_ids": ["item-1"]}
        ], {}

    ai_review_results = [
        {
            "approved": False,
            "risk_level": "medium",
            "issues": ["請重新命名 check id"],
            "suggested_fix": "把 runtime.python_version 改為 new.check.id",
        },
        {
            "approved": True,
            "risk_level": "low",
            "issues": [],
            "suggested_fix": None,
        },
    ]

    async def fake_fix_script_content(*, script_content, fix_hints):
        assert fix_hints[0]["type"] == "ai_reviewer_feedback"
        return SCRIPT_WITH_RENAMED_CHECK

    async def fake_review_script_with_ai(*, script_content, rubric_snapshot):
        return ai_review_results.pop(0)

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "fix_script_content", fake_fix_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "review_script_with_ai", fake_review_script_with_ai
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_policy",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )
    monkeypatch.setattr(
        script_artifact_service,
        "check_script_quality",
        lambda script_content: {
            "approved": True,
            "blocked": False,
            "risk_level": "low",
            "issues": [],
        },
    )

    (
        _script,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={
            "template_key": "linux",
            "items": [{"id": "item-1", "title": "輸出整數 20"}],
        },
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.approved
    assert generate_calls[0] == 2
    assert [attempt["phase"] for attempt in policy_check["review_attempts"]] == [
        "ai_review",
        "coverage",
    ]
    realign_attempt = policy_check["review_attempts"][1]
    assert realign_attempt["uncovered_rubric_items"] == [
        {"id": "item-1", "title": "輸出整數 20"}
    ]
    assert policy_check["coverage"]["approved"] is True
    assert policy_check["coverage"]["mappings"] == [
        {"check_id": "new.check.id", "rubric_item_ids": ["item-1"]}
    ]
    assert ai_review["approved"] is True


@pytest.mark.asyncio
async def test_create_artifact_includes_current_template_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    session.add(
        models.TeacherJudgeTemplateCommand(
            template_key="n8n",
            command_key="n8n.port_check",
            command_label="n8n 連接埠檢查",
            category="port",
            command_template="ss -lntp | grep ':5678'",
            description="檢查 n8n port",
        )
    )
    session.commit()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        assert template_key == "n8n"
        assert (
            rubric_snapshot["template_commands"][0]["command_key"] == "n8n.port_check"
        )
        assert (
            "grep ':5678'"
            in rubric_snapshot["template_commands"][0]["command_template"]
        )
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    artifact = await script_artifact_service.create_artifact(
        session=session,
        teaching_class_id=uuid.uuid4(),
        name="rubric.pdf",
        template_key="n8n",
        rubric_analysis=_analysis(),
        created_by=None,
    )

    assert artifact.rubric_snapshot_json["template_commands"][0]["command_key"] == (
        "n8n.port_check"
    )


@pytest.mark.asyncio
async def test_regenerate_approved_artifact_creates_next_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )
    first = await script_artifact_service.create_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis(),
        created_by=None,
    )
    regenerated = await script_artifact_service.regenerate_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=uuid.UUID(first.id),
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated.id != first.id
    assert regenerated.version == 2
    assert regenerated.source == "regenerated"
    assert regenerated.status == "approved"

    regenerated_again = await script_artifact_service.regenerate_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=uuid.UUID(first.id),
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated_again.version == 3


@pytest.mark.asyncio
async def test_regenerate_failed_artifact_passes_previous_review_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="unsafe.pdf",
        template_key="linux",
        rubric_snapshot_json={
            **_analysis().model_dump(mode="json"),
            "template_key": "linux",
        },
        script_content="import subprocess\nsubprocess.run('echo hi', shell=True)",
        status=TeacherJudgeScriptStatus.review_failed,
        policy_check_result_json={
            "approved": False,
            "issues": ["工具缺失時應回傳 unknown，不可使用 warning"],
            "safety_approved": True,
            "safety_issues": [],
            "quality_approved": False,
            "quality_issues": ["工具缺失時應回傳 unknown，不可使用 warning"],
            "coverage": {
                "approved": False,
                "issues": ["以下檢查項目沒有任何 check 證據覆蓋：item-1（n8n Web UI）"],
                "uncovered_items": [{"id": "item-1", "title": "n8n Web UI"}],
                "available_check_ids": ["service.n8n_port"],
            },
            "review_attempts": [
                {
                    "phase": "static",
                    "fix_hints": [
                        {
                            "type": "add_truncate_in_record_check",
                            "target": "record_check_definition",
                            "lineno": 3,
                            "end_lineno": 8,
                            "required_pattern": '"raw": truncate_output(raw)',
                            "description": "record_check 的 raw 欄位必須在函式定義內截斷",
                        }
                    ],
                }
            ],
        },
        ai_review_result_json={
            "approved": False,
            "issues": ["指令執行方式不符合規範"],
            "suggested_fix": "改用 argv list 與 timeout",
        },
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        feedback = rubric_snapshot["previous_review_feedback"]
        assert feedback["policy_approved"] is True
        assert feedback["policy_issues"] == []
        assert feedback["quality_approved"] is False
        assert feedback["quality_issues"] == [
            "工具缺失時應回傳 unknown，不可使用 warning"
        ]
        assert feedback["coverage_approved"] is False
        assert feedback["coverage_issues"] == [
            "以下檢查項目沒有任何 check 證據覆蓋：item-1（n8n Web UI）"
        ]
        assert feedback["uncovered_rubric_items"] == [
            {"id": "item-1", "title": "n8n Web UI"}
        ]
        assert feedback["available_check_ids"] == ["service.n8n_port"]
        assert feedback["repair_guidance"][0]["target"] == "record_check_definition"
        assert feedback["repair_guidance"][0]["line_range"] == [3, 8]
        assert feedback["repair_guidance"][0]["required_pattern"] == '"raw": truncate_output(raw)'
        assert feedback["ai_review_issues"] == ["指令執行方式不符合規範"]
        assert feedback["ai_review_suggested_fix"] == "改用 argv list 與 timeout"
        return (
            SAFE_SCRIPT,
            {
                "approved": True,
                "blocked": False,
                "risk_level": "low",
                "issues": [],
                "safety_approved": True,
                "safety_issues": [],
                "quality_approved": True,
                "quality_issues": [],
            },
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    regenerated = await script_artifact_service.regenerate_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated.status == "approved"
    assert "previous_review_feedback" not in regenerated.rubric_snapshot_json


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


@pytest.mark.asyncio
async def test_regenerate_rejects_archived_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )
    artifact = await script_artifact_service.create_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis(),
        created_by=None,
    )
    archived = script_artifact_service.archive_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=uuid.UUID(artifact.id),
    )

    with pytest.raises(HTTPException) as exc_info:
        await script_artifact_service.regenerate_artifact(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=uuid.UUID(archived.id),
            rubric_analysis=None,
            created_by=None,
        )

    assert exc_info.value.status_code == 400


def test_approve_rejects_failed_review_artifact() -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="unsafe.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content="print('unsafe')",
        status=TeacherJudgeScriptStatus.review_failed,
        policy_check_result_json={"approved": False},
        ai_review_result_json={"approved": False},
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    with pytest.raises(HTTPException) as exc_info:
        script_artifact_service.approve_artifact(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact.id,
            approved_by=None,
        )

    assert exc_info.value.status_code == 400


def test_delete_artifact_removes_script_even_when_archived() -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="old.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.archived,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    script_artifact_service.delete_artifact(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
    )

    assert (
        script_artifact_service.list_artifacts(
            session=session,
            teaching_class_id=teaching_class_id,
        )
        == []
    )
    with pytest.raises(HTTPException) as exc_info:
        script_artifact_service.get_artifact(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact.id,
        )
    assert exc_info.value.status_code == 404


def test_create_script_run_snapshots_only_running_class_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {
                "user_id": str(user_id),
                "email": "student@example.com",
                "full_name": "Student",
            }
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    assert run.status == "pending"
    assert run.started_by == str(user_id)
    assert run.target_snapshot_json["targets"][0]["name"] == "101"
    assert run.target_snapshot_json["targets"][0]["resource_type"] == "lxc"
    assert run.target_snapshot_json["targets"][0]["proxmox_node"] == "pve1"
    assert run.target_snapshot_json["targets"][0]["user"]["full_name"] == "Student"
    assert run.progress_json["targets"][0]["status"] == "queued"
    assert run.progress_json["targets"][0]["user"]["email"] == "student@example.com"


def test_create_script_run_falls_back_to_live_ip_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=131, user_id=user_id, ip=None)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            131: {
                "user_id": str(user_id),
                "email": "student@example.com",
                "full_name": "Student",
            }
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {131: {"vmid": 131, "type": "lxc", "status": "running", "node": "pve"}},
    )
    monkeypatch.setattr(
        target_ip_resolver.proxmox_ops,
        "get_ip_address",
        lambda node, vmid, resource_type: "10.0.0.131",
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[131],
        started_by=user_id,
    )

    assert run.target_snapshot_json["targets"][0]["ip_address"] == "10.0.0.131"
    assert (
        resource_repo.get_cached_ip_address(session=session, vmid=131) == "10.0.0.131"
    )


def test_create_script_run_rejects_stopped_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": None}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {101: {"vmid": 101, "type": "qemu", "status": "stopped"}},
    )

    with pytest.raises(HTTPException) as exc_info:
        script_run_service.create_script_run(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact.id,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=[101],
            started_by=None,
        )

    assert exc_info.value.status_code == 400
    assert "不是運行中" in str(exc_info.value.detail)


def test_create_script_run_rejects_target_without_ssh_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    resource = _add_resource(session, vmid=101, user_id=user_id)
    resource.ssh_private_key_encrypted = None
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": None}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {101: {"vmid": 101, "type": "qemu", "status": "running"}},
    )

    with pytest.raises(HTTPException) as exc_info:
        script_run_service.create_script_run(
            session=session,
            teaching_class_id=teaching_class_id,
            artifact_id=artifact.id,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=[101],
            started_by=None,
        )

    assert exc_info.value.status_code == 400
    assert "SSH" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_execute_script_run_saves_valid_target_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": "S"}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_live_running_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_execute_target_script",
        lambda *, target, script_content: script_executor_service.RemoteScriptResult(
            exit_code=0,
            result_json_text=_valid_result_json(),
            stderr_text="",
        ),
    )
    analysis_calls = []

    async def fake_analyze_target_results(
        *,
        rubric_snapshot,
        script_metadata,
        target_results,
    ):
        analysis_calls.append(
            {
                "rubric_snapshot": rubric_snapshot,
                "script_metadata": script_metadata,
                "target_results": target_results,
            }
        )
        return [
            {
                **result,
                "ai_judgement": {
                    "schema_version": "teacher_judge_ai_judgement.v1",
                    "status": "completed",
                    "summary": "符合檢查表要求。",
                    "item_judgements": [],
                },
            }
            for result in target_results
        ]

    monkeypatch.setattr(
        script_executor_service,
        "analyze_target_results",
        fake_analyze_target_results,
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    await script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    assert stored_run.status.value == "completed"
    assert stored_run.progress_json["stage"] == "completed"
    assert stored_run.result_summary_json["valid_json"] == 1
    assert stored_run.result_summary_json["ai_completed"] == 1
    assert stored_run.target_results_json["targets"][0]["status"] == "completed"
    assert stored_run.target_results_json["targets"][0]["reason_code"] == "success"
    assert stored_run.target_results_json["targets"][0]["proxmox_node"] == "pve1"
    assert stored_run.target_results_json["targets"][0]["resource_type"] == "lxc"
    assert stored_run.target_results_json["targets"][0]["user"]["full_name"] == "S"
    assert stored_run.target_results_json["targets"][0]["validation"]["valid"] is True
    assert (
        stored_run.target_results_json["targets"][0]["parsed_result"]["schema_version"]
        == "teacher_judge_result.v1"
    )
    assert "score" not in stored_run.target_results_json["targets"][0]["ai_judgement"]
    assert analysis_calls[0]["script_metadata"]["id"] == str(artifact.id)
    assert analysis_calls[0]["target_results"][0]["ai_judgement"]["status"] == "pending"


@pytest.mark.asyncio
async def test_execute_script_run_does_not_commit_partial_results_before_analysis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": "S"}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_live_running_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_execute_target_script",
        lambda *, target, script_content: script_executor_service.RemoteScriptResult(
            exit_code=0,
            result_json_text=_valid_result_json(),
            stderr_text="",
        ),
    )

    async def raise_analysis_error(**_kwargs):
        raise RuntimeError("analysis unavailable")

    monkeypatch.setattr(
        script_executor_service,
        "analyze_target_results",
        raise_analysis_error,
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    await script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    assert stored_run.status.value == "failed"
    assert stored_run.result_summary_json["executor_error"] == "analysis unavailable"
    assert stored_run.target_results_json == {}


def test_executor_runtime_target_falls_back_to_live_ip_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    _add_resource(session, vmid=131, user_id=user_id, ip=None)
    session.commit()

    run = models.TeacherJudgeScriptRun(
        teaching_class_id=teaching_class_id,
        artifact_id=uuid.uuid4(),
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        status=TeacherJudgeScriptRunStatus.running,
    )
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        target_ip_resolver.proxmox_ops,
        "get_ip_address",
        lambda node, vmid, resource_type: "10.0.0.131",
    )

    target = script_executor_service._resolve_runtime_target(
        session=session,
        run=run,
        target={"vmid": 131, "user_id": str(user_id), "name": "131"},
        live_by_vmid={
            131: {"vmid": 131, "type": "lxc", "status": "running", "node": "pve"}
        },
    )

    assert target["host"] == "10.0.0.131"
    assert target["private_key_pem"] == "KEY"
    assert (
        resource_repo.get_cached_ip_address(session=session, vmid=131) == "10.0.0.131"
    )


@pytest.mark.asyncio
async def test_execute_script_run_saves_invalid_json_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": "S"}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_live_running_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )
    monkeypatch.setattr(
        script_executor_service,
        "_execute_target_script",
        lambda *, target, script_content: script_executor_service.RemoteScriptResult(
            exit_code=0,
            result_json_text="{not-json",
            stderr_text="",
        ),
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    await script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    result = stored_run.target_results_json["targets"][0]
    assert stored_run.status.value == "completed"
    assert stored_run.result_summary_json["invalid_json"] == 1
    assert stored_run.result_summary_json["ai_skipped"] == 1
    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_json"
    assert result["proxmox_node"] == "pve1"
    assert result["user"]["email"] == "s@example.com"
    assert result["validation"]["valid"] is False
    assert result["ai_judgement"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_ai_analysis_skips_invalid_target_without_calling_vllm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_call_ai_judgement(payload):
        nonlocal called
        called = True
        return {"status": "completed"}

    monkeypatch.setattr(
        script_result_analysis_service,
        "_call_ai_judgement",
        fake_call_ai_judgement,
    )

    results = await script_result_analysis_service.analyze_target_results(
        rubric_snapshot={},
        script_metadata={"id": "script-1"},
        target_results=[
            {
                "vmid": 101,
                "status": "failed",
                "validation": {
                    "valid": False,
                    "error": "invalid json",
                },
                "parsed_result": None,
            }
        ],
    )

    assert called is False
    assert results[0]["ai_judgement"]["status"] == "skipped"
    assert results[0]["ai_judgement"]["summary"] == "invalid json"


@pytest.mark.asyncio
async def test_ai_analysis_uses_valid_json_even_when_execution_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    async def fake_call_ai_judgement(payload):
        captured_payload.update(payload)
        return {
            "schema_version": "teacher_judge_ai_judgement.v1",
            "status": "completed",
            "summary": "部分符合。",
            "item_judgements": [],
        }

    monkeypatch.setattr(
        script_result_analysis_service,
        "_call_ai_judgement",
        fake_call_ai_judgement,
    )

    results = await script_result_analysis_service.analyze_target_results(
        rubric_snapshot={},
        script_metadata={"id": "script-1"},
        target_results=[
            {
                "vmid": 101,
                "status": "failed",
                "reason_code": "execution_nonzero",
                "exit_code": 1,
                "validation": {"valid": True},
                "parsed_result": json.loads(_valid_result_json()),
            }
        ],
    )

    assert captured_payload["target"]["execution_status"] == "failed"
    assert captured_payload["target"]["reason_code"] == "execution_nonzero"
    assert results[0]["ai_judgement"]["status"] == "completed"
    assert "score" not in results[0]["ai_judgement"]


@pytest.mark.asyncio
async def test_execute_script_run_records_executor_level_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    teaching_class_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        teaching_class_id=teaching_class_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    _add_resource(session, vmid=101, user_id=user_id)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(
        script_run_service,
        "_class_member_by_vmid",
        lambda *, session, teaching_class_id: {
            101: {"user_id": str(user_id), "email": "s@example.com", "full_name": "S"}
        },
    )
    monkeypatch.setattr(
        script_run_service,
        "_running_resources_by_vmid",
        lambda: {
            101: {"vmid": 101, "type": "lxc", "status": "running", "node": "pve1"}
        },
    )

    def raise_live_lookup_error() -> dict[int, dict[str, object]]:
        raise RuntimeError("proxmox unavailable")

    monkeypatch.setattr(
        script_executor_service,
        "_live_running_by_vmid",
        raise_live_lookup_error,
    )

    run = script_run_service.create_script_run(
        session=session,
        teaching_class_id=teaching_class_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    await script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    assert stored_run.status.value == "failed"
    assert stored_run.progress_json["stage"] == "failed"
    assert stored_run.result_summary_json["executor_error"] == "proxmox unavailable"


def test_execute_target_script_uploads_runs_and_collects_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRemoteFile:
        def __init__(self, files: dict[str, bytes], path: str, mode: str) -> None:
            self.files = files
            self.path = path
            self.mode = mode

        def __enter__(self) -> FakeRemoteFile:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def write(self, data: bytes) -> None:
            self.files[self.path] = data

        def read(self) -> bytes:
            return self.files[self.path]

    class FakeSFTP:
        def __init__(self) -> None:
            self.files: dict[str, bytes] = {}
            self.closed = False

        def file(self, path: str, mode: str) -> FakeRemoteFile:
            return FakeRemoteFile(self.files, path, mode)

        def close(self) -> None:
            self.closed = True

    class FakeClient:
        def __init__(self) -> None:
            self.sftp = FakeSFTP()
            self.closed = False

        def open_sftp(self) -> FakeSFTP:
            return self.sftp

        def close(self) -> None:
            self.closed = True

    fake_client = FakeClient()
    commands: list[str] = []
    remote_dir = "/tmp/campus-cloud-judge/run-1/101"

    monkeypatch.setattr(
        script_executor_service,
        "create_key_client",
        lambda *args, **kwargs: fake_client,
    )

    def fake_exec_command(client, command, *, timeout):
        commands.append(command)
        if "python3 script.py" in command:
            client.sftp.files[f"{remote_dir}/result.json"] = (
                _valid_result_json().encode()
            )
            client.sftp.files[f"{remote_dir}/stderr.log"] = b""
        return 0, "", ""

    monkeypatch.setattr(script_executor_service, "exec_command", fake_exec_command)

    result = script_executor_service._execute_target_script(
        target={
            "vmid": 101,
            "host": "10.0.0.10",
            "ssh_user": "root",
            "private_key_pem": "KEY",
            "run_id": "run-1",
        },
        script_content=SAFE_SCRIPT,
    )

    cleanup_command = (
        "rm -f -- /tmp/campus-cloud-judge/run-1/101/script.py "
        "/tmp/campus-cloud-judge/run-1/101/result.json "
        "/tmp/campus-cloud-judge/run-1/101/stderr.log && "
        "rmdir -- /tmp/campus-cloud-judge/run-1/101 2>/dev/null || true"
    )
    assert commands == [
        "mkdir -p /tmp/campus-cloud-judge/run-1/101",
        "cd /tmp/campus-cloud-judge/run-1/101 && python3 script.py > result.json 2> stderr.log",
        cleanup_command,
    ]
    assert fake_client.sftp.files[f"{remote_dir}/script.py"] == SAFE_SCRIPT.encode()
    assert result.exit_code == 0
    assert validate_managed_script_output(result.result_json_text)["valid"] is True
    assert fake_client.sftp.closed is True
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_create_artifact_rejects_blank_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()

    async def fake_build_reviewed_script(*, rubric_snapshot, template_key):
        return (
            SAFE_SCRIPT,
            {"approved": True, "blocked": False, "risk_level": "low", "issues": []},
            {"approved": True, "risk_level": "low", "issues": []},
            TeacherJudgeScriptStatus.reviewed,
        )

    monkeypatch.setattr(
        script_artifact_service,
        "build_reviewed_script",
        fake_build_reviewed_script,
    )

    with pytest.raises(HTTPException) as exc_info:
        await script_artifact_service.create_artifact(
            session=session,
            teaching_class_id=uuid.uuid4(),
            name="   ",
            template_key="linux",
            rubric_analysis=_analysis(),
            created_by=None,
        )

    assert exc_info.value.status_code == 400


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
        f'''\nimport json\nimport subprocess\n\nsubprocess.run({argv}, timeout=5)\nprint(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))\n'''.strip()
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
        f'''\nimport json\nimport subprocess\n\nsubprocess.run({argv}, cwd="/srv/student/project", timeout=5)\nprint(json.dumps({{"schema_version": "teacher_judge_result.v1", "metadata": {{"timestamp": "now", "platform": "test"}}, "checks": [], "errors": []}}, ensure_ascii=False))\n'''.strip()
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
