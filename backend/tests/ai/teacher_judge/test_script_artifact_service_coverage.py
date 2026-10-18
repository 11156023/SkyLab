"""Coverage for app.ai.teacher_judge.script_artifact_service error and lifecycle paths."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import script_artifact_service as svc
from app.ai.teacher_judge.deterministic_compiler import CheckPlanContractError
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import make_script_workspace, make_session


def _item(item_id: str, node_key: str | None = "web") -> TeacherJudgeRubricItem:
    return TeacherJudgeRubricItem(
        id=item_id,
        title=item_id,
        detectable="auto",
        detection_method="受控檢查",
        target_node_key=node_key,
        check_steps=[
            TeacherJudgeRubricCheckStep(
                id=f"{item_id}.check",
                title=f"{item_id} check",
                collector={
                    "type": "command",
                    "argv": ["systemctl", "is-active", "nginx"],
                    "timeout_seconds": 30,
                },
                assertion={"type": "returncode_equals", "expected": 0},
            )
        ],
    )


def _add_node(session: Session, class_id: uuid.UUID, node_key: str = "web") -> None:
    session.add(
        TeachingClassMachineNode(
            class_id=class_id,
            node_key=node_key,
            name="Web",
            role="frontend",
            resource_type="lxc",
            cpu=1,
            memory_mb=512,
            disk_gb=8,
            sort_order=0,
        )
    )
    session.commit()


def _artifact(
    class_id: uuid.UUID,
    *,
    status: TeacherJudgeScriptStatus = TeacherJudgeScriptStatus.approved,
    set_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    node_key: str | None = "web",
    version: int = 1,
    policy: dict[str, Any] | None = None,
    review: dict[str, Any] | None = None,
) -> TeacherJudgeScriptArtifact:
    return TeacherJudgeScriptArtifact(
        artifact_set_id=set_id,
        target_node_key=node_key,
        teaching_class_id=class_id,
        session_id=session_id,
        name="script",
        template_key="linux",
        rubric_snapshot_json={"items": []},
        source_file_snapshot_json={},
        script_content="print(1)",
        source=TeacherJudgeScriptSource.ai_generated,
        status=status,
        version=version,
        policy_check_result_json=policy if policy is not None else {"approved": True},
        ai_review_result_json=review if review is not None else {"approved": True},
    )


def _persist(session: Session, *rows: TeacherJudgeScriptArtifact) -> None:
    session.add_all(rows)
    session.commit()
    for row in rows:
        session.refresh(row)


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the heavy collaborators of create_artifact_set to controllable fakes."""
    state: dict[str, Any] = {
        "commands": [],
        "peer_keys": set(),
        "issues": [],
        "canonical_error": None,
        "compile": lambda partition, target_node_key: (
            "print('ok')",
            {"approved": True},
            {"approved": True},
            None,
        ),
        "peer_policy": {"approved": True},
    }
    monkeypatch.setattr(
        svc, "get_enabled_template_commands", lambda *a, **k: state["commands"]
    )
    monkeypatch.setattr(svc, "ensure_script_generation_supported", lambda *a, **k: None)
    monkeypatch.setattr(
        svc, "peer_node_keys_from_snapshot", lambda snapshot: state["peer_keys"]
    )
    monkeypatch.setattr(svc, "rubric_item_machine_issues", lambda item: state["issues"])

    def fake_canonical(analysis: Any) -> None:
        if state["canonical_error"] is not None:
            raise state["canonical_error"]

    monkeypatch.setattr(svc, "canonicalize_check_plan", fake_canonical)
    monkeypatch.setattr(
        svc,
        "compile_check_plan",
        lambda partition, target_node_key: state["compile"](partition, target_node_key),
    )
    monkeypatch.setattr(
        svc, "check_peer_runtime_policy", lambda content, snap: state["peer_policy"]
    )
    monkeypatch.setattr(svc, "source_file_snapshot", lambda **_: (None, {}))
    return state


