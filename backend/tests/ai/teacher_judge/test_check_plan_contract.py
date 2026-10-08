from __future__ import annotations

import asyncio
import copy
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import (
    file_service,
    service,
    session_chat_service,
    session_service,
)
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.check_plan_contract import (
    typed_item_issues,
    typed_step_issues,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricItem,
    TeacherJudgeSessionMessageCreateRequest,
)
from app.api.routes import teacher_judge_sessions
from app.infrastructure.ai import VLLMRequestProfile
from app.models.teacher_judge_session import TeacherJudgeSession
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    scripted_vllm,
    tool_call_message,
)


def test_teacher_judge_request_profile_matches_payload_shape():
    assert (
        service._request_profile({"tools": [{"type": "function"}]})
        is VLLMRequestProfile.COMPLEX_AGENT
    )
    assert (
        service._request_profile({"response_format": {"type": "json_object"}})
        is VLLMRequestProfile.STRUCTURED_OBJECT
    )
    assert service._request_profile({}) is VLLMRequestProfile.CONFIGURED_TEXT


def version_item() -> dict:
    return {
        "id": "item-8c15b3a4",
        "title": "Python版本檢查",
        "detectable": "auto",
        "judgement_mode": "ai",
        "detection_method": "Python 版本包含 3.12",
        "target_node_key": "web",
        "check_steps": [
            {
                "id": "python.version",
                "title": "Python 版本",
                "collector": {"type": "command", "argv": ["python3", "--version"]},
                "assertion": {"type": "text_contains", "expected": "3.12"},
            }
        ],
    }


def alias_steps() -> list[dict]:
    steps = copy.deepcopy(version_item()["check_steps"])
    steps[0]["collector"]["collector_type"] = steps[0]["collector"].pop("type")
    steps[0]["assertion"]["assertion_type"] = steps[0]["assertion"].pop("type")
    steps[0]["assertion"]["expected_value"] = steps[0]["assertion"].pop("expected")
    return steps


def install_responses(monkeypatch, responses):
    patch_teacher_judge_vllm_settings(monkeypatch)
    calls, fake = scripted_vllm(
        [
            (
                response,
                {"completion_tokens": 10, "prompt_tokens": 20, "usage_reported": True},
            )
            for response in responses
        ]
    )
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    return calls


async def chat(*, snapshot=None, refine=False):
    return await service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="請核對 Python 版本包含 3.12")],
        rubric_context=json.dumps({"items": snapshot or []}, ensure_ascii=False),
        is_refine=refine,
        template_commands=[],
        rubric_available=True,
    )


def test_chat_and_finalizer_expose_identical_model_derived_contract():
    assert service._build_proposal_tools() == service._build_proposal_tools(
        finalizer=True
    )
    issues = typed_step_issues(alias_steps())
    assert {issue["field"] for issue in issues} == {
        "check_steps[0].collector",
        "check_steps[0].assertion",
    }
    assert all(issue["step_id"] == "python.version" for issue in issues)


@pytest.mark.parametrize("refine", [False, True])
@pytest.mark.asyncio
async def test_incident_alias_error_is_repaired_without_losing_steps(
    monkeypatch, refine
):
    item = version_item()
    legacy = copy.deepcopy(item)
    legacy["check_steps"] = [{"argv": ["python3", "--version"], "timeout_seconds": 30}]
    calls = install_responses(
        monkeypatch,
        [
            tool_call_message("get_checklist_item", {"id": item["id"]}),
            tool_call_message(
                "edit_checklist_item", {"id": item["id"], "check_steps": alias_steps()}
            ),
            reply_message("我會嘗試修正後重新提交，請稍候。", "none"),
            tool_call_message(
                "edit_checklist_item",
                {"id": item["id"], "check_steps": item["check_steps"]},
            ),
            reply_message("已整理提案，請確認後套用。", "ready"),
        ],
    )
    result = await chat(snapshot=[legacy], refine=refine)
    assert len(calls) == 5
    error = json.loads(calls[2]["messages"][-1]["content"])
    assert error["reason_code"] == "check_plan_contract_invalid"
    assert error["retryable"] is True
    assert "expected_value" in error["repair_hint"]
    assert calls[3]["tool_choice"]["function"]["name"] == "edit_checklist_item"
    assert result.proposal[0]["check_steps"][0]["assertion"]["expected"] == "3.12"
    assert any(outcome.get("resolved") for outcome in result.tool_calls)
    assert "請稍候" not in result.reply
    assert "collector" not in legacy["check_steps"][0]


