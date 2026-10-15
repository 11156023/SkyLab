"""Absolute paths and location verdicts agree at every proposal boundary."""

import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.teacher_judge import service, session_chat_service
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.check_plan_contract import typed_step_issues
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    canonicalize_check_plan,
    compile_check_plan,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeSessionMessageCreateRequest,
)
from app.ai.teacher_judge.session_service import apply_proposal_operations_to_analysis
from app.api.routes import teacher_judge_sessions as routes
from app.models.teacher_judge_session import (
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    reply_message,
    tool_call_message,
)
from tests.ai.teacher_judge.test_finalizer_path_conflicts import (
    file_item,
    review,
    tool_state,
)


@pytest.mark.parametrize("operation", ["create", "edit", "finalizer"])
@pytest.mark.parametrize("missing_assertion", [False, True])
def test_known_path_conflict_is_repairable_independently_of_assertion(operation, missing_assertion):
    item = file_item()
    if missing_assertion:
        item["check_steps"][0].pop("assertion")
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
    arguments.update(detectable="partial", missing_information=["檔案的絕對路徑"])
    result = service._execute_checklist_tool(tool, arguments, **state)
    assert result["reason_code"] == "check_plan_contract_invalid"
    assert result["teacher_input_required"] is False
    assert result["retryable"] is True
    assert state["staged_ops"] == []


@pytest.mark.parametrize("finalizer", [False, True])
def test_index_never_authorizes_edit(finalizer):
    item = file_item()
    state = tool_state(item)
    state["finalizer"] = finalizer
    service._execute_checklist_tool("list_checklist", {}, **state)
    result = service._execute_checklist_tool("edit_checklist_item", {"id": item["id"], "title": "updated"}, **state)
    assert result["reason_code"] == "checklist_item_not_read"
    assert result["teacher_input_required"] is False


@pytest.mark.parametrize("cwd", [None, "", "/root/test"])
def test_write_schema_and_validation_remove_cwd(cwd):
    assert '"cwd"' not in json.dumps(service._build_proposal_tools())
    item = file_item()
    item["check_steps"][0]["collector"]["cwd"] = cwd
    assert typed_step_issues(item["check_steps"])


@pytest.mark.parametrize("collector", [
    {"type": "file_text", "path": "test.txt"},
    {"type": "file_text", "path": "test.txt", "cwd": "/root/test"},
    {"type": "command", "argv": ["cat", "test.txt"], "cwd": "/root/test"},
])
def test_relative_paths_never_compile_even_with_legacy_directory(collector):
    item = file_item()
    item["check_steps"][0]["collector"] = collector
    analysis = TeacherJudgeRubricAnalysis(items=[item])
    with pytest.raises(CheckPlanContractError):
        canonicalize_check_plan(analysis)


def test_absolute_path_compiles_without_directory_in_plan_or_runtime():
    item = file_item()
    item["check_steps"][0]["collector"] = {"type": "command", "argv": ["cat", "/root/test/test.txt"]}
    script, _policy, _review, plan = compile_check_plan(TeacherJudgeRubricAnalysis(items=[item]), target_node_key="linux")
    assert "cwd" not in script
    assert '"cwd"' not in json.dumps(plan)
    assert "/root/test/test.txt" in script


def test_old_directory_is_readable_but_requires_contract_repair():
    item = file_item()
    item["check_steps"][0]["collector"]["cwd"] = "/root/test"
    original = copy.deepcopy(item)
    analysis = TeacherJudgeRubricAnalysis(items=[item])
    assert item == original
    assert analysis.items[0].check_steps[0].collector.cwd == "/root/test"
    blockers = get_script_generation_blockers(analysis, [], require_typed_plan=True)
    assert blockers[0]["status"] == "analysis_error"
    assert blockers[0]["missing_information"] == []