def _create(
    session: Session,
    class_id: uuid.UUID,
    *,
    name: str = "rubric",
    analysis: TeacherJudgeRubricAnalysis | None = None,
    session_id: uuid.UUID | None = None,
    artifact_set_id: uuid.UUID | None = None,
    source_file_id: uuid.UUID | None = None,
):
    workspace = make_script_workspace(session, class_id, session_id=session_id, source_file_id=source_file_id)
    return svc.create_artifact_set(
        session=session,
        teaching_class_id=class_id,
        session_id=workspace.id,
        name=name,
        template_key="linux",
        rubric_analysis=analysis or TeacherJudgeRubricAnalysis(items=[_item("a")]),
        source_analysis_revision=1,
        created_by=None,
        source_file_id=source_file_id,
        artifact_set_id=artifact_set_id,
    )


# ------------------------------------------------------------- small helpers


def test_template_command_catalog_snapshot_helpers() -> None:
    assert svc._snapshot_uses_legacy_command_references({"items": "nope"}) is False
    command = SimpleNamespace(
        command_key="k",
        command_label="L",
        category="c",
        command_template="echo",
        description="d",
        risk_level="low",
        requires_confirmation=False,
    )
    legacy = {"items": [{"check_steps": [{"command_key": "k"}]}]}
    snapshot = svc._with_template_command_catalog(legacy, [command])
    assert snapshot["template_commands"][0]["command_key"] == "k"
    plain = svc._with_template_command_catalog({"items": []}, [command])
    assert "template_commands" not in plain


def test_partition_requires_target_node() -> None:
    with pytest.raises(HTTPException) as exc:
        svc.partition_analysis_by_target_node(
            TeacherJudgeRubricAnalysis(items=[_item("x", node_key=None)])
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["item_ids"] == ["x"]


def test_script_set_to_public_without_set_id_raises() -> None:
    row = _artifact(uuid.uuid4(), set_id=None)
    with pytest.raises(HTTPException) as exc:
        svc._script_set_to_public([row])
    assert exc.value.status_code == 404


# ------------------------------------------------------------- list / get sets


def test_list_artifacts_and_get_artifact_set_filter_by_session() -> None:
    session = make_session()
    class_id = uuid.uuid4()
    sid = uuid.uuid4()
    set_id = uuid.uuid4()
    _persist(
        session,
        _artifact(class_id, set_id=set_id, session_id=sid),
        _artifact(class_id, session_id=uuid.uuid4()),
    )
    listed = svc.list_artifacts(
        session=session, teaching_class_id=class_id, session_id=sid
    )
    assert len(listed) == 1

    result = svc.get_artifact_set(
        session=session,
        teaching_class_id=class_id,
        artifact_set_id=set_id,
        session_id=sid,
    )
    assert result.artifact_set_id == str(set_id)

    with pytest.raises(HTTPException) as exc:
        svc.get_artifact_set(
            session=session,
            teaching_class_id=class_id,
            artifact_set_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404


def test_list_artifact_sets_skips_rows_without_set_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class_id = uuid.uuid4()
    set_id = uuid.uuid4()
    rows = [
        _artifact(class_id, set_id=None),
        _artifact(class_id, set_id=set_id),
    ]
    for row in rows:
        row.id = uuid.uuid4()
        row.created_at = row.updated_at = svc._now()

    class _FakeSession:
        def get(self, *args):
            return None

        def exec(self, _statement: Any) -> Any:
            return SimpleNamespace(all=lambda: rows)

    monkeypatch.setattr(svc, "load_class_machine_nodes", lambda s, c: [])
    result = svc.list_artifact_sets(
        session=_FakeSession(),  # type: ignore[arg-type]
        teaching_class_id=class_id,
        session_id=uuid.uuid4(),
    )
    assert [r.artifact_set_id for r in result] == [str(set_id)]


# ------------------------------------------------------------- create set


def test_create_artifact_set_blank_name(patched: dict[str, Any]) -> None:
    with pytest.raises(HTTPException) as exc:
        _create(make_session(), uuid.uuid4(), name="   ")
    assert exc.value.status_code == 400


def test_create_artifact_set_invalid_node_keys(patched: dict[str, Any]) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    patched["peer_keys"] = {"ghost"}
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id)
    assert exc.value.status_code == 422
    assert exc.value.detail["target_node_keys"] == ["ghost"]


def test_create_artifact_set_machine_contract_issues(patched: dict[str, Any]) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    patched["issues"] = ["peer mismatch"]
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id)
    assert exc.value.detail["code"] == "teacher_judge_machine_contract_invalid"


