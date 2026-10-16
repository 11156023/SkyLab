"""Failures retain evidence and cannot masquerade as complete checks."""

import io
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge.script_run_service import project_run_items
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact


def payload(ids=("file-check",)):
    return json.dumps({
        "schema_version": "teacher_judge_result.v1",
        "metadata": {"timestamp": "test", "platform": "linux"},
        "checks": [{"id": value, "title": "File", "status": "pass"} for value in ids],
    })


def target():
    return {"vmid": 101, "run_id": "test", "host": "mock.invalid",
            "ssh_user": "root", "private_key_pem": "MOCK",
            "expected_check_ids": ["file-check"]}


@pytest.mark.parametrize("ids,reason", [
    ([], "missing_checks"),
    (["wrong-id"], "check_id_mismatch"),
    (["file-check", "file-check"], "duplicate_check_ids"),
])
def test_incomplete_output_is_not_success(ids, reason):
    result = executor._target_result(target(), executor.RemoteScriptResult(0, payload(ids), ""))
    assert result["status"] == "failed"
    assert result["reason_code"] == reason
    assert result["validation"]["valid"] is False
    assert result["raw_result_json"] == payload(ids)
    assert result["parsed_result"] is not None


def test_failed_projection_retains_diagnostics_and_evidence():
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(), name="test", template_key="linux", script_content="",
        rubric_snapshot_json={"items": [{"id": "file", "title": "File"}]},
        policy_check_result_json={"coverage": {"mappings": [
            {"check_id": "file-check", "rubric_item_ids": ["file"]},
        ]}},
    )
    result = executor._target_result(target(), executor.RemoteScriptResult(127, "", "python3: not found"))
    result["failure_stage"] = "executing"
    node = project_run_items(artifact=artifact, target_result=result, display_labels={})
    assert node["stderr_excerpt"] == "python3: not found"
    assert node["exit_code"] == 127
    assert node["failure_stage"] == "executing"
    assert node["validation"]["valid"] is False
    assert node["items"][0]["evidence_state"] == "unavailable"


def test_invalid_json_survives_canonical_storage_and_teacher_projection():
    from app.ai.teacher_judge.run_record_service import store_target_result
    from app.models.teacher_judge_run_target import TeacherJudgeRunTarget

    result = executor._target_result(target(), executor.RemoteScriptResult(0, "not JSON", "warning"))
    row = TeacherJudgeRunTarget(run_id=uuid.uuid4(), target_key="vmid:101")
    store_target_result(row, result)
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(), name="test", template_key="linux", script_content="",
    )
    node = project_run_items(artifact=artifact, target_result={
        **row.result_json, "status": row.status, "reason_code": row.reason_code, "exit_code": row.exit_code,
    }, display_labels={})
    assert row.result_json["parsed_result"] is None
    assert row.result_json["raw_result_json"] == "not JSON"
    assert node["reason_code"] == "invalid_json"
    assert node["stdout_excerpt"] == "not JSON"
    assert node["stderr_excerpt"] == "warning"


@pytest.mark.parametrize("scoped", [False, True])
def test_failed_execution_cannot_turn_partial_checks_into_student_grades(scoped):
    from app.services.course.ai_assignment_service import _script_checks

    result = executor._target_result(target(), executor.RemoteScriptResult(2, payload(), "exception"))
    checks = _script_checks(result, artifact=None, item_id="file-check" if scoped else None)
    assert len(checks) == 1
    assert checks[0].status == "unknown"


@pytest.mark.parametrize("status,valid", [("failed", True), ("completed", False)])
def test_teacher_cannot_grade_retained_evidence_from_failed_execution(status, valid):
    from fastapi import HTTPException

    from app.ai.teacher_judge.script_run_service import update_target_review
    from tests.ai.teacher_judge.test_target_reviews import _completed_run

    db, _, _, run = _completed_run()
    document = dict(run.target_results_json)
    document["targets"] = [{**document["targets"][0], "status": status, "validation": {"valid": valid}}]
    run.target_results_json = document
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException) as error:
        update_target_review(db, run, vmid=483, feedback="", decisions={"log": "pass"}, reviewer_id=uuid.uuid4())
    assert error.value.status_code == 409
    db.close()


