from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401
from app.ai.teacher_judge import script_artifact_service
from app.ai.teacher_judge.schemas import RubricAnalysis, RubricItem
from app.ai.teacher_judge.script_policy import (
    check_script_policy,
    validate_managed_script_output,
)
from app.api.routes.teacher_judge_scripts import _normalize_supported_template_key
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptStatus

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


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _analysis() -> RubricAnalysis:
    return RubricAnalysis(
        items=[
            RubricItem(
                id="item-1",
                title="n8n Web UI",
                description="確認 n8n 可存取",
                checked=False,
                detectable="auto",
                detection_method="檢查 localhost 5678",
                fallback=None,
                check_steps=[],
            )
        ],
        total_items=1,
        auto_count=1,
        summary="n8n rubric",
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
    assert user_payload["template_commands"][0]["command_key"] == "n8n.port_check"
    assert user_payload["previous_review_feedback"]["policy_issues"] == [
        "禁止使用 shell=True 執行指令"
    ]
    assert user_payload["previous_review_feedback"]["quality_issues"] == [
        "工具缺失時應回傳 unknown，不可使用 warning"
    ]


@pytest.mark.asyncio
async def test_create_artifact_saves_reviewed_managed_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
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
        group_id=group_id,
        name="rubric.pdf",
        template_key="n8n",
        rubric_analysis=_analysis(),
        created_by=user_id,
    )

    assert artifact.status == "reviewed"
    assert artifact.script_language == "python"
    assert artifact.source == "ai_generated"
    assert artifact.rubric_snapshot_json["template_key"] == "n8n"

    approved = script_artifact_service.approve_artifact(
        session=session,
        group_id=group_id,
        artifact_id=uuid.UUID(artifact.id),
        approved_by=user_id,
    )

    assert approved.status == "approved"
    assert approved.approved_by == str(user_id)


@pytest.mark.asyncio
async def test_build_reviewed_script_retries_with_quality_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_snapshots: list[dict] = []

    async def fake_generate_script_content(*, rubric_snapshot, template_key):
        seen_snapshots.append(dict(rubric_snapshot))
        return "bad-script"

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
            "issues": ["不能用 stdout/stderr truthiness 直接判定 pass"]
            if script_content == "bad-script"
            else [],
            "fix_hints": [{"type": "remove_stdout_truthiness_check", "description": "不能用 stdout/stderr truthiness 直接判定 pass"}]
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

    script_content, policy_check, ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )
    )

    assert script_content == SAFE_SCRIPT
    assert status == TeacherJudgeScriptStatus.reviewed
    assert policy_check["quality_approved"] is True
    assert ai_review["approved"] is True
    assert len(seen_snapshots) == 1
    assert "previous_review_feedback" not in seen_snapshots[0]
    assert review_call_count[0] == 1


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
        return SAFE_SCRIPT

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

    script_content, policy_check, ai_review, status = (
        await script_artifact_service.build_reviewed_script(
            rubric_snapshot={"template_key": "linux", "items": []},
            template_key="linux",
        )
    )

    assert '"summary": "fixed"' in script_content
    assert policy_check["approved"] is True
    assert ai_review["approved"] is True
    assert status == TeacherJudgeScriptStatus.reviewed
    assert len(reviewed_scripts) == 2


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
            rubric_snapshot["template_commands"][0]["command_key"]
            == "n8n.port_check"
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
        group_id=uuid.uuid4(),
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
    group_id = uuid.uuid4()

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
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis(),
        created_by=None,
    )
    approved = script_artifact_service.approve_artifact(
        session=session,
        group_id=group_id,
        artifact_id=uuid.UUID(first.id),
        approved_by=None,
    )

    regenerated = await script_artifact_service.regenerate_artifact(
        session=session,
        group_id=group_id,
        artifact_id=uuid.UUID(approved.id),
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated.id != approved.id
    assert regenerated.version == 2
    assert regenerated.source == "regenerated"
    assert regenerated.status == "reviewed"

    regenerated_again = await script_artifact_service.regenerate_artifact(
        session=session,
        group_id=group_id,
        artifact_id=uuid.UUID(approved.id),
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated_again.version == 3


@pytest.mark.asyncio
async def test_regenerate_failed_artifact_passes_previous_review_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="unsafe.pdf",
        template_key="linux",
        rubric_snapshot_json={"template_key": "linux", "items": []},
        script_content="import subprocess\nsubprocess.run('echo hi', shell=True)",
        status=TeacherJudgeScriptStatus.review_failed,
        policy_check_result_json={
            "approved": False,
            "issues": ["工具缺失時應回傳 unknown，不可使用 warning"],
            "safety_approved": True,
            "safety_issues": [],
            "quality_approved": False,
            "quality_issues": ["工具缺失時應回傳 unknown，不可使用 warning"],
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
        group_id=group_id,
        artifact_id=artifact.id,
        rubric_analysis=None,
        created_by=None,
    )

    assert regenerated.status == "reviewed"
    assert "previous_review_feedback" not in regenerated.rubric_snapshot_json


@pytest.mark.asyncio
async def test_regenerate_rejects_archived_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()

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
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_analysis=_analysis(),
        created_by=None,
    )
    archived = script_artifact_service.archive_artifact(
        session=session,
        group_id=group_id,
        artifact_id=uuid.UUID(artifact.id),
    )

    with pytest.raises(HTTPException) as exc_info:
        await script_artifact_service.regenerate_artifact(
            session=session,
            group_id=group_id,
            artifact_id=uuid.UUID(archived.id),
            rubric_analysis=None,
            created_by=None,
        )

    assert exc_info.value.status_code == 400


def test_approve_rejects_failed_review_artifact() -> None:
    session = _session()
    group_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
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
            group_id=group_id,
            artifact_id=artifact.id,
            approved_by=None,
        )

    assert exc_info.value.status_code == 400


def test_delete_artifact_removes_script_even_when_archived() -> None:
    session = _session()
    group_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
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
        group_id=group_id,
        artifact_id=artifact.id,
    )

    assert script_artifact_service.list_artifacts(
        session=session,
        group_id=group_id,
    ) == []
    with pytest.raises(HTTPException) as exc_info:
        script_artifact_service.get_artifact(
            session=session,
            group_id=group_id,
            artifact_id=artifact.id,
        )
    assert exc_info.value.status_code == 404


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
            group_id=uuid.uuid4(),
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
    result = check_script_policy("import subprocess\nsubprocess.run('rm -rf /', shell=True)")

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


def test_script_policy_blocks_sensitive_file_open() -> None:
    result = check_script_policy(
        """
import json

with open("/home/student/.ssh/id_rsa", "r", encoding="utf-8") as key_file:
    key_file.read()

print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is False
    assert any("敏感檔案" in issue for issue in result["issues"])


def test_script_policy_blocks_sensitive_subprocess_path() -> None:
    result = check_script_policy(
        """
import json
import subprocess

subprocess.run(["cat", "/home/student/.ssh/id_rsa"], timeout=5)
print(json.dumps({"schema_version": "teacher_judge_result.v1", "metadata": {"timestamp": "now", "platform": "test"}, "checks": [], "errors": []}, ensure_ascii=False))
""".strip()
    )

    assert result["approved"] is False
    assert any("敏感檔案" in issue for issue in result["issues"])


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
