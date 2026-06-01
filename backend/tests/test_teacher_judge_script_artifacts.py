from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401
from app.ai.teacher_judge import (
    script_artifact_service,
    script_executor_service,
    script_run_service,
    target_ip_resolver,
)
from app.ai.teacher_judge.schemas import RubricAnalysis, RubricItem
from app.ai.teacher_judge.script_policy import (
    check_script_policy,
    validate_managed_script_output,
)
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


def _resource(*, vmid: int, user_id: uuid.UUID) -> models.Resource:
    return models.Resource(
        vmid=vmid,
        user_id=user_id,
        environment_type="linux",
        ip_address="10.0.0.10",
        ssh_private_key_encrypted="encrypted-key",
        created_at=datetime.now(timezone.utc),
    )


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
            "fix_hints": [
                {
                    "type": "remove_stdout_truthiness_check",
                    "description": "不能用 stdout/stderr truthiness 直接判定 pass",
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
    assert status == TeacherJudgeScriptStatus.reviewed
    assert policy_check["quality_approved"] is True
    assert len(policy_check["review_attempts"]) == 1
    assert policy_check["review_attempts"][0]["quality_issues"] == [
        "不能用 stdout/stderr truthiness 直接判定 pass"
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
                                    "    errors.append(f\"runtime.python_version: 未預期錯誤: {str(exc)[:200]}\")",
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

    fixed = await script_artifact_service.fix_script_content(
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

    assert (
        script_artifact_service.list_artifacts(
            session=session,
            group_id=group_id,
        )
        == []
    )
    with pytest.raises(HTTPException) as exc_info:
        script_artifact_service.get_artifact(
            session=session,
            group_id=group_id,
            artifact_id=artifact.id,
        )
    assert exc_info.value.status_code == 404


def test_create_script_run_snapshots_only_running_group_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(_resource(vmid=101, user_id=user_id))
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
        group_id=group_id,
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
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    resource = _resource(vmid=131, user_id=user_id)
    resource.ip_address = None
    session.add(resource)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
        lambda: {
            131: {"vmid": 131, "type": "lxc", "status": "running", "node": "pve"}
        },
    )
    monkeypatch.setattr(
        target_ip_resolver.proxmox_ops,
        "get_ip_address",
        lambda node, vmid, resource_type: "10.0.0.131",
    )

    run = script_run_service.create_script_run(
        session=session,
        group_id=group_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[131],
        started_by=user_id,
    )

    assert run.target_snapshot_json["targets"][0]["ip_address"] == "10.0.0.131"
    assert (
        resource_repo.get_cached_ip_address(session=session, vmid=131)
        == "10.0.0.131"
    )


def test_create_script_run_rejects_stopped_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(_resource(vmid=101, user_id=user_id))
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
            group_id=group_id,
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
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    resource = _resource(vmid=101, user_id=user_id)
    resource.ssh_private_key_encrypted = None
    session.add(resource)
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
            group_id=group_id,
            artifact_id=artifact.id,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=[101],
            started_by=None,
        )

    assert exc_info.value.status_code == 400
    assert "SSH" in str(exc_info.value.detail)


def test_execute_script_run_saves_valid_target_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(_resource(vmid=101, user_id=user_id))
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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

    run = script_run_service.create_script_run(
        session=session,
        group_id=group_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    assert stored_run.status.value == "completed"
    assert stored_run.progress_json["stage"] == "completed"
    assert stored_run.result_summary_json["valid_json"] == 1
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


def test_executor_runtime_target_falls_back_to_live_ip_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    resource = _resource(vmid=131, user_id=user_id)
    resource.ip_address = None
    session.add(resource)
    session.commit()

    run = models.TeacherJudgeScriptRun(
        group_id=group_id,
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
        resource_repo.get_cached_ip_address(session=session, vmid=131)
        == "10.0.0.131"
    )


def test_execute_script_run_saves_invalid_json_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(_resource(vmid=101, user_id=user_id))
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(script_executor_service, "decrypt_value", lambda _value: "KEY")
    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
        group_id=group_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    script_executor_service.execute_script_run(uuid.UUID(run.id))

    session.expire_all()
    stored_run = session.get(models.TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert stored_run is not None
    result = stored_run.target_results_json["targets"][0]
    assert stored_run.status.value == "completed"
    assert stored_run.result_summary_json["invalid_json"] == 1
    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_json"
    assert result["proxmox_node"] == "pve1"
    assert result["user"]["email"] == "s@example.com"
    assert result["validation"]["valid"] is False


def test_execute_script_run_records_executor_level_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    group_id = uuid.uuid4()
    user_id = uuid.uuid4()
    artifact = models.TeacherJudgeScriptArtifact(
        group_id=group_id,
        name="rubric.pdf",
        template_key="linux",
        rubric_snapshot_json={},
        script_content=SAFE_SCRIPT,
        status=TeacherJudgeScriptStatus.approved,
        policy_check_result_json={"approved": True},
        ai_review_result_json={"approved": True},
    )
    session.add(_resource(vmid=101, user_id=user_id))
    session.add(artifact)
    session.commit()
    session.refresh(artifact)

    monkeypatch.setattr(script_executor_service, "engine", session.get_bind())
    monkeypatch.setattr(
        script_run_service,
        "_group_member_by_vmid",
        lambda *, session, group_id: {
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
        group_id=group_id,
        artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual,
        target_vmids=[101],
        started_by=user_id,
    )

    script_executor_service.execute_script_run(uuid.UUID(run.id))

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

    assert commands == [
        "mkdir -p /tmp/campus-cloud-judge/run-1/101",
        "cd /tmp/campus-cloud-judge/run-1/101 && python3 script.py > result.json 2> stderr.log",
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
