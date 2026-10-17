"""Weekly publication, admission and immutable accepted-batch regressions."""

from __future__ import annotations

import importlib
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlmodel import select

from app.ai.teacher_judge import script_artifact_service as artifacts
from app.ai.teacher_judge import script_run_service as runs
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunTargetScope,
)
from app.models.teacher_judge_session import TeacherJudgeSession
from app.services.course import ai_assignment_service
from tests.ai.teacher_judge.helpers import (
    make_script_workspace,
    make_session,
    make_teacher_judge_file,
)
from tests.ai.teacher_judge.test_script_artifact_service_coverage import (
    _add_node,
    _item,
)


@pytest.fixture
def context(monkeypatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        _add_node(db, class_id, "web")
        _add_node(db, class_id, "db")
        file = make_teacher_judge_file(db, class_id)
        analysis = TeacherJudgeRubricAnalysis(
            items=[_item("service", "web"), _item("database", "db")]
        )
        file.analysis_json = analysis.model_dump(mode="json")
        db.add(file)
        db.commit()
        workspace = make_script_workspace(db, class_id, source_file_id=file.id)
        monkeypatch.setattr(
            artifacts, "get_enabled_template_commands", lambda *a, **k: []
        )

        def build(previous=None):
            return artifacts.create_artifact_set(
                session=db,
                teaching_class_id=class_id,
                session_id=workspace.id,
                name="Weekly",
                template_key="linux",
                rubric_analysis=analysis,
                source_analysis_revision=1,
                source_file_id=file.id,
                created_by=None,
                artifact_set_id=previous,
            )

        yield db, workspace, file, build


def test_success_switches_whole_set_and_keeps_approved_history(context):
    db, workspace, _, build = context
    first, second = build(), build()
    assert first.artifact_set_id != second.artifact_set_id
    db.refresh(workspace)
    assert str(workspace.active_artifact_set_id) == second.artifact_set_id
    assert len(db.exec(select(TeacherJudgeScriptArtifact)).all()) == 4
    assert {
        row.status.value for row in db.exec(select(TeacherJudgeScriptArtifact)).all()
    } == {"approved"}
    listed = artifacts.list_artifact_sets(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        session_id=workspace.id,
    )
    assert [item.artifact_set_id for item in listed if item.is_active] == [
        second.artifact_set_id
    ]
    old = db.get(TeacherJudgeScriptArtifact, uuid.UUID(first.children[0].id))
    with pytest.raises(HTTPException) as error:
        runs.create_script_run(
            session=db,
            teaching_class_id=workspace.teaching_class_id,
            artifact_id=old.id,
            target_scope=TeacherJudgeScriptRunTargetScope.manual,
            target_vmids=[101],
            started_by=None,
        )
    assert error.value.detail["code"] == "teacher_judge_script_set_not_active"
    with pytest.raises(HTTPException) as error:
        runs.create_script_run_batch(
            session=db,
            teaching_class_id=workspace.teaching_class_id,
            artifact_set_id=old.artifact_set_id,
            session_id=workspace.id,
            started_by=None,
        )
    assert error.value.status_code == 409
    assert db.exec(select(TeacherJudgeScriptRun)).first() is None


def test_regenerate_gets_new_identity_without_archiving_accepted_scripts(context):
    db, workspace, _, build = context
    first = build()
    second = build(uuid.UUID(first.artifact_set_id))
    assert second.artifact_set_id != first.artifact_set_id
    assert all(child.version == 2 for child in second.children)
    assert all(
        db.get(TeacherJudgeScriptArtifact, uuid.UUID(child.id)).status.value
        == "approved"
        for child in first.children
    )
    assert len(artifacts.active_set_children(db, workspace)) == 2


def test_one_child_failure_leaves_previous_set_intact(context, monkeypatch):
    db, workspace, _, build = context
    first = build()
    compiler = artifacts.compile_check_plan

    def compile_partition(partition, *, target_node_key):
        if target_node_key == "db":
            raise RuntimeError("compiler failure")
        return compiler(partition, target_node_key=target_node_key)

    monkeypatch.setattr(artifacts, "compile_check_plan", compile_partition)
    with pytest.raises(HTTPException) as error:
        build()
    assert error.value.status_code == 502
    db.rollback()
    assert str(workspace.active_artifact_set_id) == first.artifact_set_id
    assert len(db.exec(select(TeacherJudgeScriptArtifact)).all()) == 2


def test_failed_compiler_review_cannot_publish(context, monkeypatch):
    db, workspace, _, build = context
    first = build()
    monkeypatch.setattr(
        artifacts,
        "compile_check_plan",
        lambda *a, **k: ("print(1)", {"approved": True}, {"approved": False}, None),
    )
    with pytest.raises(HTTPException) as error:
        build()
    assert error.value.status_code == 422
    assert str(workspace.active_artifact_set_id) == first.artifact_set_id
    assert len(db.exec(select(TeacherJudgeScriptArtifact)).all()) == 2


def test_single_run_rechecks_after_slow_target_preparation(context, monkeypatch):
    db, workspace, _, build = context
    first = build()

    def resolve(**kwargs):
        build()
        return [], [{"vmid": None, "status": "failed", "reason_code": "missing_vmid"}]

    monkeypatch.setattr(runs, "_resolve_node_targets", resolve)
    monkeypatch.setattr(runs, "_attach_peer_runtime_contexts", lambda **k: None)
    with pytest.raises(HTTPException) as error:
        runs.create_script_run(
            session=db,
            teaching_class_id=workspace.teaching_class_id,
            artifact_id=uuid.UUID(first.children[0].id),
            target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
            target_vmids=None,
            started_by=None,
        )
    assert error.value.status_code == 409
    assert db.exec(select(TeacherJudgeScriptRun)).first() is None


def test_source_draft_change_does_not_revoke_active_group(context):
    db, workspace, file, build = context
    first = build()
    file.analysis_revision += 1
    db.add(file)
    db.commit()
    [active] = [
        item
        for item in artifacts.list_artifact_sets(
            session=db,
            teaching_class_id=workspace.teaching_class_id,
            session_id=workspace.id,
        )
        if item.is_active
    ]
    assert active.artifact_set_id == first.artifact_set_id
    assert active.source_is_current is False
    assert len(artifacts.active_set_children(db, workspace)) == 2


@pytest.mark.parametrize(
    "change", ["source_revision", "active_set", "selected_file", "archived"]
)
def test_revalidate_authorities_after_compilation(context, monkeypatch, change):
    db, workspace, file, build = context
    first = build()
    original = artifacts.compile_check_plan
    changed = False

    def compile_partition(partition, *, target_node_key):
        nonlocal changed
        if not changed:
            changed = True
            if change == "source_revision":
                db.exec(
                    file.__table__.update()
                    .where(file.__table__.c.id == file.id)
                    .values(analysis_revision=2)
                )
            else:
                values = (
                    {"active_artifact_set_id": uuid.uuid4()}
                    if change == "active_set"
                    else {"selected_file_id": None}
                    if change == "selected_file"
                    else {"status": "archived"}
                )
                db.exec(
                    TeacherJudgeSession.__table__.update()
                    .where(TeacherJudgeSession.__table__.c.id == workspace.id)
                    .values(**values)
                )
            db.commit()
        return original(partition, target_node_key=target_node_key)

    monkeypatch.setattr(artifacts, "compile_check_plan", compile_partition)
    with pytest.raises(HTTPException) as error:
        build()
    assert error.value.status_code == 409
    db.rollback()
    assert len(db.exec(select(TeacherJudgeScriptArtifact)).all()) == 2
    if change != "active_set":
        assert str(workspace.active_artifact_set_id) == first.artifact_set_id


@pytest.mark.parametrize("stage", ["flush", "commit"])
def test_persistence_failure_rolls_back_children_and_active_pointer(context, monkeypatch, stage):
    db, workspace, _, build = context
    first = build()

    original_flush = db.flush

    def fail(*args, **kwargs):
        if stage == "flush" and not any(isinstance(row, TeacherJudgeScriptArtifact) for row in db.new):
            return original_flush(*args, **kwargs)
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, stage, fail)
    with pytest.raises(RuntimeError, match="commit failed"):
        build()
    assert str(workspace.active_artifact_set_id) == first.artifact_set_id
    assert len(db.exec(select(TeacherJudgeScriptArtifact)).all()) == 2


