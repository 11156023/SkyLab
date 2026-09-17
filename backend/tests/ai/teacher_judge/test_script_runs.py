"""Split from tests/test_teacher_judge_script_artifacts.py: script runs (snapshot/execute/analyse)."""

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


def test_create_script_run_snapshots_only_running_class_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
    session = make_session()
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
