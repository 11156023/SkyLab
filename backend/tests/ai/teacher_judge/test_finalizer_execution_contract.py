"""Finalizer must not turn known locations into teacher-information gaps."""

from __future__ import annotations

import copy
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.teacher_judge import file_service, script_artifact_service, service
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.session_service import apply_proposal_operations_to_analysis
from app.models.teaching_class import TeachingClassMachineNode
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    scripted_vllm,
    tool_call_message,
)

LOCATION_GAP = "工作目錄的完整路徑（不可使用相對路徑或 ~；不需要工作目錄時可留空）"


def multi_node_items():
    items = []
    for node, argv, cwd, mode in [
        ("postgresql", ["cat", "/home/owo/main.log"], None, "teacher"),
        ("n8n", ["pgrep", "-f", "n8n"], None, "ai"),
        ("python", ["python3", "main.py"], "/home/owo", "teacher"),
    ]:
        items.append({
            "id": f"item-{node}", "title": f"check {node}", "detectable": "auto",
            "judgement_mode": mode, "detection_method": "Collect the requested evidence",
            "target_node_key": node, "missing_information": [],
            "check_steps": [{
                # Repeated step IDs are valid across distinct nodes.
                "id": "step-1", "title": "Collect", "collector": {
                    "type": "command", "argv": argv, "cwd": cwd, "timeout_seconds": 30,
                },
                **({"assertion": {"type": "returncode_equals", "expected": 0}} if mode == "ai" else {}),
            }],
        })
    return items


async def run_finalizer(monkeypatch, items, edits, *, refine=True):
    responses = [
        tool_call_message("list_checklist", {}),
        *[tool_call_message("edit_checklist_item", edit) for edit in edits],
        reply_message("核對已完成。", "ready"),
    ]
    calls, fake = scripted_vllm([
        (response, {"completion_tokens": 10, "prompt_tokens": 20, "usage_reported": True})
        for response in responses
    ])
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    patch_teacher_judge_vllm_settings(monkeypatch)
    result = await service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="核對目前檢查表並製作脚本")],
        rubric_context=json.dumps({"items": items}), is_refine=refine,
        template_commands=[], rubric_available=True,
        machine_entries=[{"node_key": item["target_node_key"]} for item in items],
    )
    return calls, result


async def test_finalizer_preserves_locations_through_save_and_three_node_artifacts(monkeypatch):
    items = multi_node_items()
    edits = []
    for index, item in enumerate(items):
        steps = copy.deepcopy(item["check_steps"])
        if index < 2:
            steps[0]["collector"]["cwd"] = "null"
        else:
            steps[0]["collector"].pop("cwd")
        edits.append({
            "id": item["id"], "check_steps": steps,
            "detectable": "partial" if index < 2 else "auto",
            "missing_information": [LOCATION_GAP] if index < 2 else [],
        })
    calls, result = await run_finalizer(monkeypatch, items, edits)
    assert len(calls) == 5
    candidate = apply_proposal_operations_to_analysis(
        TeacherJudgeRubricAnalysis(items=items), result.proposal,
    )
    assert get_script_generation_blockers(
        candidate, [], require_target_node=True, require_typed_plan=True,
    ) == []

    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        for index, item in enumerate(items):
            db.add(TeachingClassMachineNode(
                class_id=class_id, node_key=item["target_node_key"], name=item["title"],
                role="test", resource_type="lxc", cpu=1, memory_mb=512, disk_gb=8,
                sort_order=index,
            ))
        db.commit()
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id,
            analysis=candidate, expected_revision=1,
        )
        restored = TeacherJudgeRubricAnalysis.model_validate(saved.analysis_json)
        monkeypatch.setattr(script_artifact_service, "get_enabled_template_commands", lambda *a, **kw: [])
        artifact_set = script_artifact_service.create_artifact_set(
            session=db, teaching_class_id=class_id, session_id=uuid.uuid4(),
            name="three nodes", template_key="linux", rubric_analysis=restored,
            source_analysis_revision=saved.analysis_revision, created_by=None,
            source_file_id=file.id,
        )
        assert artifact_set.status == "approved"
        assert len(artifact_set.children) == 3
        expected = {item["target_node_key"]: item["check_steps"][0]["collector"] for item in items}
        for child in artifact_set.children:
            assert child.source_analysis_revision == saved.analysis_revision
            child_items = child.rubric_snapshot_json["items"]
            assert len(child_items) == 1
            assert child_items[0]["target_node_key"] == child.target_node_key
            assert child_items[0]["check_steps"][0]["collector"] == expected[child.target_node_key]


async def test_invalid_finalizer_location_is_repairable_without_asking_teacher(monkeypatch):
    item = multi_node_items()[0]
    invalid = copy.deepcopy(item["check_steps"])
    invalid[0]["collector"]["cwd"] = "~"
    calls, result = await run_finalizer(monkeypatch, [item], [
        {"id": item["id"], "check_steps": invalid, "detectable": "auto"},
        {"id": item["id"], "check_steps": item["check_steps"], "detectable": "auto"},
    ])
    error = json.loads(calls[2]["messages"][-1]["content"])
    assert error["reason_code"] == "check_plan_contract_invalid"
    assert error["retryable"] is True
    assert error["teacher_input_required"] is False
    assert result.proposal == []
    assert any(outcome.get("resolved") for outcome in result.tool_calls)


async def test_return_to_saved_contract_supersedes_earlier_partial_candidate(monkeypatch):
    item = multi_node_items()[2]
    changed = copy.deepcopy(item["check_steps"])
    changed[0]["collector"].update(argv=["python3", "other.py"], cwd=None)
    _, result = await run_finalizer(monkeypatch, [item], [
        {"id": item["id"], "check_steps": changed, "detectable": "auto"},
        {"id": item["id"], "check_steps": item["check_steps"], "detectable": "auto"},
    ])
    assert result.proposal == []


async def test_location_reconciliation_does_not_clear_unrelated_missing_information(monkeypatch):
    item = multi_node_items()[0]
    _, result = await run_finalizer(monkeypatch, [item], [{
        "id": item["id"], "detectable": "partial",
        "missing_information": [LOCATION_GAP, "要收集記錄的日期範圍"],
    }])
    assert result.proposal[0]["detectable"] == "partial"
    assert result.proposal[0]["missing_information"] == ["要收集記錄的日期範圍"]


@pytest.mark.parametrize("change", ["node", "argv", "ordinary_chat"])
async def test_location_is_never_borrowed_for_a_different_execution_scope(monkeypatch, change):
    items = multi_node_items()
    item = items[2]
    steps = copy.deepcopy(item["check_steps"])
    steps[0]["collector"].pop("cwd")
    edit = {"id": item["id"], "check_steps": steps, "detectable": "auto"}
    if change == "node":
        edit["target_node_key"] = "postgresql"
        steps[0]["id"] = "moved-step"
    elif change == "argv":
        steps[0]["collector"]["argv"] = ["python3", "other.py"]
    _, result = await run_finalizer(monkeypatch, items, [edit], refine=change != "ordinary_chat")
    assert not result.proposal or result.proposal[0]["detectable"] == "partial"
    if result.proposal:
        assert result.proposal[0]["check_steps"][0]["collector"]["cwd"] is None