def test_active_and_accepted_artifacts_cannot_be_retired(context, monkeypatch):
    db, workspace, _, build = context
    first = build()
    artifact_id = uuid.UUID(first.children[0].id)
    for operation in (artifacts.archive_artifact, artifacts.delete_artifact):
        with pytest.raises(HTTPException) as error:
            operation(
                session=db,
                teaching_class_id=workspace.teaching_class_id,
                artifact_id=artifact_id,
            )
        assert error.value.detail["code"] == "teacher_judge_active_script_protected"

    target = {
        "vmid": 101,
        "name": "Student",
        "node_key": "web",
        "proxmox_node": "pve",
        "resource_type": "lxc",
        "user": {"id": str(uuid.uuid4())},
    }
    monkeypatch.setattr(runs, "_resolve_node_targets", lambda **k: ([target], []))
    monkeypatch.setattr(runs, "_attach_peer_runtime_contexts", lambda **k: None)
    runs.create_script_run(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        artifact_id=artifact_id,
        target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
        target_vmids=None,
        started_by=None,
    )
    build()
    for operation in (artifacts.archive_artifact, artifacts.delete_artifact):
        with pytest.raises(HTTPException) as error:
            operation(
                session=db,
                teaching_class_id=workspace.teaching_class_id,
                artifact_id=artifact_id,
            )
        assert error.value.detail["code"] == "teacher_judge_script_in_use"