async def test_finalizer_explanation_uses_actual_blocker_not_false_model_path_claim(monkeypatch):
    item = file_item()
    item["check_steps"][0].pop("assertion")
    false_reply = "目前缺少檔案的完整路徑資訊，請提供 /root/test/test.txt 在 P1 與 P2 上的絕對路徑。"

    async def fake_chat(*args, **kwargs):
        assert kwargs["is_refine"] is True
        return false_reply, [], {}

    monkeypatch.setattr(routes, "_access", lambda *args: None)
    monkeypatch.setattr(routes, "get_enabled_template_commands", lambda *args, **kwargs: [])
    monkeypatch.setattr(routes, "chat_with_rubric", fake_chat)
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        file.analysis_json = {"items": [item]}
        chat_session = TeacherJudgeSession(
            teaching_class_id=class_id, title="檔案檢查", selected_file_id=file.id,
        )
        db.add_all([file, chat_session])
        db.commit()
        result = await routes.create_message(
            class_id, chat_session.id,
            TeacherJudgeSessionMessageCreateRequest(content="儲存並製作", is_refine=True),
            db, SimpleNamespace(id=uuid.uuid4()),
        )
        assistant = db.get(TeacherJudgeSessionMessage, uuid.UUID(result.assistant_message.id))
        assert assistant.content == result.assistant_message.content
        assert assistant.metadata_json["status"] == "analysis_error"
        assert assistant.metadata_json["script_ready"] is False
        assert "缺少檔案的完整路徑" not in assistant.content
        assert "請提供" not in assistant.content
        assert assistant.metadata_json["item_results"][0]["missing_information"] == []
        db.refresh(file)
        assert file.analysis_json == {"items": [item]}


async def test_finalizer_repairs_assertion_without_requesting_known_path(monkeypatch):
    item = file_item()
    corrected_steps = copy.deepcopy(item["check_steps"])
    item["check_steps"][0].pop("assertion")
    calls, result = await review(monkeypatch, item, [
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "partial",
            "missing_information": ["檔案的絕對路徑"],
        }),
        tool_call_message("edit_checklist_item", {
            "id": item["id"], "detectable": "auto", "missing_information": [],
            "check_steps": corrected_steps,
        }),
        reply_message("已依原本條件補齊檢查方式，檔案位置維持不變。", "ready"),
    ])
    repair = json.loads(calls[2]["messages"][-1]["content"])
    assert repair["reason_code"] == "check_plan_contract_invalid"
    assert repair["teacher_input_required"] is False
    assert any(outcome.get("resolved") for outcome in result.tool_calls)
    assert len(result.proposal) == 1
    candidate = apply_proposal_operations_to_analysis(
        TeacherJudgeRubricAnalysis(items=[item]), result.proposal,
    )
    assert get_script_generation_blockers(candidate, [], require_typed_plan=True) == []
    script, _policy, _review, _plan = compile_check_plan(candidate, target_node_key="linux")
    assert "/root/test/test.txt" in script
    monkeypatch.setattr(session_chat_service, "load_class_machine_nodes", lambda *args: [])
    workflow = session_chat_service.refine_readiness_workflow(
        None, teaching_class_id=uuid.uuid4(), session_id=uuid.uuid4(),
        file=SimpleNamespace(id=uuid.uuid4(), analysis_json={"items": [item]}),
        proposal=result.proposal, template_commands=[], analysis_revision=1,
        tool_calls=result.tool_calls,
    )
    assert workflow["metadata"]["script_ready"] is True


@pytest.mark.parametrize("status", ["error", "rejected", "duplicate"])
def test_unresolved_tool_failure_blocks_finalizer_consistently(monkeypatch, status):
    item = file_item()
    monkeypatch.setattr(session_chat_service, "load_class_machine_nodes", lambda *args: [])
    workflow = session_chat_service.refine_readiness_workflow(
        None, teaching_class_id=uuid.uuid4(), session_id=uuid.uuid4(),
        file=SimpleNamespace(id=uuid.uuid4(), analysis_json={"items": [item]}),
        proposal=[], template_commands=[], analysis_revision=1,
        tool_calls=[{"status": status, "item_id": item["id"], "title": item["title"], "reason_code": "check_plan_contract_invalid"}],
    )
    assert workflow["metadata"]["script_ready"] is False
    assert workflow["metadata"]["status"] == "analysis_error"
