"""Coverage tests for app.ai.teacher_judge.script_run_service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import script_run_service as svc
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import activate_script, make_session


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class _QueueSession:
    """Fake session whose ``exec`` returns queued result rows in order."""

    def __init__(
        self,
        *results: list[Any],
        artifact: TeacherJudgeScriptArtifact | None = None,
    ) -> None:
        self._results = list(results)
        self._artifact = artifact
        self.added: list[Any] = []
        self.flushed = False
        self.committed = False

    def exec(self, _statement: Any) -> _Result:
        return _Result(self._results.pop(0))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def refresh(self, obj: Any, *_args: Any, **_kwargs: Any) -> None:
        if isinstance(obj, TeacherJudgeScriptRun) and self._artifact is not None:
            obj.artifact = self._artifact

    def commit(self) -> None:
        self.committed = True

    def flush(self) -> None:
        self.flushed = True


def _artifact(**overrides: Any) -> TeacherJudgeScriptArtifact:
    values: dict[str, Any] = {
        "teaching_class_id": uuid.uuid4(),
        "name": "script",
        "template_key": "linux",
        "rubric_snapshot_json": {"items": []},
        "script_content": "print('{}')",
        "status": TeacherJudgeScriptStatus.approved,
    }
    values.update(overrides)
    return TeacherJudgeScriptArtifact(**values)


def _run(
    artifact: TeacherJudgeScriptArtifact, **overrides: Any
) -> TeacherJudgeScriptRun:
    run = TeacherJudgeScriptRun(artifact_id=artifact.id, **overrides)
    run.artifact = artifact
    return run


def _node(
    class_id: uuid.UUID, key: str, sort_order: int = 0
) -> TeachingClassMachineNode:
    return TeachingClassMachineNode(
        class_id=class_id,
        node_key=key,
        name=f"{key}-name",
        role="role",
        resource_type="lxc",
        cpu=1,
        memory_mb=512,
        disk_gb=8,
        sort_order=sort_order,
    )


# ---------------------------------------------------------------- helpers


def test_peer_ip_helpers_handle_every_shape() -> None:
    assert svc._peer_ips_from_target("x") == set()
    assert svc._peer_ips_from_target({"runtime_context": "x"}) == set()
    assert svc._peer_ips_from_target(
        {
            "runtime_context": {
                "peers": {
                    "a": {"ip_address": " 10.0.0.1 "},
                    "b": {"ip_address": "  "},
                    "c": {"ip_address": 5},
                    "d": "nope",
                }
            }
        }
    ) == {"10.0.0.1"}
    assert svc._peer_ips_from_snapshot("x") == set()
    assert svc._peer_ips_from_snapshot({"targets": "x"}) == set()
    assert svc._peer_ips_from_snapshot(
        {"targets": [{"runtime_context": {"peers": {"a": {"ip_address": "1.2.3.4"}}}}]}
    ) == {"1.2.3.4"}


def test_redact_peer_ips_walks_nested_values() -> None:
    assert svc._redact_peer_ips("10.0.0.1", set()) == "10.0.0.1"
    assert svc._redact_peer_ips(
        {"a": ["ping 10.0.0.1", 3], "b": None}, {"10.0.0.1"}
    ) == {"a": ["ping <peer-ip>", 3], "b": None}


def test_peer_resolution_for_target_branches() -> None:
    assert svc._peer_resolution_for_target("x", {}) == {}
    assert svc._peer_resolution_for_target({"targets": "x"}, {}) == {}
    snapshot = {
        "targets": [
            "not-a-dict",
            {"student_id": "other", "vmid": 1},
            {"student_id": "s1", "vmid": 2},
            {"student_id": "s1", "vmid": 1, "runtime_context": "bad"},
        ]
    }
    assert (
        svc._peer_resolution_for_target(snapshot, {"student_id": "s1", "vmid": 1}) == {}
    )
    found = {
        "targets": [
            {
                "student_id": "s1",
                "vmid": 1,
                "runtime_context": {
                    "peers": {"db": {"resolution_status": "ready"}, "x": "bad"}
                },
            }
        ]
    }
    assert svc._peer_resolution_for_target(found, {"student_id": "s1", "vmid": 1}) == {
        "db": {"resolution_status": "ready"}
    }
    assert svc._peer_resolution_for_target(found, {"student_id": "s9"}) == {}


def test_public_payload_helpers_reject_non_dicts() -> None:
    assert svc._public_target("x") == {}
    assert svc._public_targets_payload("x") == {}
    assert svc._public_target_snapshot("x") == {}
    snapshot = svc._public_target_snapshot(
        {
            "script": {"id": "1", "template_key": "linux"},
            "targets": [{"vmid": 1, "email": "a"}],
            "preflight_results": [{"host": "h", "status": "failed"}],
        }
    )
    assert snapshot["script"] == {"id": "1"}
    assert snapshot["targets"] == [{"email": "a"}]
    assert snapshot["preflight_results"] == [{"status": "failed"}]


def test_get_script_run_public_not_found_and_found() -> None:
    artifact = _artifact()
    run = _run(artifact)
    session = MagicMock()
    session.get.return_value = None
    with pytest.raises(HTTPException) as exc:
        svc.get_script_run_public(
            session=session,
            teaching_class_id=artifact.teaching_class_id,
            artifact_id=artifact.id,
            run_id=run.id,
        )
    assert exc.value.status_code == 404

    session.get.return_value = run
    with pytest.raises(HTTPException):
        svc.get_script_run_public(
            session=session,
            teaching_class_id=uuid.uuid4(),
            artifact_id=artifact.id,
            run_id=run.id,
        )
    public = svc.get_script_run_public(
        session=session,
        teaching_class_id=artifact.teaching_class_id,
        artifact_id=artifact.id,
        run_id=run.id,
    )
    assert public.id == str(run.id)


# ---------------------------------------------------------- teacher review


def _review_run(status: TeacherJudgeScriptRunStatus) -> TeacherJudgeScriptRun:
    artifact = _artifact()
    return _run(
        artifact,
        status=status,
        target_results_json={
            "targets": [
                {
                    "vmid": 101,
                    "student_id": "s1",
                    "status": status.value,
                    "parsed_result": {"checks": [{"id": "c1", "status": "warning"}]},
                    "teacher_review": {"feedback": "old"},
                }
            ]
        },
    )


def test_update_target_review_rejects_incomplete_run() -> None:
    run = _review_run(TeacherJudgeScriptRunStatus.running)
    with pytest.raises(HTTPException) as exc:
        svc.update_target_review(
            MagicMock(),
            run,
            vmid=101,
            feedback="x",
            decisions={},
            reviewer_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 409


def test_update_target_review_unknown_target_is_404() -> None:
    run = _review_run(TeacherJudgeScriptRunStatus.completed)
    with pytest.raises(HTTPException) as exc:
        svc.update_target_review(
            MagicMock(),
            run,
            student_id="missing",
            feedback="x",
            decisions={},
            reviewer_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------- class members


def test_class_member_by_vmid_without_enrollments() -> None:
    assert (
        svc._class_member_by_vmid(
            session=_QueueSession([]), teaching_class_id=uuid.uuid4()
        )
        == {}
    )


def test_class_member_by_vmid_skips_incomplete_rows() -> None:
    class_id = uuid.uuid4()
    node = _node(class_id, "web")
    user = SimpleNamespace(id=uuid.uuid4(), email="a@x", full_name="A")
    enrollment = SimpleNamespace(id=uuid.uuid4(), user_id=user.id)
    orphan_enrollment = SimpleNamespace(id=uuid.uuid4(), user_id=uuid.uuid4())
    machines = [
        SimpleNamespace(class_student_id=uuid.uuid4(), vmid=1, machine_node_id=node.id),
        SimpleNamespace(
            class_student_id=enrollment.id, vmid=None, machine_node_id=None
        ),
        SimpleNamespace(
            class_student_id=orphan_enrollment.id, vmid=2, machine_node_id=node.id
        ),
        SimpleNamespace(
            class_student_id=enrollment.id, vmid=3, machine_node_id=node.id
        ),
        SimpleNamespace(
            class_student_id=enrollment.id, vmid=4, machine_node_id=uuid.uuid4()
        ),
    ]
    session = _QueueSession([enrollment, orphan_enrollment], [user], [node], machines)
    result = svc._class_member_by_vmid(session=session, teaching_class_id=class_id)
    assert set(result) == {3, 4}
    assert result[3]["node_key"] == "web"
    assert result[3]["display_label"] == "P1"
    assert "node_key" not in result[4]


def test_class_member_by_node_key_groups_and_sorts() -> None:
    assert (
        svc._class_member_by_node_key(
            session=_QueueSession([]), teaching_class_id=uuid.uuid4()
        )
        == {}
    )
    class_id = uuid.uuid4()
    node = _node(class_id, "web")
    user = SimpleNamespace(id=uuid.uuid4(), email="a@x", full_name="A")
    enrollment_b = SimpleNamespace(id=uuid.UUID(int=2), user_id=user.id)
    enrollment_a = SimpleNamespace(id=uuid.UUID(int=1), user_id=user.id)
    orphan = SimpleNamespace(id=uuid.UUID(int=3), user_id=uuid.uuid4())
    machines = [
        SimpleNamespace(
            class_student_id=uuid.uuid4(), machine_node_id=node.id, vmid=9, status="x"
        ),
        SimpleNamespace(
            class_student_id=enrollment_a.id,
            machine_node_id=uuid.uuid4(),
            vmid=8,
            status="x",
        ),
        SimpleNamespace(
            class_student_id=orphan.id, machine_node_id=node.id, vmid=7, status="x"
        ),
        SimpleNamespace(
            class_student_id=enrollment_b.id,
            machine_node_id=node.id,
            vmid=None,
            status="pending",
        ),
        SimpleNamespace(
            class_student_id=enrollment_a.id,
            machine_node_id=node.id,
            vmid=5,
            status="completed",
        ),
    ]
    session = _QueueSession(
        [enrollment_b, enrollment_a, orphan], [user], [node], machines
    )
    result = svc._class_member_by_node_key(session=session, teaching_class_id=class_id)
    assert [member["vmid"] for member in result["web"]] == [5, None]


# ---------------------------------------------------------- target resolution


def _member(user_id: str = "u1", **extra: Any) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "email": "a@x",
        "full_name": "A",
        "student_id": "s1",
        **extra,
    }


def _resolve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    member: dict[str, Any] | None,
    live: dict[str, Any] | None,
    resource: Any = None,
    ip: str | None = "10.0.0.5",
) -> list[dict[str, Any]]:
    monkeypatch.setattr(
        svc.resource_repo, "get_resource_by_vmid", lambda *, session, vmid: resource
    )
    monkeypatch.setattr(svc.os_identity_service, "ensure_guest_os", lambda **_: None)
    monkeypatch.setattr(svc, "resolve_target_ip_address", lambda **_: ip)
    return svc._resolve_running_targets(
        session=MagicMock(),
        teaching_class_id=uuid.uuid4(),
        target_vmids=[101],
        member_by_vmid={101: member} if member else {},
        live_by_vmid={101: live} if live else {},
    )


def _resource(user_id: str = "u1", key: str | None = "enc") -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        ssh_private_key_encrypted=key,
        os_info="Ubuntu",
        environment_type="linux",
        guest_os=None,
    )


@pytest.mark.parametrize(
    ("member", "live", "resource", "ip", "key"),
    [
        (None, None, None, None, "run.vmid_not_in_class"),
        (_member(), None, None, None, "run.vmid_not_runnable"),
        (_member(), {"type": "pool", "status": "running"}, None, None, "x"),
        (_member(), {"type": "lxc", "status": "stopped"}, None, None, "x"),
        (_member(), {"type": "lxc", "status": "running"}, None, None, "x"),
        (
            _member(),
            {"type": "lxc", "status": "running"},
            _resource(user_id="other"),
            None,
            "x",
        ),
        (_member(), {"type": "lxc", "status": "running"}, _resource(), None, "x"),
    ],
)
def test_resolve_running_targets_rejections(
    monkeypatch: pytest.MonkeyPatch,
    member: Any,
    live: Any,
    resource: Any,
    ip: Any,
    key: str,
) -> None:
    monkeypatch.setattr(svc, "_ensure_linux_executor_capability", lambda *_: None)
    with pytest.raises(HTTPException) as exc:
        _resolve(monkeypatch, member=member, live=live, resource=resource, ip=ip)
    assert exc.value.status_code == 400


def test_resolve_node_targets_unknown_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "resolve_class_machine_node", lambda *_: None)
    with pytest.raises(HTTPException) as exc:
        svc._resolve_node_targets(
            session=MagicMock(), teaching_class_id=uuid.uuid4(), target_node_key="x"
        )
    assert exc.value.detail["code"] == "teacher_judge_target_node_not_in_class"


def test_resolve_node_targets_without_members(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _node(uuid.uuid4(), "web")
    monkeypatch.setattr(svc, "resolve_class_machine_node", lambda *_: node)
    monkeypatch.setattr(svc, "_class_member_by_node_key", lambda **_: {})
    with pytest.raises(HTTPException) as exc:
        svc._resolve_node_targets(
            session=MagicMock(), teaching_class_id=uuid.uuid4(), target_node_key="web"
        )
    assert exc.value.detail["code"] == "teacher_judge_node_has_no_students"


def test_resolve_node_targets_records_preflight_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = _node(uuid.uuid4(), "web")
    members = [
        _member(vmid=None, node_name="web"),
        _member(vmid=101, student_id="s2"),
        _member(vmid=102, student_id="s3"),
    ]
    monkeypatch.setattr(svc, "resolve_class_machine_node", lambda *_: node)
    monkeypatch.setattr(svc, "_class_member_by_node_key", lambda **_: {"web": members})
    monkeypatch.setattr(svc, "_running_resources_by_vmid", lambda: {})

    def fake_resolve(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs["target_vmids"] == [101]:
            raise HTTPException(status_code=400, detail="not running")
        return [{"vmid": 102}]

    monkeypatch.setattr(svc, "_resolve_running_targets", fake_resolve)
    targets, failures = svc._resolve_node_targets(
        session=MagicMock(), teaching_class_id=uuid.uuid4(), target_node_key="web"
    )
    assert targets == [{"vmid": 102}]
    assert [row["reason_code"] for row in failures] == [
        "missing_vmid",
        "target_not_ready",
    ]
    assert failures[0]["vmid"] is None and failures[0]["name"] == "web"


# ------------------------------------------------------------- peer context


def test_attach_peer_contexts_records_each_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    students = [f"s{index}" for index in range(10)]
    peer_members = [
        {"student_id": "s1", "vmid": None},
        {"student_id": "s2", "vmid": 202, "user_id": "u"},
        {"student_id": "s3", "vmid": 203, "user_id": "u"},
        {"student_id": "s4", "vmid": 204, "user_id": "u"},
        {"student_id": "s5", "vmid": 205, "user_id": "u"},
        {"student_id": "s6", "vmid": 206, "user_id": "u"},
        {"student_id": "s7", "vmid": 207, "user_id": "u"},
        {"student_id": "s8", "vmid": 208, "user_id": "u"},
        {"student_id": "s9", "vmid": 209, "user_id": "u"},
    ]
    live = {
        203: {"status": "running", "type": "pool"},
        204: {"status": "running", "type": "lxc"},
        205: {"status": "running", "type": "lxc"},
        206: {"status": "running", "type": "qemu"},
        207: {"status": "running", "type": "lxc"},
        208: {"status": "running", "type": "lxc"},
        209: {"status": "running", "type": "lxc"},
    }

    def fake_resource(*, session: Any, vmid: int) -> Any:
        if vmid == 202:
            raise RuntimeError("db down")
        if vmid == 204:
            return None
        if vmid == 205:
            return SimpleNamespace(user_id="other")
        return SimpleNamespace(user_id="u")

    def fake_ip(*, session: Any, vmid: int, live_resource: Any) -> str | None:
        if vmid == 207:
            raise RuntimeError("guest agent down")
        if vmid == 208:
            return None
        return f"10.0.0.{vmid - 200}"

    monkeypatch.setattr(
        svc, "_class_member_by_node_key", lambda **_: {"db": peer_members}
    )
    monkeypatch.setattr(svc, "_running_resources_by_vmid", lambda: live)
    monkeypatch.setattr(svc.resource_repo, "get_resource_by_vmid", fake_resource)
    monkeypatch.setattr(svc, "resolve_target_ip_address", fake_ip)

    targets: list[dict[str, Any]] = [{"student_id": sid} for sid in students]
    svc._attach_peer_runtime_contexts(
        session=MagicMock(),
        teaching_class_id=uuid.uuid4(),
        executor_node_key="web",
        peer_node_keys={"db"},
        targets=targets,
    )
    reasons = [
        target["runtime_context"]["peers"]["db"]["reason_code"] for target in targets
    ]
    assert reasons == [
        "peer_machine_missing",
        "peer_vmid_missing",
        "peer_not_running",
        "peer_resource_type_invalid",
        "peer_resource_missing",
        "peer_owner_mismatch",
        None,
        "peer_ip_unavailable",
        "peer_ip_unavailable",
        None,
    ]
    assert targets[9]["runtime_context"]["peers"]["db"]["ip_address"] == "10.0.0.9"


def test_attach_peer_contexts_tolerates_live_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> dict[int, dict[str, Any]]:
        raise HTTPException(status_code=503, detail="down")

    monkeypatch.setattr(
        svc,
        "_class_member_by_node_key",
        lambda **_: {"db": [{"student_id": "s1", "vmid": 1, "user_id": "u"}]},
    )
    monkeypatch.setattr(svc, "_running_resources_by_vmid", boom)
    monkeypatch.setattr(
        svc.resource_repo,
        "get_resource_by_vmid",
        lambda **_: SimpleNamespace(user_id="u"),
    )
    targets: list[dict[str, Any]] = [{"student_id": "s1"}]
    svc._attach_peer_runtime_contexts(
        session=MagicMock(),
        teaching_class_id=uuid.uuid4(),
        executor_node_key="web",
        peer_node_keys={"db"},
        targets=targets,
    )
    assert targets[0]["runtime_context"]["peers"]["db"]["reason_code"] == (
        "peer_not_running"
    )


# ------------------------------------------------------------ create run


def _create(
    monkeypatch: pytest.MonkeyPatch,
    artifact: TeacherJudgeScriptArtifact,
    **kwargs: Any,
) -> Any:
    monkeypatch.setattr(svc, "get_artifact", lambda **_: artifact)
    # These unit tests isolate target preparation; admission has integration tests.
    monkeypatch.setattr(svc, "require_active_artifact", lambda *args, **kwargs: None)
    session = kwargs.pop("session", None) or _QueueSession(artifact=artifact)
    return svc.create_script_run(
        session=session,
        teaching_class_id=artifact.teaching_class_id,
        artifact_id=artifact.id,
        started_by=None,
        **kwargs,
    )


def _target(vmid: int, node_key: str | None = "web") -> dict[str, Any]:
    return {
        "vmid": vmid,
        "name": str(vmid),
        "student_id": "s1",
        "node_key": node_key,
        "proxmox_node": "pve",
        "resource_type": "lxc",
        "user": {"id": "u"},
    }


def test_create_script_run_requires_approved_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(status=TeacherJudgeScriptStatus.draft)
    with pytest.raises(HTTPException) as exc:
        _create(
            monkeypatch,
            artifact,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=[1],
        )
    assert exc.value.status_code == 400


def test_create_script_run_rejects_mixed_and_mismatched_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mixed = _artifact(
        rubric_snapshot_json={
            "items": [{"target_node_key": "a"}, {"target_node_key": "b"}]
        }
    )
    with pytest.raises(HTTPException) as exc:
        _create(
            monkeypatch,
            mixed,
            target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
            target_vmids=None,
        )
    assert exc.value.detail["code"] == "teacher_judge_mixed_target_nodes"

    single = _artifact(rubric_snapshot_json={"target_node_key": "a"})
    with pytest.raises(HTTPException) as exc:
        _create(
            monkeypatch,
            single,
            target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
            target_vmids=None,
            target_node_key="b",
        )
    assert exc.value.detail["code"] == "teacher_judge_target_node_mismatch"


def test_create_script_run_node_scope_with_preflight_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(rubric_snapshot_json={"target_node_key": "web"})
    failure = {"vmid": None, "status": "failed", "reason_code": "missing_vmid"}
    monkeypatch.setattr(svc, "_resolve_node_targets", lambda **_: ([], [failure]))
    monkeypatch.setattr(svc, "_attach_peer_runtime_contexts", lambda **_: None)
    session = _QueueSession(artifact=artifact)
    public = _create(
        monkeypatch,
        artifact,
        session=session,
        target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
        target_vmids=None,
        commit=False,
    )
    assert session.flushed and not session.committed
    assert public.result_summary_json == {"preflight_failed": 1}
    target = public.target_results_json["targets"][0]
    assert target["status"] == failure["status"]
    assert target["reason_code"] == failure["reason_code"]


def test_create_script_run_legacy_vmids(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(rubric_snapshot_json={"items": []})
    monkeypatch.setattr(svc, "_resolve_running_targets", lambda **_: [_target(1)])
    public = _create(
        monkeypatch,
        artifact,
        target_scope=TeacherJudgeScriptRunTargetScope.all_with_vm,
        target_vmids=[1],
    )
    assert public.progress_json["targets"][0]["status"] == "queued"


def test_create_script_run_needs_some_target(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _artifact(rubric_snapshot_json={"items": []})
    with pytest.raises(HTTPException):
        _create(
            monkeypatch,
            artifact,
            target_scope=TeacherJudgeScriptRunTargetScope.all_with_vm,
            target_vmids=None,
        )
    monkeypatch.setattr(svc, "_resolve_running_targets", lambda **_: [])
    with pytest.raises(HTTPException) as exc:
        _create(
            monkeypatch,
            artifact,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=None,
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------- projection


def test_coverage_mapping_and_projection_tolerate_bad_shapes() -> None:
    artifact = _artifact(
        policy_check_result_json={
            "coverage": {
                "mappings": [
                    "bad",
                    {"check_id": "c1", "rubric_item_ids": ["i1", "i1"]},
                ]
            }
        },
        rubric_snapshot_json={"items": ["bad", {"id": "i1"}]},
    )
    assert svc._coverage_check_ids_by_item(artifact) == {"i1": ["c1"]}
    projected = svc.project_run_items(
        artifact=artifact,
        target_result={"status": "completed", "parsed_result": {"checks": "bad"}},
        display_labels={},
    )
    assert projected["items"][0]["missing_check_ids"] == ["c1"]


# ---------------------------------------------------------- batches (sqlite)


def _seed_batch(session: Session) -> tuple[TeacherJudgeScriptArtifact, uuid.UUID]:
    session_id = uuid.uuid4()
    artifact = _artifact(session_id=session_id, target_node_key="web")
    session.add(artifact)
    session.commit()
    batch_id = uuid.uuid4()
    session.add(
        TeacherJudgeScriptRun(
            artifact_id=artifact.id,
            run_batch_id=batch_id,
            status=TeacherJudgeScriptRunStatus.completed,
            target_results_json={
                "targets": [
                    {"status": "failed"},
                    {"student_id": "s1", "status": "completed"},
                    # 壞資料（非 dict）要被整段略過，不能讓彙總炸掉
                    "not-a-dict",
                ]
            },
        )
    )
    session.commit()
    return artifact, batch_id


def test_get_script_run_batch_public_filters_by_session() -> None:
    session = make_session()
    artifact, batch_id = _seed_batch(session)
    with pytest.raises(HTTPException) as exc:
        svc.get_script_run_batch_public(
            session=session,
            teaching_class_id=artifact.teaching_class_id,
            run_batch_id=batch_id,
            session_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404
    result = svc.get_script_run_batch_public(
        session=session,
        teaching_class_id=artifact.teaching_class_id,
        run_batch_id=batch_id,
        session_id=artifact.session_id,
    )
    assert result.summary["failed"] == 1
    assert result.summary["completed"] == 1
    assert result.summary["targets"] == 2
    assert [row["student_id"] for row in result.students] == ["s1"]


def test_create_script_run_batch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    session_id = uuid.uuid4()
    class_id = uuid.uuid4()
    artifact = _artifact(
        teaching_class_id=class_id, session_id=session_id, target_node_key="web"
    )
    session.add(artifact)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        svc.create_script_run_batch(
            session=session,
            teaching_class_id=class_id,
            artifact_set_id=uuid.uuid4(),
            started_by=None,
        )
    assert exc.value.status_code == 404

    set_id = uuid.uuid4()
    artifact.artifact_set_id = set_id
    activate_script(session, artifact)
    session.add(artifact)
    session.commit()
    with pytest.raises(HTTPException) as exc:
        svc.create_script_run_batch(
            session=session,
            teaching_class_id=class_id,
            artifact_set_id=set_id,
            started_by=None,
            session_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404

    artifact.status = TeacherJudgeScriptStatus.draft
    session.add(artifact)
    session.commit()
    with pytest.raises(HTTPException) as exc:
        svc.create_script_run_batch(
            session=session,
            teaching_class_id=class_id,
            artifact_set_id=set_id,
            started_by=None,
        )
    assert exc.value.status_code == 409

    artifact.status = TeacherJudgeScriptStatus.approved
    session.add(artifact)
    session.commit()

    def failing_create(**_: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_class_member_by_node_key", lambda **kwargs: {
        "web": [{"student_id": "student-1", "user_id": "user-1", "vmid": None, "node_key": "web"}],
    })
    create_record = svc._create_script_run_record
    monkeypatch.setattr(svc, "_create_script_run_record", failing_create)
    with pytest.raises(RuntimeError):
        svc.create_script_run_batch(
            session=session,
            teaching_class_id=class_id,
            artifact_set_id=set_id,
            started_by=None,
        )

    monkeypatch.setattr(svc, "_create_script_run_record", create_record)
    result = svc.create_script_run_batch(
        session=session,
        teaching_class_id=class_id,
        artifact_set_id=set_id,
        started_by=None,
        session_id=session_id,
    )
    assert result.status == "pending"
    assert result.session_id == str(session_id)