@pytest.mark.asyncio
async def test_identical_tool_errors_stop_after_one_retry_and_survive_adherence(
    monkeypatch,
):
    args = {key: value for key, value in version_item().items() if key != "id"}
    args["check_steps"] = alias_steps()
    calls = install_responses(
        monkeypatch,
        [
            tool_call_message("create_checklist_item", args),
            tool_call_message("create_checklist_item", args),
        ],
    )
    from app.ai.role_contracts import AdherenceReason, AdherenceResult, AdherenceVerdict

    async def block(*_args, **_kwargs):
        return AdherenceResult(AdherenceVerdict.BLOCK, AdherenceReason.ROLE_DRIFT)

    monkeypatch.setattr(service, "check_adherence", block)
    result = await chat()
    assert len(calls) == 2
    assert result.proposal is None
    assert "Python版本檢查" in result.reply
    assert "本次修正已停止" in result.reply
    assert result.tool_calls[-1]["attempt"] == 2
    assert result.tool_calls[-1]["retryable"] is False
    assert result.conversation_focus["requirements"][0]["status"] == "analysis_error"


@pytest.mark.asyncio
async def test_duplicate_step_ids_are_rejected_early_without_losing_valid_proposals(
    monkeypatch,
):
    valid = {key: value for key, value in version_item().items() if key != "id"}
    duplicate = {**valid, "title": "另一個版本檢查"}
    calls = install_responses(
        monkeypatch,
        [
            tool_call_message("create_checklist_item", valid),
            tool_call_message("create_checklist_item", duplicate),
            tool_call_message("create_checklist_item", duplicate),
        ],
    )
    result = await chat()
    assert len(calls) == 3
    assert len(result.proposal) == 1
    assert result.proposal[0]["title"] == valid["title"]
    assert "重複" in result.tool_calls[-1]["reason"]
    assert "已整理通過驗證的提案" in result.reply
    assert result.proposal_status == "ready"


@pytest.mark.asyncio
async def test_empty_edit_cannot_claim_a_failed_repair_succeeded(monkeypatch):
    item = version_item()
    install_responses(
        monkeypatch,
        [
            tool_call_message("get_checklist_item", {"id": item["id"]}),
            tool_call_message(
                "edit_checklist_item", {"id": item["id"], "check_steps": alias_steps()}
            ),
            tool_call_message("edit_checklist_item", {"id": item["id"]}),
            reply_message("我會修正後再試，請稍候。", "none"),
            tool_call_message(
                "edit_checklist_item", {"id": item["id"], "check_steps": alias_steps()}
            ),
        ],
    )
    result = await chat(snapshot=[item])
    assert result.proposal is None
    assert "本次修正已停止" in result.reply
    assert not any(outcome.get("resolved") for outcome in result.tool_calls)


@pytest.mark.asyncio
async def test_corrected_lookup_resolves_its_error_without_replaying_the_edit(monkeypatch):
    item = version_item()
    replacement = copy.deepcopy(item["check_steps"])
    replacement[0]["title"] = "Python 版本資訊"
    calls = install_responses(monkeypatch, [
        tool_call_message("get_checklist_item", {"id": "mistyped-id"}),
        tool_call_message("get_checklist_item", {"id": item["id"]}),
        tool_call_message("edit_checklist_item", {"id": item["id"], "check_steps": replacement}),
        reply_message("已整理修改提案。", "ready"),
    ])
    result = await chat(snapshot=[item])
    assert len(calls) == 4
    assert len(result.proposal) == 1
    assert result.tool_calls[0]["resolved"] is True
    assert "本次修正已停止" not in result.reply


@pytest.mark.asyncio
async def test_malformed_json_is_retried_and_resolved_after_valid_native_call(monkeypatch):
    args = {key: value for key, value in version_item().items() if key != "id"}
    malformed = tool_call_message("create_checklist_item", args)
    malformed["tool_calls"][0]["function"]["arguments"] = "{invalid-json"
    calls = install_responses(monkeypatch, [
        malformed,
        tool_call_message("create_checklist_item", args),
        reply_message("已整理提案，請確認後套用。", "ready"),
    ])
    result = await chat()
    assert len(calls) == 3
    error = json.loads(calls[1]["messages"][-1]["content"])
    assert error["reason_code"] == "tool_arguments_invalid"
    assert error["retryable"] is True
    assert "JSON" in error["repair_hint"]
    assert len(result.proposal) == 1
    assert result.tool_calls[0]["resolved"] is True
    assert "本次修正已停止" not in result.reply


