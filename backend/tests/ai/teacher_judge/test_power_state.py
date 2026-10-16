"""Power-off skips must leave durable failures without opening SSH."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge import script_run_service as service
from app.ai.teacher_judge.run_record_service import batch_status
from app.models.base import get_datetime_utc
from app.models.resource import Resource
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunTargetScope,
)
from tests.ai.teacher_judge.helpers import activate_script, make_session
from tests.ai.teacher_judge.test_scripts_execution import _target


@pytest.mark.parametrize(
    ("current", "reason"),
    [
        ({"status": "stopped"}, "vm_powered_off"),
        ({"status": "paused"}, "not_running"),
        ({"status": "unknown"}, "vm_status_unavailable"),
        ({}, "vm_status_unavailable"),
        (None, "vm_status_unavailable"),
        (TimeoutError("PVE unavailable"), "vm_status_unavailable"),
    ],
)
@pytest.mark.parametrize("resource_type", ["qemu", "lxc"])
def test_worker_checks_current_power_before_any_ssh(
    monkeypatch: pytest.MonkeyPatch, current: object, reason: str, resource_type: str,
) -> None:
    lookup = Mock(side_effect=current) if isinstance(current, Exception) else Mock(return_value=current)
    connect, command = Mock(), Mock()
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lookup)
    monkeypatch.setattr(executor, "create_key_client", connect)
    monkeypatch.setattr(executor, "exec_command", command)

    result = executor._execute_scheduled_target(
        target=_target(resource_type=resource_type), script_content="pass",
        queued_at=0, on_start=lambda: None,
    )

    lookup.assert_called_once_with("pve1", 101, resource_type)
    connect.assert_not_called()
    command.assert_not_called()
    assert result["status"] == "failed"
    assert result["reason_code"] == reason
    assert result["failure_stage"] == "preparing"
    assert result["exit_code"] is None
    assert result["parsed_result"] is None
    assert result["raw_result_json"] == ""
    assert result["timing"]["finished_at"]
    assert "script_seconds" not in result["timing"]
    assert "cleanup_seconds" not in result["timing"]


def test_running_vm_with_ssh_timeout_is_not_reported_as_powered_off(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def status(*args):
        calls.append("power")
        return {"status": "running"}

    def connect(*args, **kwargs):
        calls.append("ssh")
        raise TimeoutError("SSH unavailable")

    monkeypatch.setattr(executor.proxmox_ops, "get_status", status)
    monkeypatch.setattr(executor, "create_key_client", connect)
    result = executor._execute_scheduled_target(
        target=_target(), script_content="pass", queued_at=0, on_start=lambda: None,
    )
    assert calls == ["power", "ssh"]
    assert result["reason_code"] == "ssh_connection_timeout"


@pytest.mark.parametrize("metadata", [{"proxmox_node": None}, {"resource_type": "storage"}])
def test_missing_power_lookup_metadata_never_opens_ssh(monkeypatch: pytest.MonkeyPatch, metadata) -> None:
    lookup, connect = Mock(), Mock()
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lookup)
    monkeypatch.setattr(executor, "create_key_client", connect)
    result = executor._execute_scheduled_target(
        target=_target(**metadata), script_content="pass", queued_at=0, on_start=lambda: None,
    )
    assert result["reason_code"] == "vm_status_unavailable"
    lookup.assert_not_called()
    connect.assert_not_called()


def test_runtime_power_off_precedes_os_ip_and_key_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    owner = uuid.uuid4()
    resource = SimpleNamespace(user_id=owner, environment_type="windows")
    monkeypatch.setattr(executor.resource_repo, "get_resource_by_vmid", lambda **_: resource)
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lambda *args: {"status": "stopped"})
    probes = [Mock(), Mock(), Mock()]
    monkeypatch.setattr(executor.os_identity_service, "ensure_guest_os", probes[0])
    monkeypatch.setattr(executor, "resolve_target_ip_address", probes[1])
    monkeypatch.setattr(executor, "decrypt_value", probes[2])
    with pytest.raises(executor.TargetExecutionError) as caught:
        executor._resolve_runtime_target(
            session=None, run=SimpleNamespace(id=uuid.uuid4()),
            target={"vmid": 101, "user_id": str(owner)},
            live_by_vmid={101: {"status": "stopped", "type": "qemu", "node": "pve1"}},
        )
    assert caught.value.reason_code == "vm_powered_off"
    for probe in probes:
        probe.assert_not_called()


@pytest.fixture
def prepared(monkeypatch: pytest.MonkeyPatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        artifact = TeacherJudgeScriptArtifact(
            teaching_class_id=class_id, name="power", template_key="linux",
            script_content="pass", status=TeacherJudgeScriptStatus.approved,
            rubric_snapshot_json={"target_node_key": "web", "items": []},
        )
        activate_script(db, artifact)
        members = {}
        for vmid in (101, 102):
            owner = uuid.uuid4()
            members[vmid] = {
                "vmid": vmid, "student_id": str(uuid.uuid4()), "user_id": str(owner),
                "email": f"s{vmid}@example.com", "full_name": str(vmid), "node_key": "web",
            }
            db.add(Resource(
                vmid=vmid, user_id=owner, environment_type="linux",
                ssh_private_key_encrypted="encrypted",
                created_at=get_datetime_utc(),
            ))
        db.commit()
        live = {vmid: {"vmid": vmid, "status": "running", "type": "qemu", "node": "pve1"} for vmid in members}
        monkeypatch.setattr(executor.proxmox_ops, "get_status", lambda node, vmid, kind: {"status": live[vmid]["status"]})
        monkeypatch.setattr(service, "_class_member_by_vmid", lambda **_: members)
        monkeypatch.setattr(service, "_running_resources_by_vmid", lambda: live)
        monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: live)
        monkeypatch.setattr(executor, "engine", db.get_bind())
        monkeypatch.setattr(executor, "decrypt_value", lambda _: "KEY")
        monkeypatch.setattr(service.os_identity_service, "ensure_guest_os", lambda **_: None)
        monkeypatch.setattr(service, "resolve_target_ip_address", lambda **_: "192.0.2.1")
        monkeypatch.setattr(executor, "resolve_target_ip_address", lambda **_: "192.0.2.1")
        yield db, artifact, members, live


def _create(db: Session, artifact: TeacherJudgeScriptArtifact):
    return service.create_script_run(
        session=db, teaching_class_id=artifact.teaching_class_id, artifact_id=artifact.id,
        target_scope=TeacherJudgeScriptRunTargetScope.manual, target_vmids=[101, 102], started_by=None,
    )


@pytest.mark.parametrize("all_off", [False, True])
def test_preflight_power_off_keeps_results_and_other_targets_running(
    monkeypatch: pytest.MonkeyPatch, prepared, all_off: bool,
) -> None:
    db, artifact, members, live = prepared
    live[101]["status"] = "stopped"
    if all_off:
        live[102]["status"] = "stopped"
    executed = []

    def remote(*, target, script_content):
        executed.append(target["vmid"])
        return executor.RemoteScriptResult(0, json.dumps({
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "2026-10-16T00:00:00Z", "platform": "linux"},
            "checks": [{"id": "check", "title": "Check", "status": "pass"}], "errors": [],
        }), "")

    monkeypatch.setattr(executor, "_execute_target_script", remote)
    public = _create(db, artifact)
    asyncio.run(executor.execute_script_run(uuid.UUID(public.id)))
    db.expire_all()
    run = db.get(TeacherJudgeScriptRun, uuid.UUID(public.id))
    targets = {target["vmid"]: target for target in run.results_document["targets"]}
    assert executed == ([] if all_off else [102])
    assert len(targets) == 2
    assert targets[101]["reason_code"] == "vm_powered_off"
    assert targets[101]["timing"]["finished_at"]
    assert targets[102]["status"] == ("failed" if all_off else "completed")
    assert run.status.value == "completed"
    assert run.progress_json["done"] == run.progress_json["total"] == 2
    assert run.result_summary_json["failed"] == (2 if all_off else 1)
    assert batch_status([run]) == "completed_with_failures"
    progress = {row["vmid"]: row for row in run.progress_json["targets"]}
    assert progress[101]["reason_code"] == "vm_powered_off"
    page = service.list_run_targets(db, run, skip=0, limit=10, status="failed")
    assert page.total == (2 if all_off else 1)
    assert page.data[0].reason_code == "vm_powered_off"


@pytest.mark.parametrize("unavailable", [False, True])
def test_power_off_after_selection_is_caught_at_real_ssh_boundary(
    monkeypatch: pytest.MonkeyPatch, prepared, unavailable: bool,
) -> None:
    db, artifact, members, live = prepared
    public = _create(db, artifact)
    lookup = Mock(side_effect=TimeoutError("PVE unavailable")) if unavailable else Mock(return_value={"status": "stopped"})
    connect = Mock()
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lookup)
    monkeypatch.setattr(executor, "create_key_client", connect)
    asyncio.run(executor.execute_script_run(uuid.UUID(public.id)))
    db.expire_all()
    run = db.get(TeacherJudgeScriptRun, uuid.UUID(public.id))
    assert lookup.call_count == 2
    connect.assert_not_called()
    reason = "vm_status_unavailable" if unavailable else "vm_powered_off"
    assert {row.reason_code for row in run.target_records} == {reason}
    assert {row["reason_code"] for row in run.progress_json["targets"]} == {reason}
    assert all(row.to_result()["failure_stage"] == "preparing" for row in run.target_records)


def test_stale_stopped_list_does_not_skip_a_vm_that_has_started(monkeypatch: pytest.MonkeyPatch, prepared) -> None:
    db, artifact, members, live = prepared
    live[101]["status"] = "stopped"
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lambda *args: {"status": "running"})
    public = _create(db, artifact)
    assert len(public.target_snapshot_json["targets"]) == 2
    assert public.target_snapshot_json["preflight_results"] == []
    runtime = executor._resolve_runtime_target(
        session=db, run=SimpleNamespace(id=public.id),
        target=public.target_snapshot_json["targets"][0], live_by_vmid=live,
    )
    assert runtime["host"] == "192.0.2.1"


def test_stopped_snapshot_with_failed_current_lookup_is_not_labeled_powered_off(monkeypatch: pytest.MonkeyPatch, prepared) -> None:
    db, artifact, members, live = prepared
    live[101]["status"] = "stopped"
    monkeypatch.setattr(executor.proxmox_ops, "get_status", Mock(side_effect=TimeoutError("PVE unavailable")))
    public = _create(db, artifact)
    assert public.target_snapshot_json["preflight_results"][0]["reason_code"] == "vm_status_unavailable"


def test_runtime_power_lookup_uses_the_current_node_after_migration(monkeypatch: pytest.MonkeyPatch, prepared) -> None:
    db, artifact, members, live = prepared
    public = _create(db, artifact)
    live[101]["node"] = "pve2"
    target = executor._resolve_runtime_target(
        session=db, run=SimpleNamespace(id=public.id),
        target=public.target_snapshot_json["targets"][0], live_by_vmid=live,
    )
    lookup = Mock(return_value={"status": "stopped"})
    monkeypatch.setattr(executor.proxmox_ops, "get_status", lookup)
    with pytest.raises(executor.TargetExecutionError):
        executor.check_current_target_power(target)
    lookup.assert_called_once_with("pve2", 101, "qemu")


def test_node_scope_preserves_power_off_reason_and_skips_guest_probes(monkeypatch: pytest.MonkeyPatch, prepared) -> None:
    db, artifact, members, live = prepared
    live[101]["status"] = live[102]["status"] = "stopped"
    monkeypatch.setattr(service, "resolve_class_machine_node", lambda *args: SimpleNamespace(node_key="web"))
    monkeypatch.setattr(service, "_class_member_by_node_key", lambda **_: {"web": list(members.values())})
    probe = Mock()
    monkeypatch.setattr(service.os_identity_service, "ensure_guest_os", probe)
    monkeypatch.setattr(service, "resolve_target_ip_address", probe)
    targets, failures = service._resolve_node_targets(
        session=db, teaching_class_id=artifact.teaching_class_id, target_node_key="web",
    )
    assert targets == []
    assert len(failures) == 2
    assert {row["reason_code"] for row in failures} == {"vm_powered_off"}
    probe.assert_not_called()


@pytest.mark.parametrize("invalid", ["class", "owner", "node"])
def test_stopped_manual_targets_still_enforce_membership_owner_and_node(
    monkeypatch: pytest.MonkeyPatch, prepared, invalid: str,
) -> None:
    db, artifact, members, live = prepared
    live[101]["status"] = "stopped"
    if invalid == "class":
        del members[101]
    elif invalid == "owner":
        members[101]["user_id"] = str(uuid.uuid4())
    else:
        members[101]["node_key"] = "db"
    with pytest.raises(HTTPException) as caught:
        _create(db, artifact)
    assert caught.value.status_code == 400