def test_teacher_file_evidence_preserves_false(tmp_path):
    from app.ai.teacher_judge.deterministic_compiler import compile_check_plan
    from tests.ai.teacher_judge.test_deterministic_compiler import (
        _analysis,
        _execute_compiled_script,
    )

    analysis = _analysis({"type": "file_stat", "path": str(tmp_path / "missing")}, None, judgement_mode="teacher")
    script, _, _, _ = compile_check_plan(analysis, target_node_key="web")
    check = _execute_compiled_script(script, tmp_path)["checks"][0]
    assert check["status"] == "collected"
    assert check["evidence"] == "False"
    assert json.loads(check["raw"])["exists"] is False


def test_authentication_failure_is_visible_without_any_checks(monkeypatch):
    monkeypatch.setattr(executor, "check_current_target_power", lambda target: None)

    def connect(*args, **kwargs):
        raise executor.SSHAuthenticationError("mock authentication rejected")

    monkeypatch.setattr(executor, "create_key_client", connect)
    result = executor._execute_scheduled_target(
        target=target(), script_content="pass", queued_at=executor.perf_counter(), on_start=lambda: None,
    )
    assert result["parsed_result"] is None
    assert result["reason_code"] == "ssh_authentication_failed"
    assert result["failure_stage"] == "connecting"
    assert "authentication rejected" in result["stderr_excerpt"]


class FakeSFTP:
    def get_channel(self):
        return SimpleNamespace(settimeout=lambda value: None)

    def file(self, path, mode):
        assert mode == "wb"
        return io.BytesIO()

    def close(self):
        pass


@pytest.mark.parametrize("failure", ["shell", "stderr_download", "result_download", "timeout"])
def test_transport_failure_keeps_acquired_evidence(monkeypatch, failure):
    commands = []
    reads = []
    client = SimpleNamespace(open_sftp=FakeSFTP, close=lambda: None)
    monkeypatch.setattr(executor, "check_current_target_power", lambda target: None)
    monkeypatch.setattr(executor, "create_key_client", lambda *a, **k: client)

    def execute(client, command, **kwargs):
        commands.append(command)
        if "python3 script.py" in command:
            if failure == "timeout":
                raise TimeoutError("SSH deadline")
            if failure == "shell":
                return 2, "shell output", "shell cannot enter directory"
        return 0, "", ""

    def read(sftp, path, limit):
        reads.append(path)
        if failure == "shell":
            return "", False
        if failure == "stderr_download" and path.endswith("stderr.log"):
            raise OSError("stderr transfer failed")
        if failure == "result_download" and path.endswith("result.json"):
            raise OSError("result transfer failed")
        return (payload() if path.endswith("result.json") else "remote diagnostic"), False

    monkeypatch.setattr(executor, "exec_command", execute)
    monkeypatch.setattr(executor, "_read_remote_text", read)
    result = executor._execute_scheduled_target(
        target=target(), script_content="pass", queued_at=executor.perf_counter(), on_start=lambda: None,
    )
    assert result["status"] == "failed"
    assert len(reads) == 2
    if failure == "shell":
        assert "shell output" in result["stdout_excerpt"]
        assert "shell cannot enter directory" in result["stderr_excerpt"]
    elif failure == "result_download":
        assert "remote diagnostic" in result["stderr_excerpt"]
        assert result["reason_code"] == "download_failed"
    else:
        assert result["raw_result_json"] == payload()
        assert result["parsed_result"]["checks"][0]["id"] == "file-check"
    if failure.endswith("download"):
        cleanup = next(command for command in commands if command.startswith("rm -f"))
        assert "/result.json" not in cleanup
        assert "/stderr.log" not in cleanup