@pytest.mark.parametrize("failure", ["arguments", "adherence"])
@pytest.mark.asyncio
async def test_route_keeps_failed_finalizer_unready_even_if_saved_plan_is_valid(monkeypatch, failure):
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    original = version_item()
    file.analysis_json = TeacherJudgeRubricAnalysis(items=[original]).model_dump(mode="json")
    session = TeacherJudgeSession(teaching_class_id=class_id, selected_file_id=file.id, title="Contract regression")
    db.add_all([file, session])
    db.commit()
    responses = [
        tool_call_message("get_checklist_item", {"id": original["id"]}),
        tool_call_message("edit_checklist_item", {"id": original["id"], "check_steps": alias_steps()}),
        tool_call_message("edit_checklist_item", {"id": original["id"], "check_steps": alias_steps()}),
    ]
    if failure == "adherence":
        from app.ai.role_contracts import (
            AdherenceReason,
            AdherenceResult,
            AdherenceVerdict,
        )

        corrected_steps = copy.deepcopy(original["check_steps"])
        corrected_steps[0]["title"] = "Python 3.12 版本取證"
        responses[1] = tool_call_message("edit_checklist_item", {"id": original["id"], "check_steps": corrected_steps})
        responses[2] = reply_message("已整理提案。", "ready")

        async def block(*_args, **_kwargs):
            return AdherenceResult(AdherenceVerdict.BLOCK, AdherenceReason.ROLE_DRIFT)

        monkeypatch.setattr(service, "check_adherence", block)
    calls = install_responses(monkeypatch, responses)
    monkeypatch.setattr(teacher_judge_sessions, "_access", lambda *_args: None)
    monkeypatch.setattr(teacher_judge_sessions, "machine_context_entries", lambda *_args: [{"node_key": "web", "display_label": "P1"}])
    monkeypatch.setattr(session_chat_service, "load_class_machine_nodes", lambda *_args: [SimpleNamespace(node_key="web")])
    response = await teacher_judge_sessions.create_message(
        class_id, session.id,
        TeacherJudgeSessionMessageCreateRequest(content="重新核對完整檢查表", is_refine=True, analysis_revision=1),
        db, SimpleNamespace(id=uuid.uuid4()),
    )
    metadata = response.assistant_message.metadata_json
    assert len(calls) == 3
    assert response.rubric_proposal == []
    assert metadata["script_ready"] is False
    assert metadata["status"] == "analysis_error"
    assert len(metadata["item_results"]) == 1
    assert metadata["item_results"][0]["title"] == "Python版本檢查"
    assert metadata["item_results"][0]["issues"]
    assert metadata["tool_calls"][-1]["retryable"] is False
    assert response.assistant_message.content.count("「Python版本檢查」") == 1
    assert file.analysis_revision == 1
    assert file.analysis_json["items"][0]["check_steps"][0]["assertion"]["expected"] == "3.12"
    db.close()


@pytest.mark.parametrize(
    "mutation, marker",
    [
        (lambda step: step.pop("assertion"), "assertion"),
        (
            lambda step: step["collector"].update(argv=["python3", "-c", "print(1)"]),
            "inline",
        ),
        (
            lambda step: step["collector"].update(
                argv=["systemctl", "restart", "nginx"]
            ),
            "服務",
        ),
        (
            lambda step: step.update(assertion={"type": "exists", "expected": True}),
            "不支援",
        ),
    ],
)
def test_new_auto_items_share_compiler_semantic_validation(mutation, marker):
    item = version_item()
    mutation(item["check_steps"][0])
    issues = typed_item_issues(TeacherJudgeRubricItem.model_validate(item))
    assert issues
    assert any(marker in issue["message"] for issue in issues)


def test_save_rejects_new_legacy_plan_but_preserves_unchanged_old_data(monkeypatch):
    db = make_session()
    class_id = uuid.uuid4()
    file = make_teacher_judge_file(db, class_id)
    monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *_args: [])
    legacy = version_item()
    legacy["target_node_key"] = None
    legacy["check_steps"] = [{"argv": ["python3", "--version"], "timeout_seconds": 30}]
    analysis = TeacherJudgeRubricAnalysis(items=[legacy])
    with pytest.raises(HTTPException) as exc:
        file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id, analysis=analysis
        )
    assert exc.value.status_code == 422
    assert file.analysis_revision == 1
    assert file.analysis_json["items"] == []
    file.analysis_json = analysis.model_dump(mode="json")
    db.add(file)
    db.commit()
    analysis.items[0].checked = True
    updated = file_service.update_file_analysis(
        session=db, teaching_class_id=class_id, file_id=file.id, analysis=analysis
    )
    assert updated.analysis_revision == 2
    analysis.items[0].check_steps[0].parameters["argv"] = ["uname", "-a"]
    with pytest.raises(HTTPException):
        file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id, analysis=analysis
        )
    assert file.analysis_revision == 2
    db.close()


