"""Split from tests/test_teacher_judge_script_artifacts.py: reviewed-script generation & retry limits."""

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

    (
        script_content,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
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

    (
        _script,
        policy_check,
        _ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
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

    (
        _script,
        policy_check,
        ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={"template_key": "linux", "items": []},
        template_key="linux",
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

    monkeypatch.setattr(
        script_artifact_service, "generate_script_content", fake_generate_script_content
    )
    monkeypatch.setattr(
        script_artifact_service, "fix_script_content", fake_fix_script_content
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
            "approved": False,
            "blocked": True,
            "risk_level": "high",
            "issues": [f"錯誤 {script_content}"],
            "fix_hints": [{"type": f"failure_{script_content}"}],
        },
    )

    (
        _script,
        policy_check,
        _ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
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
        attempt["phase"] == "generation" for attempt in policy_check["review_attempts"]
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
