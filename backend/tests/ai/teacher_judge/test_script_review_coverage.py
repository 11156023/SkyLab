"""Split from tests/test_teacher_judge_script_artifacts.py: coverage alignment & repair."""

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
            return (
                SCRIPT_WITH_RECORD_CHECK,
                [{"check_id": "runtime.python_version", "rubric_item_ids": []}],
                {},
            )
        return (
            SCRIPT_WITH_RECORD_CHECK,
            [{"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]}],
            {},
        )

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
        return (
            SAFE_SCRIPT,
            [{"check_id": "missing.check", "rubric_item_ids": ["item-1"]}],
            {},
        )

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

    (
        _script,
        policy_check,
        _ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={
            "template_key": "linux",
            "items": [{"id": "item-1", "title": "收集 Python 版本"}],
        },
        template_key="linux",
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
        return (
            SAFE_SCRIPT,
            [
                {
                    "check_id": f"model-invented-{generate_calls[0]}",
                    "rubric_item_ids": ["item-1"],
                }
            ],
            {},
        )

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

    (
        _script,
        policy_check,
        _ai_review,
        status,
    ) = await script_artifact_service.build_reviewed_script(
        rubric_snapshot={
            "template_key": "linux",
            "items": [{"id": "item-1", "title": "收集 Python 版本"}],
        },
        template_key="linux",
    )

    assert status == TeacherJudgeScriptStatus.review_failed
    assert generate_calls[0] == 3
    assert [
        attempt["same_failure_count"] for attempt in policy_check["review_attempts"]
    ] == [
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
            return (
                SCRIPT_WITH_RECORD_CHECK,
                [{"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]}],
                {},
            )
        return (
            SCRIPT_WITH_RENAMED_CHECK,
            [{"check_id": "new.check.id", "rubric_item_ids": ["item-1"]}],
            {},
        )

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