def test_two_legacy_steps_render_once_with_title_and_preserve_each_issue():
    item = version_item()
    item["check_steps"] = [
        {"argv": ["python3", "--version"], "timeout_seconds": 30},
        {"argv": ["uname", "-a"], "timeout_seconds": 30},
    ]
    blockers = get_script_generation_blockers(
        TeacherJudgeRubricAnalysis(items=[item]), [], require_typed_plan=True
    )
    workflow = session_service.reanalysis_workflow_message(
        blockers,
        source_file_id=None,
        analysis_revision=12,
        assistant_reply="我會嘗試修正格式後重新提交，請稍候。",
    )
    assert workflow["content"].count("「Python版本檢查」") == 1
    assert "請稍候" not in workflow["content"]
    rows = workflow["metadata"]["item_results"]
    assert len(rows) == 1
    assert {issue["step_index"] for issue in rows[0]["issues"]} == {0, 1}
    assert workflow["metadata"]["script_ready"] is False


@pytest.mark.parametrize(
    "status, expected_calls", [(429, 2), (503, 2), (400, 1), (401, 1), (504, 1)]
)
@pytest.mark.asyncio
async def test_model_http_retry_is_bounded_and_only_for_temporary_rejection(
    monkeypatch, status, expected_calls
):
    calls = []
    request = httpx.Request("POST", "http://model/chat/completions")

    async def completion(payload, *, profile, timeout):
        calls.append((payload, profile, timeout))
        if len(calls) == 1 or status == 503:
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError(
                "model error", request=request, response=response
            )
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(
        service.teacher_judge_client, "create_chat_completion", completion
    )
    if status in {400, 401, 503, 504}:
        with pytest.raises(httpx.HTTPStatusError):
            await service._request_vllm_with_retry(
                {"messages": []},
                profile=VLLMRequestProfile.CONFIGURED_TEXT,
                timeout=1,
            )
    else:
        assert await service._request_vllm_with_retry(
            {"messages": []},
            profile=VLLMRequestProfile.CONFIGURED_TEXT,
            timeout=1,
        )
    assert len(calls) == expected_calls
    assert all(profile is VLLMRequestProfile.CONFIGURED_TEXT for _, profile, _ in calls)
    assert all(0 < timeout <= 1 for _, _, timeout in calls)


@pytest.mark.asyncio
async def test_model_read_timeout_does_not_replay_an_ambiguous_generation(monkeypatch):
    calls = []

    async def completion(_payload, *, profile, timeout):
        calls.append((profile, timeout))
        raise httpx.ReadTimeout("request timed out")

    monkeypatch.setattr(
        service.teacher_judge_client, "create_chat_completion", completion
    )
    with pytest.raises(httpx.ReadTimeout):
        await service._request_vllm_with_retry(
            {}, profile=VLLMRequestProfile.CONFIGURED_TEXT, timeout=1
        )
    assert len(calls) == 1
    assert calls[0][0] is VLLMRequestProfile.CONFIGURED_TEXT


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
@pytest.mark.asyncio
async def test_model_connection_retry_preserves_payload_and_deadline(monkeypatch, error_type):
    calls = []

    async def completion(payload, *, profile, timeout):
        calls.append((copy.deepcopy(payload), profile, timeout))
        if len(calls) == 1:
            raise error_type("connection unavailable")
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(service.teacher_judge_client, "create_chat_completion", completion)
    payload = {"messages": [{"role": "user", "content": "version"}], "max_tokens": 100}
    result = await service._request_vllm_with_retry(
        payload, profile=VLLMRequestProfile.CONFIGURED_TEXT, timeout=1
    )
    assert result["choices"][0]["message"]["content"] == "ok"
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == payload
    assert calls[0][1] is calls[1][1] is VLLMRequestProfile.CONFIGURED_TEXT
    assert 0 < calls[1][2] < calls[0][2] <= 1


@pytest.mark.asyncio
async def test_model_deadline_also_bounds_client_internal_retries(monkeypatch):
    calls = []
    cancelled = []

    async def completion(_payload, *, profile, timeout):
        calls.append((profile, timeout))
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(service.teacher_judge_client, "create_chat_completion", completion)
    with pytest.raises(httpx.ReadTimeout):
        await service._request_vllm_with_retry(
            {}, profile=VLLMRequestProfile.CONFIGURED_TEXT, timeout=0.01
        )
    assert len(calls) == 1
    assert calls[0][0] is VLLMRequestProfile.CONFIGURED_TEXT
    assert cancelled == [True]
