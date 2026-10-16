"""Known file locations must not become teacher-input requests during review."""

from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.teacher_judge import (
    file_service,
    script_artifact_service,
    service,
    session_chat_service,
)
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.session_service import (
    apply_proposal_operations_to_analysis,
    reanalysis_workflow_message,
)
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import (
    make_script_workspace,
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    scripted_vllm,
    tool_call_message,
)

PATH_GAP = "沒有說明檔案所在的工作目錄或完整路徑，導致無法唯一定位 `/root/test/test.txt`"


def file_item():
    return {
        "id": "file-check", "title": "檢查檔案內容", "detectable": "auto",
        "judgement_mode": "ai", "detection_method": "讀取指定檔案並核對包含 hello",
        "target_node_key": "linux", "missing_information": [],
        "check_steps": [{
            "id": "file.content", "title": "讀取檔案",
            "collector": {"type": "file_text", "path": "/root/test/test.txt"},
            "assertion": {"type": "text_contains", "expected": "hello"},
        }],
    }


def tool_state(item):
    return dict(
        snapshot_items=[item], analysis_revision=1, template_key="linux",
        template_commands=[], machine_entries=[{"node_key": "linux"}],
        ready_only=False, finalizer=True, read_ids=set(), staged_ops=[],
        rejected_ops=[], tool_calls=[],
    )


async def review(monkeypatch, item, responses):
    patch_teacher_judge_vllm_settings(monkeypatch)
    calls, fake = scripted_vllm([
        (response, {"completion_tokens": 10, "prompt_tokens": 20, "usage_reported": True})
        for response in responses
    ])
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    result = await service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="儲存並製作目前檢查表")],
        rubric_context=json.dumps({"items": [item]}, ensure_ascii=False),
        is_refine=True, template_commands=[], rubric_available=True,
        machine_entries=[{"node_key": "linux"}],
    )
    return calls, result


def test_list_does_not_authorize_finalizer_edit_without_reading_path():
    item = file_item()
    state = tool_state(item)
    listing = service._execute_checklist_tool("list_checklist", {}, **state)
    assert "check_steps" not in listing["items"][0]
    result = service._execute_checklist_tool("edit_checklist_item", {
        "id": item["id"], "detectable": "partial", "missing_information": [PATH_GAP],
    }, **state)
    assert result["reason_code"] == "checklist_item_not_read"
    assert result["teacher_input_required"] is False
    assert result["retryable"] is True
    assert state["staged_ops"] == []


@pytest.mark.parametrize("gap", [
    PATH_GAP, "尚缺檔案的絕對路徑", "Missing working directory or full path",
])
def test_located_plan_rejects_prose_gap_as_repairable_contract_error(gap):
    item = file_item()
    state = tool_state(item)
    detail = service._execute_checklist_tool("get_checklist_item", {"id": item["id"]}, **state)
    assert detail["execution_readiness"]["ready"] is True
    result = service._execute_checklist_tool("edit_checklist_item", {
        "id": item["id"], "detectable": "partial", "missing_information": [gap],
    }, **state)
    assert result["reason_code"] == "check_plan_contract_invalid"
    assert result["teacher_input_required"] is False
    assert result["retryable"] is True
    assert result["issues"][0]["field"] == "missing_information[0]"
    assert state["staged_ops"] == []
    assert item["check_steps"][0]["collector"]["path"] == "/root/test/test.txt"


