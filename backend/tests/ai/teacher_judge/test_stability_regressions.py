"""Observable Teacher Judge failures: incomplete results and lost teacher edits."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.ai.teacher_judge import (
    attachment_service,
    file_service,
    script_artifact_service,
    script_run_service,
    service,
)
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    compile_check_plan,
)
from app.ai.teacher_judge.schemas import TeacherJudgeRubricAnalysis
from app.ai.teacher_judge.script_executor_service import RAW_RESULT_LIMIT
from app.ai.teacher_judge.script_policy import validate_managed_script_output
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptArtifact
from app.models.teacher_judge_session import TeacherJudgeSession
from app.models.teaching_class import TeachingClassMachineNode
from app.services.course.ai_assignment_service import _script_checks
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
)
from tests.ai.teacher_judge.test_deterministic_compiler import (
    _analysis,
    _execute_compiled_script,
)
from tests.ai.teacher_judge.test_finalizer_execution_contract import (
    multi_node_items,
    run_finalizer,
)


@pytest.mark.parametrize("mode,expected", [("head", "FIRST"), ("tail", "LAST")])
def test_file_line_selection_uses_requested_end(tmp_path, mode, expected):
    path = tmp_path / "student.log"
    path.write_text("FIRST\n" + "middle\n" * 6000 + "LAST\n", encoding="utf-8")
    analysis = _analysis(
        {"type": "file_text", "path": str(path), "read_mode": mode, "lines": 1, "max_chars": 1000},
        {"type": "text_equals", "expected": expected},
    )
    script, policy, _, _ = compile_check_plan(analysis, target_node_key="web")
    assert policy["approved"] is True
    result = _execute_compiled_script(script, tmp_path)
    assert result["checks"][0]["status"] == "pass"
    assert result["checks"][0]["evidence"] == expected


@pytest.mark.parametrize("mode", ["full", "head", "tail"])
def test_incomplete_file_content_does_not_produce_a_grade(tmp_path, mode):
    path = tmp_path / "student.log"
    path.write_text("start" + "x" * 10000 + "end", encoding="utf-8")
    collector = {"type": "file_text", "path": str(path), "read_mode": mode, "max_chars": 1000}
    if mode != "full":
        collector["lines"] = 1
    analysis = _analysis(collector, {"type": "text_contains", "expected": "not present"})
    script, policy, _, _ = compile_check_plan(analysis, target_node_key="web")
    assert policy["approved"] is True
    result = _execute_compiled_script(script, tmp_path)
    check = result["checks"][0]
    assert check["status"] == "unknown"
    assert json.loads(check["raw"])["error_code"] == "content_truncated"


@pytest.mark.parametrize("text", ["x", "資", "\x01"])
def test_large_evidence_keeps_every_check_within_executor_limit(tmp_path, text):
    path = tmp_path / "student.log"
    path.write_text(text * 6000, encoding="utf-8")
    base = _analysis({"type": "file_text", "path": str(path)}, None, judgement_mode="teacher")
    items = []
    for index in range(40):
        item = base.items[0].model_copy(deep=True)
        item.id = f"item-{index}"
        item.check_steps[0].id = f"check-{index}"
        items.append(item)
    script, policy, _, _ = compile_check_plan(TeacherJudgeRubricAnalysis(items=items), target_node_key="web")
    assert policy["approved"] is True
    result = _execute_compiled_script(script, tmp_path)
    payload = json.dumps(result, ensure_ascii=False)
    assert len(payload.encode("utf-8")) <= RAW_RESULT_LIMIT
    assert len(result["checks"]) == 40
    assert {check["status"] for check in result["checks"]} == {"collected"}
    assert all("截斷" in check["evidence"] for check in result["checks"])
    assert validate_managed_script_output(payload)["valid"] is True


@pytest.mark.parametrize("received", [[], [{"id": "a", "status": "pass", "title": "A"}]])
def test_missing_steps_are_incomplete_for_teacher_and_student(received):
    artifact = TeacherJudgeScriptArtifact(
        teaching_class_id=uuid.uuid4(), name="two checks", template_key="linux",
        script_content="", rubric_snapshot_json={"items": [{"id": "item", "title": "Service"}]},
        policy_check_result_json={"coverage": {"mappings": [
            {"check_id": check_id, "rubric_item_ids": ["item"]} for check_id in ("a", "b")
        ]}},
    )
    target = {"status": "completed", "parsed_result": {"checks": received}}
    teacher = script_run_service.project_run_items(artifact=artifact, target_result=target, display_labels={})["items"][0]
    student = _script_checks(target, artifact=artifact, item_id="item")[0]
    assert teacher["status"] == student.status == "unknown"
    assert teacher["reason_code"] == "missing_checks"
    assert "b" in teacher["missing_check_ids"]
    assert "不完整" in student.comment


async def test_successive_edits_keep_both_changes_in_one_proposal(monkeypatch):
    item = multi_node_items()[0]
    _, result = await run_finalizer(monkeypatch, [item], [
        {"id": item["id"], "title": "Updated title"},
        {"id": item["id"], "detection_method": "Updated evidence instructions"},
    ])
    assert len(result.proposal) == 1
    assert result.proposal[0]["title"] == "Updated title"
    assert result.proposal[0]["detection_method"] == "Updated evidence instructions"


@pytest.mark.parametrize("expected_revision", [None, 1])
def test_stale_session_cannot_overwrite_a_newer_saved_revision(monkeypatch, expected_revision):
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *_: [])
    with Session(db.get_bind()) as second:
        stale = file_service.get_file(session=second, teaching_class_id=class_id, file_id=file.id)
        assert stale.analysis_revision == file.analysis_revision == 1
        file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id,
            analysis=TeacherJudgeRubricAnalysis(summary="first saved edit", items=[]), expected_revision=1,
        )
        with pytest.raises(HTTPException) as exc:
            file_service.update_file_analysis(
                session=second, teaching_class_id=class_id, file_id=file.id,
                analysis=TeacherJudgeRubricAnalysis(summary="stale edit", items=[]), expected_revision=expected_revision,
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["analysis_revision"] == 2
    db.refresh(file)
    assert file.analysis_revision == 2
    assert file.analysis_json["summary"] == "first saved edit"
    db.close()


def test_attachment_extraction_does_not_silently_discard_excess_rows():
    sources, error = service._parse_attachment_extraction(json.dumps({
        "items": [{"title": f"Requirement {index}"} for index in range(51)],
    }))
    assert sources == []
    assert error and "50" in error


def test_oversized_attachment_is_not_saved_as_a_partial_document(tmp_path, monkeypatch):
    db = make_session()
    chat = TeacherJudgeSession(teaching_class_id=uuid.uuid4(), title="Attachments")
    db.add(chat)
    db.commit()
    monkeypatch.setattr(attachment_service, "ATTACHMENT_ROOT", tmp_path)
    with pytest.raises(ValueError, match="無法完整分析"):
        attachment_service.create_attachment(
            db, session_id=chat.id, uploaded_by=None, filename="requirements.txt", media_type="text/plain",
            file_bytes=b"x" * (attachment_service.MAX_EXTRACTED_CHARS + 1),
        )
    assert list(tmp_path.iterdir()) == []
    db.close()


@pytest.mark.parametrize("row", [None, {}, {"title": "Requirement", "description": "x" * 501}])
def test_malformed_or_overlong_source_row_is_not_silently_omitted(row):
    sources, error = service._parse_attachment_extraction(json.dumps({"items": [{"title": "First"}, row]}))
    assert sources == []
    assert error


def test_multiple_proposals_for_one_source_are_reported_as_unresolved():
    result = service._itemwise_result_from_chat(
        {"source_index": 1, "source_label": "第 1 列", "title": "Two requirements"},
        service.TeacherJudgeChatResult(reply="ready", proposal=[{"id": "a"}, {"id": "b"}], metrics={}),
    )
    assert result["status"] == "analysis_error"
    assert result["operation"] is None
    assert "多個提案" in result["detail"]


async def test_teacher_instruction_reaches_attachment_extraction_and_each_row(monkeypatch):
    instruction = "只檢查第二部分，Python 版本以 3.12 為準"
    captured = []

    async def extract(payload, timeout=60.0):
        assert instruction in payload["messages"][-1]["content"]
        return json.dumps({"items": [{"title": "Python version"}]}), {"completion_tokens": 10}

    async def analyze(**kwargs):
        captured.append(kwargs["teacher_message"])
        return service.TeacherJudgeChatResult(reply="需要路徑", proposal=None, metrics={}, proposal_status="needs_information")

    patch_teacher_judge_vllm_settings(monkeypatch)
    monkeypatch.setattr(service, "_call_vllm_message", extract)
    monkeypatch.setattr(service, "analyze_requirement_item", analyze)
    result = await service.analyze_attachments_itemwise(
        teacher_message=instruction, rubric_context="{}", attachment_context="Old document",
    )
    assert captured == [instruction]
    assert result.item_results[0]["status"] == "needs_information"


async def test_attachment_rows_cannot_overwrite_each_others_edits(monkeypatch):
    saved = multi_node_items()[0]

    async def extract(*args, **kwargs):
        return [{"source_index": index, "source_label": f"Row {index}", "title": f"Requirement {index}"} for index in (1, 2)], None, {}

    async def analyze(**kwargs):
        operation = {**saved, "title": kwargs["source"]["title"]}
        return service.TeacherJudgeChatResult(reply="ready", proposal=[operation], metrics={})

    patch_teacher_judge_vllm_settings(monkeypatch)
    monkeypatch.setattr(service, "extract_attachment_requirements", extract)
    monkeypatch.setattr(service, "analyze_requirement_item", analyze)
    result = await service.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": [saved]}), attachment_context="Two rows changing the same item",
    )
    assert result.proposal is None
    assert len(result.item_results) == 2
    assert all(row["status"] == "analysis_error" and row["operation"] is None for row in result.item_results)


def test_artifact_generation_does_not_write_back_an_older_source_snapshot(monkeypatch):
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        analysis = TeacherJudgeRubricAnalysis(items=[multi_node_items()[0]])
        db.add(TeachingClassMachineNode(
            class_id=class_id, node_key="postgresql", name="Database",
            role="test", resource_type="lxc", cpu=1, memory_mb=512, disk_gb=8,
            sort_order=0,
        ))
        file.analysis_json = {**analysis.model_dump(mode="json"), "summary": "Newer teacher edit"}
        file.analysis_revision = 2
        db.add(file)
        db.commit()
        monkeypatch.setattr(script_artifact_service, "get_enabled_template_commands", lambda *a, **kw: [])
        artifact_set = script_artifact_service.create_artifact_set(
            session=db, teaching_class_id=class_id, session_id=uuid.uuid4(),
            name="Source snapshot", template_key="linux", rubric_analysis=analysis,
            source_analysis_revision=1, source_file_id=file.id, created_by=None,
        )
        assert artifact_set.status == "approved"
        db.refresh(file)
        assert file.analysis_revision == 2
        assert file.analysis_json["summary"] == "Newer teacher edit"


def test_plan_that_cannot_return_all_step_identities_fails_before_execution():
    analysis = _analysis({"type": "file_stat", "path": "/tmp/student.txt"}, {"type": "exists", "expected": True})
    step = analysis.items[0].check_steps[0]
    analysis.items[0].check_steps = [step.model_copy(update={"id": f"check-{index}"}) for index in range(500)]
    with pytest.raises(CheckPlanContractError, match="檢查步驟過多"):
        compile_check_plan(analysis, target_node_key="web")