def test_create_artifact_set_canonicalize_error(patched: dict[str, Any]) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    patched["canonical_error"] = CheckPlanContractError([{"message": "bad"}])
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id)
    assert exc.value.detail["code"] == "teacher_judge_check_plan_invalid"
    assert exc.value.detail["issues"] == [{"message": "bad"}]


def test_create_artifact_set_peer_policy_rejected(patched: dict[str, Any]) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    patched["peer_policy"] = {"approved": False, "issues": ["peer"]}
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id)
    assert exc.value.status_code == 422
    assert exc.value.detail["target_node_key"] == "web"


def test_create_artifact_set_legacy_steps_keep_template_catalog(
    patched: dict[str, Any],
) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    patched["commands"] = [
        SimpleNamespace(
            command_key="svc",
            command_label="Service",
            category="system",
            command_template="systemctl is-active {name}",
            description="",
            risk_level="low",
            requires_confirmation=False,
        )
    ]
    item = _item("legacy")
    item.check_steps = [
        TeacherJudgeRubricCheckStep(template_key="linux", command_key="svc")
    ]
    result = _create(
        session, class_id, analysis=TeacherJudgeRubricAnalysis(items=[item])
    )
    snapshot = result.children[0].rubric_snapshot_json
    assert snapshot["template_key"] == "linux"
    assert snapshot["template_commands"][0]["command_key"] == "svc"


def test_create_artifact_set_regenerate_missing_previous(
    patched: dict[str, Any],
) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id, artifact_set_id=uuid.uuid4())
    assert exc.value.status_code == 404


def test_create_artifact_set_regenerate_context_mismatch(
    patched: dict[str, Any],
) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    _add_node(session, class_id)
    set_id = uuid.uuid4()
    _persist(session, _artifact(class_id, set_id=set_id, session_id=uuid.uuid4()))
    with pytest.raises(HTTPException) as exc:
        _create(session, class_id, artifact_set_id=set_id, session_id=uuid.uuid4())
    assert exc.value.status_code == 409


# ------------------------------------------------------------- approve / rename


@pytest.mark.parametrize(
    ("status", "policy", "review"),
    [
        (TeacherJudgeScriptStatus.approved, {"approved": True}, {"approved": True}),
        (TeacherJudgeScriptStatus.reviewed, {"approved": False}, {"approved": True}),
        (TeacherJudgeScriptStatus.reviewed, {"approved": True}, {"approved": False}),
    ],
)
def test_approve_artifact_rejections(
    status: TeacherJudgeScriptStatus,
    policy: dict[str, Any],
    review: dict[str, Any],
) -> None:
    session = make_session()
    class_id = uuid.uuid4()
    row = _artifact(class_id, status=status, policy=policy, review=review)
    _persist(session, row)
    with pytest.raises(HTTPException) as exc:
        svc.approve_artifact(
            session=session,
            teaching_class_id=class_id,
            artifact_id=row.id,
            approved_by=None,
        )
    assert exc.value.status_code == 400


def test_rename_artifact_blank_name() -> None:
    session = make_session()
    class_id = uuid.uuid4()
    row = _artifact(class_id)
    _persist(session, row)
    with pytest.raises(HTTPException) as exc:
        svc.rename_artifact(
            session=session,
            teaching_class_id=class_id,
            artifact_id=row.id,
            name="  ",
        )
    assert exc.value.status_code == 400