@pytest.mark.parametrize("gap", [PATH_GAP, "檔案「/root/test/test.txt」的完整路徑（或提供工作目錄與相對路徑）"])
def test_persisted_partial_with_known_path_requests_contract_repair(gap):
    item = file_item()
    item.update(detectable="partial", missing_information=[gap])
    blockers = get_script_generation_blockers(TeacherJudgeRubricAnalysis(items=[item]), [], require_typed_plan=True)
    assert blockers[0]["reason_code"] == "check_plan_contract_invalid"
    assert blockers[0]["status"] == "analysis_error"
    assert blockers[0]["missing_information"] == []
    assert item["missing_information"] == [gap]
    workflow = reanalysis_workflow_message(blockers, source_file_id=None, analysis_revision=1)
    assert workflow["metadata"]["script_ready"] is False
    assert workflow["metadata"]["status"] == "analysis_error"
    assert "無須重填已知路徑" in workflow["content"]
    assert "請提供該程式／檔案的完整路徑" not in workflow["content"]
    assert all(issue["detail"] for issue in workflow["metadata"]["item_results"][0]["issues"])
    assert not any(term in json.dumps(workflow, ensure_ascii=False) for term in (
        "get_checklist_item", "detectable=auto", "typed", "repair_hint",
    ))


def test_real_missing_file_location_and_unrelated_scope_remain_blocked():
    item = file_item()
    item.update(detectable="partial", missing_information=[PATH_GAP, "要收集記錄的日期範圍"])
    item["check_steps"][0]["collector"]["path"] = "test.txt"
    blockers = get_script_generation_blockers(TeacherJudgeRubricAnalysis(items=[item]), [], require_typed_plan=True)
    assert blockers[0]["reason_code"] == "automatic_detection_information_missing"
    assert blockers[0]["missing_information"] == item["missing_information"]


async def test_read_precondition_is_repaired_by_detail_read_without_replaying_edit(monkeypatch):
    item = file_item()
    calls, result = await review(monkeypatch, item, [
        tool_call_message("list_checklist", {}),
        tool_call_message("edit_checklist_item", {"id": item["id"], "title": item["title"]}),
        reply_message("尚待讀取項目", "none"),
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        reply_message("目前步驟已完整，不需修改", "ready"),
    ])
    assert calls[3]["tool_choice"]["function"]["name"] == "get_checklist_item"
    assert any(outcome.get("resolved") for outcome in result.tool_calls)
    assert result.proposal == []


async def test_path_conflict_repairs_only_false_gap_and_preserves_date_scope(monkeypatch):
    item = file_item()
    calls, result = await review(monkeypatch, item, [
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "partial",
            "missing_information": [PATH_GAP, "要收集記錄的日期範圍"],
        }),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "partial",
            "missing_information": ["要收集記錄的日期範圍"],
        }),
        reply_message("請提供記錄的日期範圍", "needs_information"),
    ])
    assert json.loads(calls[2]["messages"][-1]["content"])["teacher_input_required"] is False
    assert result.proposal[0]["missing_information"] == ["要收集記錄的日期範圍"]
    assert result.proposal[0]["check_steps"][0]["collector"]["path"] == "/root/test/test.txt"


def test_finalizer_cannot_replace_known_absolute_path_with_unlocated_relative_path():
    item = file_item()
    state = tool_state(item)
    service._execute_checklist_tool("get_checklist_item", {"id": item["id"]}, **state)
    steps = copy.deepcopy(item["check_steps"])
    steps[0]["collector"]["path"] = "test.txt"
    result = service._execute_checklist_tool("edit_checklist_item", {
        "id": item["id"], "check_steps": steps, "detectable": "auto",
    }, **state)
    assert result["reason_code"] == "check_plan_contract_invalid"
    assert result["teacher_input_required"] is False
    assert result["issues"][0]["step_id"] == "file.content"
    assert state["staged_ops"] == []


async def test_existing_partial_is_repaired_after_reply_without_update(monkeypatch):
    item = file_item()
    item.update(detectable="partial", missing_information=[PATH_GAP])
    calls, result = await review(monkeypatch, item, [
        tool_call_message("list_checklist", {}),
        reply_message("請補充完整路徑", "needs_information"),
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "auto", "missing_information": [],
        }),
        reply_message("已確認檔案位置", "ready"),
    ])
    assert calls[2]["tool_choice"]["function"]["name"] == "get_checklist_item"
    detail = json.loads(calls[3]["messages"][-1]["content"])
    assert detail["item"]["check_steps"][0]["collector"]["path"] == "/root/test/test.txt"
    assert detail["execution_readiness"]["blockers"][0]["status"] == "analysis_error"
    assert result.proposal[0]["detectable"] == "auto"
    assert result.proposal[0]["missing_information"] == []


