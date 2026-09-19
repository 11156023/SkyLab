"""Split from tests/test_teacher_judge_script_artifacts.py: artifact CRUD, regenerate, approve & readiness gates."""

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
    session = make_session()
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

    assert (
        automation_support.get_script_generation_blockers(
            analysis,
            [GENERAL_COMMAND],
        )
        == []
    )


def test_ai_judgement_item_is_script_ready_without_success_criteria() -> None:
    analysis = _analysis()

    assert (
        automation_support.get_script_generation_blockers(
            analysis,
            [GENERAL_COMMAND],
        )
        == []
    )


@pytest.mark.asyncio
async def test_create_artifact_auto_approves_passed_managed_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
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
async def test_create_artifact_includes_current_template_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
        assert (
            feedback["repair_guidance"][0]["required_pattern"]
            == '"raw": truncate_output(raw)'
        )
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


@pytest.mark.asyncio
async def test_regenerate_rejects_archived_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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


@pytest.mark.asyncio
async def test_create_artifact_rejects_blank_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()

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