def test_batch_pins_all_children_even_if_active_set_switches_during_preparation(
    context, monkeypatch
):
    db, workspace, _, build = context
    first = build()
    monkeypatch.setattr(
        runs,
        "_class_member_by_node_key",
        lambda **k: {node: [{"student_id": "student-1", "user_id": "user-1",
                            "vmid": None, "node_key": node}] for node in ("web", "db")},
    )
    batch = runs.create_script_run_batch(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        artifact_set_id=uuid.UUID(first.artifact_set_id),
        session_id=workspace.id,
        started_by=None,
    )
    assert batch.artifact_set_id == first.artifact_set_id
    assert len(batch.nodes) == 2
    build()
    runs.prepare_script_run_batch(db, uuid.UUID(batch.run_batch_id))
    assert str(workspace.active_artifact_set_id) != first.artifact_set_id
    child_ids = {uuid.UUID(child.id) for child in first.children}
    assert {
        run.artifact_id for run in db.exec(select(TeacherJudgeScriptRun)).all()
    } == child_ids
    assert len(artifacts.active_set_children(db, workspace)) == 2


def test_current_assignment_uses_pointer_even_when_history_is_newer(context):
    db, workspace, _, build = context
    first = build()
    build()
    workspace.active_artifact_set_id = uuid.UUID(first.artifact_set_id)
    db.add(workspace)
    db.commit()
    assert {
        str(row.id) for row in ai_assignment_service._current_scripts(db, workspace.id)
    } == {child.id for child in first.children}
    workspace.active_artifact_set_id = None
    db.commit()
    assert ai_assignment_service._current_scripts(db, workspace.id) == []


def test_current_queries_exclude_old_child_versions_with_reused_legacy_set_id(context):
    from app.models.teacher_judge_run_batch import TeacherJudgeRunBatch

    db, workspace, _, build = context
    current = build()
    child = db.get(TeacherJudgeScriptArtifact, uuid.UUID(current.children[0].id))
    values = child.model_dump(exclude={"id", "version", "status"})
    old = TeacherJudgeScriptArtifact(**values, version=0, status="archived")
    db.add(old)
    db.flush()
    old_batch = TeacherJudgeRunBatch(teaching_class_id=workspace.teaching_class_id, session_id=workspace.id, artifact_set_id=workspace.active_artifact_set_id)
    new_batch = TeacherJudgeRunBatch(teaching_class_id=workspace.teaching_class_id, session_id=workspace.id, artifact_set_id=workspace.active_artifact_set_id)
    db.add_all([old_batch, new_batch])
    db.flush()
    old_run = TeacherJudgeScriptRun(artifact_id=old.id, run_batch_id=old_batch.id)
    new_run = TeacherJudgeScriptRun(artifact_id=child.id, run_batch_id=new_batch.id)
    db.add_all([old_run, new_run])
    db.commit()
    listed = runs.list_session_run_summaries(db, workspace.teaching_class_id, workspace.id, 0, 20, workspace.active_artifact_set_id)
    assert [run.id for run in listed] == [str(new_run.id)]
    batches = runs.list_session_batch_summaries(db, workspace.teaching_class_id, workspace.id, 0, 20, workspace.active_artifact_set_id)
    assert [batch.run_batch_id for batch in batches] == [str(new_batch.id)]
    assert len(runs.list_session_run_summaries(db, workspace.teaching_class_id, workspace.id, 0, 20)) == 2
    assert len(runs.list_session_batch_summaries(db, workspace.teaching_class_id, workspace.id, 0, 20)) == 2