@pytest.mark.parametrize("already_partial", [False, True])
@pytest.mark.parametrize("collector", [
    {"type": "file_text", "path": "/root/test/test.txt"},
    {"type": "file_text", "path": "/root/test/另一份檔案.txt"},
    {"type": "command", "argv": ["cat", "/root/test/test.txt"]},
])
async def test_path_conflict_repair_survives_readiness_save_and_artifact(
    monkeypatch, already_partial, collector,
):
    item = file_item()
    item["check_steps"][0]["collector"] = copy.deepcopy(collector)
    if already_partial:
        item.update(detectable="partial", missing_information=[PATH_GAP])
    original = copy.deepcopy(item)
    calls, result = await review(monkeypatch, item, [
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "partial", "missing_information": [PATH_GAP],
        }),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "auto", "missing_information": [],
        }),
        reply_message("已確認完整路徑，不需另外提供工作目錄", "ready"),
    ])
    assert len(calls) == 4
    assert item == original
    assert any(outcome.get("resolved") for outcome in result.tool_calls)
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        file.analysis_json = TeacherJudgeRubricAnalysis(items=[item]).model_dump(mode="json")
        db.add(TeachingClassMachineNode(
            class_id=class_id, node_key="linux", name="Linux", role="test",
            resource_type="lxc", cpu=1, memory_mb=512, disk_gb=8,
        ))
        db.add(file)
        db.commit()
        workflow = session_chat_service.refine_readiness_workflow(
            db, teaching_class_id=class_id, session_id=uuid.uuid4(), file=file,
            proposal=result.proposal, template_commands=[],
            analysis_revision=1, tool_calls=result.tool_calls,
        )
        assert workflow["metadata"]["script_ready"] is True
        candidate = apply_proposal_operations_to_analysis(
            TeacherJudgeRubricAnalysis.model_validate(file.analysis_json), result.proposal,
        )
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id,
            analysis=candidate, expected_revision=1,
        )
        assert saved.analysis_revision == 2
        monkeypatch.setattr(script_artifact_service, "get_enabled_template_commands", lambda *a, **kw: [])
        artifact = script_artifact_service.create_artifact_set(
            session=db, teaching_class_id=class_id, session_id=make_script_workspace(db, class_id, source_file_id=file.id).id,
            name="path regression", template_key="linux",
            rubric_analysis=TeacherJudgeRubricAnalysis.model_validate(saved.analysis_json),
            source_analysis_revision=saved.analysis_revision, created_by=None,
            source_file_id=file.id,
        )
        assert artifact.status == "approved"
        assert len(artifact.children) == 1
        child = artifact.children[0]
        assert child.source_analysis_revision == 2
        saved_item = child.rubric_snapshot_json["items"][0]
        assert saved_item["missing_information"] == []
        assert saved_item["check_steps"] == candidate.items[0].model_dump(mode="json")["check_steps"]


async def test_repeated_path_conflict_stops_as_analysis_error_without_teacher_question(monkeypatch):
    item = file_item()
    edit = {"id": item["id"], "detectable": "partial", "missing_information": [PATH_GAP]}
    _, result = await review(monkeypatch, item, [
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", edit),
        tool_call_message("edit_checklist_item", edit),
    ])
    assert result.proposal == []
    assert result.tool_calls[-1]["retryable"] is False
    assert "本次修正已停止" in result.reply
    assert "請補上完整路徑" not in result.reply
    assert result.conversation_focus["requirements"][0]["status"] == "analysis_error"
