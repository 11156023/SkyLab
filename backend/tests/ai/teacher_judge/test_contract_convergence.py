"""Every proposal and persistence boundary uses the same current contract."""

import copy
import uuid

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import file_service, service
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.deterministic_compiler import compile_check_plan
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
)
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import make_session, make_teacher_judge_file
from tests.ai.teacher_judge.test_finalizer_path_conflicts import file_item, tool_state


@pytest.mark.parametrize("operation", ["create", "edit", "finalizer"])
@pytest.mark.parametrize("step", [
    {"argv": ["cat", "/root/test/test.txt"], "timeout_seconds": 30},
    {"template_key": "linux", "command_key": "system.run_command",
     "parameters": {"argv": ["cat", "/root/test/test.txt"], "timeout_seconds": 30}},
])
def test_legacy_steps_are_read_only_at_every_proposal_boundary(operation, step):
    item = file_item()
    state = tool_state(item)
    state.update(finalizer=operation == "finalizer", ready_only=operation != "finalizer")
    if operation == "create":
        state["snapshot_items"] = []
        arguments = {key: value for key, value in item.items() if key != "id"}
        tool = "create_checklist_item"
    else:
        service._execute_checklist_tool("get_checklist_item", {"id": item["id"]}, **state)
        arguments = {"id": item["id"]}
        tool = "edit_checklist_item"
    arguments.update(check_steps=[step], judgement_mode="teacher")
    result = service._execute_checklist_tool(tool, arguments, **state)
    assert result["reason_code"] == "check_plan_contract_invalid"
    assert result["teacher_input_required"] is False
    assert result["retryable"] is True
    assert state["staged_ops"] == []


@pytest.mark.parametrize("detectable", ["auto", "partial"])
def test_direct_save_rejects_known_path_claim_without_changing_data(monkeypatch, detectable):
    original = file_item()
    original["target_node_key"] = None
    candidate = copy.deepcopy(original)
    candidate.update(detectable=detectable, missing_information=["檔案的絕對路徑"])
    monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *args: [])
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        file.analysis_json = {"items": [original]}
        db.add(file)
        db.commit()
        with pytest.raises(HTTPException) as exc:
            file_service.update_file_analysis(
                session=db, teaching_class_id=class_id, file_id=file.id,
                analysis=TeacherJudgeRubricAnalysis(items=[candidate]), expected_revision=1,
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "teacher_judge_check_plan_contract_invalid"
        assert any(issue["field"] == "missing_information[0]" for issue in exc.value.detail["issues"])
        db.refresh(file)
        assert file.analysis_revision == 1
        assert file.analysis_json == {"items": [original]}


def test_real_missing_information_does_not_hide_another_items_contract_error():
    invalid = file_item()
    invalid["check_steps"][0].pop("assertion")
    missing = {"id": "port", "title": "服務", "detectable": "partial",
               "missing_information": ["服務 Port"], "check_steps": []}
    blockers = get_script_generation_blockers(
        TeacherJudgeRubricAnalysis(items=[invalid, missing]), [], require_typed_plan=True,
    )
    by_id = {row["item_id"]: row for row in blockers}
    assert by_id[invalid["id"]]["status"] == "analysis_error"
    assert by_id[invalid["id"]]["missing_information"] == []
    assert by_id["port"]["missing_information"] == ["服務 Port"]


@pytest.mark.parametrize("finalizer", [False, True])
def test_redeclaring_plan_does_not_silently_remove_unrelated_scope(finalizer):
    item = file_item()
    item.update(detectable="partial", missing_information=["要收集記錄的日期範圍"])
    state = tool_state(item)
    state.update(finalizer=finalizer, ready_only=not finalizer)
    service._execute_checklist_tool("get_checklist_item", {"id": item["id"]}, **state)
    result = service._execute_checklist_tool("edit_checklist_item", {
        "id": item["id"], "detectable": "auto", "check_steps": item["check_steps"],
    }, **state)
    assert not any(entry["item"].detectable == "auto" for entry in state["staged_ops"])
    assert result["unchanged"] is True
    assert item["missing_information"] == ["要收集記錄的日期範圍"]


def test_public_step_schema_only_advertises_current_write_fields():
    schema = TeacherJudgeRubricCheckStep.model_json_schema()
    assert set(schema["properties"]) == {"id", "title", "collector", "assertion"}
    assert schema["required"] == ["id", "title", "collector"]


def test_existing_directory_can_survive_metadata_save_but_cannot_be_rewritten(monkeypatch):
    original = file_item()
    original["target_node_key"] = None
    original["check_steps"][0]["collector"]["cwd"] = "/root/test"
    monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *args: [])
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        file.analysis_json = {"items": [original]}
        db.add(file)
        db.commit()
        analysis = TeacherJudgeRubricAnalysis(items=[original])
        analysis.items[0].title = "更新顯示名稱"
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id,
            analysis=analysis, expected_revision=1,
        )
        assert saved.analysis_revision == 2
        assert saved.analysis_json["items"][0]["check_steps"][0]["collector"]["cwd"] == "/root/test"
        blockers = get_script_generation_blockers(analysis, [], require_typed_plan=True)
        assert blockers[0]["status"] == "analysis_error"
        analysis.items[0].check_steps[0].collector.cwd = "/different"
        with pytest.raises(HTTPException) as exc:
            file_service.update_file_analysis(
                session=db, teaching_class_id=class_id, file_id=file.id,
                analysis=analysis, expected_revision=2,
            )
        assert exc.value.status_code == 422
        db.refresh(file)
        assert file.analysis_revision == 2


def test_two_machine_labels_preserve_same_absolute_path_through_save_and_compile():
    original = file_item()
    state = tool_state(original)
    state.update(snapshot_items=[], finalizer=False, ready_only=True, machine_entries=[
        {"display_label": "P1", "node_key": "web"},
        {"display_label": "P2", "node_key": "db"},
    ])
    for label in ("P1", "P2"):
        arguments = {key: copy.deepcopy(value) for key, value in original.items() if key != "id"}
        arguments.update(title=f"讀取 /root/test/test.txt 內容於 {label}", target_node_key=label)
        result = service._execute_checklist_tool("create_checklist_item", arguments, **state)
        assert result["staged"] == "add"
    analysis = TeacherJudgeRubricAnalysis(items=[entry["item"] for entry in state["staged_ops"]])
    assert get_script_generation_blockers(analysis, [], require_typed_plan=True) == []
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        for node_key in ("web", "db"):
            db.add(TeachingClassMachineNode(
                class_id=class_id, node_key=node_key, name=node_key, role="test",
                resource_type="lxc", cpu=1, memory_mb=512, disk_gb=8,
            ))
        db.commit()
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id,
            analysis=analysis, expected_revision=1,
        )
        restored = TeacherJudgeRubricAnalysis.model_validate(saved.analysis_json)
        assert {item.target_node_key for item in restored.items} == {"web", "db"}
        for node_key in ("web", "db"):
            script, policy, _review, plan = compile_check_plan(restored, target_node_key=node_key)
            assert policy["approved"] is True
            assert len(plan["items"]) == 1
            assert plan["items"][0]["check_steps"][0]["collector"]["path"] == "/root/test/test.txt"
            assert "cwd" not in script