@pytest.mark.parametrize("entry", ["class", "session", "set"])
def test_all_http_execution_entries_reject_old_group(context, monkeypatch, entry):
    from types import SimpleNamespace

    from app.ai.teacher_judge.schemas import (
        TeacherJudgeScriptRunCreateRequest,
        TeacherJudgeScriptSetRunRequest,
    )
    from app.api.routes import teacher_judge_scripts, teacher_judge_sessions

    db, workspace, _, build = context
    first = build()
    build()
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *a: None)
    monkeypatch.setattr(teacher_judge_scripts, "_ensure_class_access", lambda **k: None)
    kwargs = {
        "teaching_class_id": workspace.teaching_class_id,
        "session": db,
        "current_user": SimpleNamespace(id=uuid.uuid4()),
    }
    payload = TeacherJudgeScriptRunCreateRequest(
        target_scope="manual", target_vmids=[101]
    )
    with pytest.raises(HTTPException) as error:
        if entry == "class":
            teacher_judge_scripts.create_class_teacher_judge_script_run(
                **kwargs, script_id=uuid.UUID(first.children[0].id), payload=payload
            )
        elif entry == "session":
            teacher_judge_sessions.create_session_run(
                **kwargs,
                session_id=workspace.id,
                artifact_id=uuid.UUID(first.children[0].id),
                payload=payload,
            )
        else:
            teacher_judge_sessions.create_session_script_set_run(
                **kwargs,
                session_id=workspace.id,
                artifact_set_id=uuid.UUID(first.artifact_set_id),
                payload=TeacherJudgeScriptSetRunRequest(),
            )
    assert error.value.status_code == 409
    assert db.exec(select(TeacherJudgeScriptRun)).first() is None


async def test_queued_execution_finishes_with_original_artifact_after_switch(
    context, monkeypatch
):
    from app.ai.teacher_judge import script_executor_service as executor

    db, workspace, _, build = context
    first = build()
    monkeypatch.setattr(
        runs,
        "_resolve_node_targets",
        lambda **k: (
            [],
            [
                {
                    "vmid": None,
                    "status": "failed",
                    "reason_code": "missing_vmid",
                    "node_key": "web",
                }
            ],
        ),
    )
    monkeypatch.setattr(runs, "_attach_peer_runtime_contexts", lambda **k: None)
    run = runs.create_script_run(
        session=db,
        teaching_class_id=workspace.teaching_class_id,
        artifact_id=uuid.UUID(first.children[0].id),
        target_scope=TeacherJudgeScriptRunTargetScope.all_students_on_node,
        target_vmids=None,
        started_by=None,
    )
    build()
    monkeypatch.setattr(executor, "engine", db.get_bind())
    await executor.execute_script_run(uuid.UUID(run.id))
    db.expire_all()
    saved = db.get(TeacherJudgeScriptRun, uuid.UUID(run.id))
    assert saved.artifact_id == uuid.UUID(first.children[0].id)
    assert saved.finished_at is not None
    assert saved.results_document["targets"][0]["reason_code"] == "missing_vmid"
    assert saved.result_summary_json.get("error") != "只有已核准的腳本可以執行。"


@pytest.mark.parametrize(
    "invalid", ["revision", "missing_node", "archived", "duplicate", "no_valid_set"]
)
def test_migration_only_backfills_complete_current_source_set(
    context, monkeypatch, invalid
):
    db, workspace, _, build = context
    first = build()
    second = build()
    second_child = db.get(TeacherJudgeScriptArtifact, uuid.UUID(second.children[0].id))
    if invalid == "missing_node":
        db.delete(second_child)
    elif invalid == "archived":
        second_child.status = "archived"
        db.add(second_child)
    elif invalid == "duplicate":
        values = second_child.model_dump(exclude={"id"})
        db.add(TeacherJudgeScriptArtifact(**values))
    else:
        second_child.source_analysis_revision = 99
        db.add(second_child)
    if invalid == "no_valid_set":
        child = db.get(TeacherJudgeScriptArtifact, uuid.UUID(first.children[0].id))
        child.source_analysis_revision = 99
        db.add(child)
    db.commit()
    class_id, workspace_id = workspace.teaching_class_id, workspace.id
    db.close()
    migration = importlib.import_module(
        "app.alembic.versions.tjactive01_weekly_active_script_set"
    )
    with db.get_bind().begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        operations.drop_column("teacher_judge_sessions", "active_artifact_set_id")
        monkeypatch.setattr(migration, "op", operations)
        migration.upgrade()
        row = (
            connection.execute(
                select(TeacherJudgeSession).where(
                    TeacherJudgeSession.id == workspace_id
                )
            )
            .mappings()
            .one()
        )
        assert (
            row["active_artifact_set_id"] is None
            if invalid == "no_valid_set"
            else str(row["active_artifact_set_id"]) == first.artifact_set_id
        )
        assert len(
            connection.execute(
                select(TeacherJudgeScriptArtifact).where(
                    TeacherJudgeScriptArtifact.teaching_class_id == class_id
                )
            ).all()
        ) == (3 if invalid == "missing_node" else 5 if invalid == "duplicate" else 4)
        migration.downgrade()
