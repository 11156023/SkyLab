"""Coverage tests for app.ai.teacher_judge.service helper and orchestration paths."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import service as svc
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricChatMessage,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeRubricItem,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


def _settings(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "VLLM_BASE_URL": "http://vllm.test/v1",
        "VLLM_MODEL_NAME": "test-model",
        "VLLM_ENABLE_THINKING": False,
        "VLLM_TIMEOUT": 60,
        "VLLM_MAX_TOKENS": 4096,
        "VLLM_CHAT_MAX_TOKENS": 4096,
        "VLLM_CHAT_TEMPERATURE": 0.2,
        "VLLM_TOP_P": 1.0,
        "VLLM_TOP_K": 20,
        "VLLM_REPETITION_PENALTY": 1.0,
        "VLLM_CHAT_MAX_TOOL_ROUNDS": 6,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _metrics(completion_tokens: int = 10) -> dict[str, Any]:
    return {
        "prompt_tokens": 5,
        "completion_tokens": completion_tokens,
        "total_tokens": 5 + completion_tokens,
        "elapsed_seconds": 0.1,
        "tokens_per_second": 1.0,
        "usage_reported": True,
        "response_model": "m",
    }


def _item(**overrides: Any) -> TeacherJudgeRubricItem:
    values: dict[str, Any] = {"id": "i1", "title": "項目", "detectable": "manual"}
    values.update(overrides)
    return TeacherJudgeRubricItem(**values)


def _general_command() -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="system",
        command_key="system.run_command",
        command_label="一般命令",
        category="system",
        command_template="{argv}",
        description="general",
    )


# ------------------------------------------------------------ focus parsing


def test_conversation_focus_skips_invalid_entries() -> None:
    proposal = None
    assert (
        svc._conversation_focus_from_content(
            json.dumps({"reply": "x", "conversation_focus": "bad"}), proposal=proposal
        )
        is None
    )
    assert (
        svc._conversation_focus_from_content(
            json.dumps({"reply": "x", "conversation_focus": {"requirements": "x"}}),
            proposal=proposal,
        )
        is None
    )
    content = json.dumps(
        {
            "reply": "x",
            "conversation_focus": {
                "turn_kind": "question",
                "requirements": [
                    "bad",
                    {"focus_key": "  "},
                    {"focus_key": "k", "missing_information": "路徑"},
                ],
            },
        }
    )
    focus = svc._conversation_focus_from_content(content, proposal=proposal)
    assert focus == {
        "turn_kind": "question",
        "requirements": [
            {
                "focus_key": "k",
                "status": "needs_information",
                "known_information": [],
                "missing_information": ["路徑"],
            }
        ],
    }


def test_structured_requirement_helpers() -> None:
    focus = {"reply": "x", "conversation_focus": {"turn_kind": "requirement"}}
    assert svc._structured_requirement_needs_candidate(json.dumps(focus)) is False
    assert svc._structured_requirement_target_item("") is None
    payload = {
        "reply": "x",
        "conversation_focus": {
            "requirements": ["bad", {"target_item_id": " "}, {"target_item_id": "i9"}]
        },
    }
    assert svc._structured_requirement_target_item(json.dumps(payload)) == "i9"
    payload["conversation_focus"]["requirements"] = [{"target_item_id": None}]
    assert svc._structured_requirement_target_item(json.dumps(payload)) is None


# ------------------------------------------------------- step normalization


def test_normalize_typed_steps_skip_bad_and_expand_legacy() -> None:
    typed = {
        "id": "s1",
        "title": "版本",
        "collector": {"type": "command", "argv": ["python3", "--version"]},
    }
    steps = svc._normalize_check_steps(
        [
            "bad",
            typed,
            {"collector": {"type": "command"}, "unknown_field": 1},
            {"argv": ["ls", "/tmp"]},
        ]
    )
    assert steps[0].id == "s1"
    assert steps[-1].argv == ["ls", "/tmp"]
    assert len(steps) == 2


def test_normalize_template_command_steps_skip_non_dict() -> None:
    steps = svc._normalize_check_steps(
        [
            "bad",
            {"command_key": "unknown.cmd", "parameters": {"argv": ["cat", "/etc/os"]}},
        ],
        template_key="linux",
        template_commands=[_general_command()],
    )
    assert all(isinstance(step, TeacherJudgeRubricCheckStep) for step in steps)


def test_normalize_legacy_steps_without_catalog() -> None:
    steps = svc._normalize_check_steps(
        [
            "bad",
            {"argv": [], "cwd": "/srv"},
            {"argv": ["ls"], "cwd": " /srv ", "timeout_seconds": "5"},
            {"argv": ["ls"], "cwd": "  "},
            {"command_key": "x.y"},
            {"template_key": "", "command_key": ""},
            {"command_key": "svc.status", "command_label": "狀態", "parameters": "bad"},
        ],
        template_key="linux",
    )
    assert steps[0].argv == ["ls"] and steps[0].cwd == "/srv"
    assert steps[0].timeout_seconds == 5
    assert steps[1].cwd is None
    assert steps[1].timeout_seconds == svc.DEFAULT_SYSTEM_COMMAND_TIMEOUT_SECONDS
    assert [step.command_key for step in steps[2:]] == ["x.y", "svc.status"]
    assert steps[3].command_label == "狀態"


def test_normalize_rubric_items_bad_shapes() -> None:
    assert svc._normalize_rubric_items("bad") == []
    items = svc._normalize_rubric_items(
        ["bad", {"title": "t", "judgement_mode": "robot"}]
    )
    assert len(items) == 1
    assert items[0].judgement_mode == "ai"


def test_context_and_latest_request_helpers() -> None:
    assert svc._rubric_context_data("{bad") == {}
    assert svc._rubric_context_data("[]") == {}
    assert (
        svc._latest_teacher_request(
            [TeacherJudgeRubricChatMessage(role="assistant", content="hi")]
        )
        == ""
    )


def test_finalizer_completion_blockers_skip_invalid_inputs() -> None:
    blockers = svc._finalizer_completion_blockers(
        ["bad", {"id": "i1"}],
        [{"item": "not-an-item"}, {"item": _item(id="i2")}],
        template_commands=None,
        machine_entries=None,
    )
    assert isinstance(blockers, list)


def test_duplicate_title_owner_paths() -> None:
    assert svc._duplicate_title_owner("   ", [], []) is None
    assert (
        svc._duplicate_title_owner(
            "Same", ["bad", {"id": "", "title": "same"}], [{"item": None}]
        )
        is None
    )
    staged = [{"item": _item(id="i7", title="SAME")}]
    assert svc._duplicate_title_owner("same", "bad", staged) == ("i7", "SAME")


# ----------------------------------------------------- candidate rejection


def test_candidate_rejection_machine_issue() -> None:
    item = _item(target_node_key="web", peer_node_key="web")
    reason = svc._proposal_candidate_rejection(
        item,
        {},
        capability_declared=False,
        ready_only=True,
        template_commands=None,
    )
    assert reason is not None and "機器目標無效" in reason


def test_candidate_rejection_recoverable_parameter_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gap = "要檢查的檔案、服務或記錄範圍"
    monkeypatch.setattr(svc, "_recoverable_parameter_gaps", lambda _item: [gap])
    item = _item(detectable="partial", missing_information=[gap])
    reason = svc._proposal_candidate_rejection(
        item,
        {"id": "i1", "detectable": "partial"},
        capability_declared=False,
        ready_only=True,
        template_commands=None,
    )
    assert reason is not None and "argv（單一非空的命令字串 list）" in reason


# ------------------------------------------------------ teacher-facing text


def test_teacher_missing_gap_reply_location_result_and_remaining() -> None:
    item = _item(
        title="版本輸出",
        detectable="partial",
        missing_information=["檔案路徑與服務名稱", "預期結果", "其他事項"],
    )
    reply = svc._teacher_missing_gap_reply(item)
    assert "檢查位置、通過方式與「其他事項」" in reply
    assert "請補充檔案或程式的絕對路徑，以及服務、連接埠或記錄範圍" in reply
    assert "請補充預期文字或內容、版本" in reply
    assert "請補充「其他事項」" in reply

    plain = _item(title="zzz", detectable="partial", missing_information=["預期答案"])
    assert "請補充可直接比對的預期結果" in svc._teacher_missing_gap_reply(plain)
    assert svc._teacher_result_hints(_item(title="連接埠狀態")) == ["Port", "狀態"]


def test_proposal_unavailable_reply_variants() -> None:
    first = _item(id="a", title="同名", detectable="partial", missing_information=["x"])
    second = _item(
        id="b", title="同名", detectable="partial", missing_information=["y"]
    )
    reply = svc._proposal_unavailable_reply([first, second], [])
    assert reply.count("同名") == 1

    manual = _item(id="i1", title="壞 auto")
    raw_items = [
        "bad",
        {
            "id": "i1",
            "detectable": "auto",
            "check_steps": ["bad", {"template_key": "x", "command_key": "y"}],
        },
    ]
    reply = svc._proposal_unavailable_reply([manual], raw_items, [])
    assert "x/y" in reply

    unsupported = svc._proposal_unavailable_reply(
        [manual], [{"id": "i1", "detectable": "manual"}]
    )
    assert "仍未建立提案" in unsupported

    same = svc._proposal_unavailable_reply(
        [_item(detectable="auto", detection_method="m")], []
    )
    assert "相同內容" in same


def test_dedupe_helpers_and_title_coverage() -> None:
    untitled = (_item(title="  "), {}, "r")
    kept = svc._dedupe_rejected_ops_keep_latest([untitled, untitled])
    assert len(kept) == 2

    outcomes = [
        "raw",
        {"status": "rejected", "title": ""},
        {"status": "rejected", "title": "A"},
        {"status": "rejected", "title": "a", "n": 2},
    ]
    result = svc._dedupe_tool_outcomes_keep_latest(outcomes)  # type: ignore[arg-type]
    assert result[0] == "raw"
    assert result[2]["n"] == 2

    assert svc._title_covered_by_reply("anything", "") is False
    assert svc._title_covered_by_reply("檢查 Nginx 服務", "nginx") is True
    assert svc._title_covered_by_reply("沒有", "「」") is False
    assert svc._title_covered_by_reply("請看 main.log", "取得 main.log 檔案") is True


# --------------------------------------------------------------- vLLM client


async def test_request_vllm_with_retry_deadline_and_second_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(httpx.ReadTimeout):
        await svc._request_vllm_with_retry(
            {}, profile=svc.VLLMRequestProfile.CONFIGURED_TEXT, timeout=0
        )

    calls: list[float] = []

    async def failing(payload: Any, *, profile: Any, timeout: float) -> Any:
        calls.append(timeout)
        raise httpx.ConnectError("down")

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(
        svc, "teacher_judge_client", SimpleNamespace(create_chat_completion=failing)
    )
    monkeypatch.setattr(
        svc,
        "asyncio",
        SimpleNamespace(
            wait_for=asyncio.wait_for,
            sleep=no_sleep,
            TimeoutError=asyncio.TimeoutError,
        ),
    )
    with pytest.raises(httpx.ConnectError):
        await svc._request_vllm_with_retry(
            {}, profile=svc.VLLMRequestProfile.CONFIGURED_TEXT, timeout=30
        )
    assert len(calls) == 2


async def test_call_vllm_message_success_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "settings", _settings())
    responses: list[Any] = []

    async def fake_request(payload: Any, *, profile: Any, timeout: float) -> Any:
        step = responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(svc, "_request_vllm_with_retry", fake_request)

    responses.append({"choices": [{"message": {"content": 42}}]})
    message, metrics = await svc._call_vllm_message({"messages": []})
    assert message["content"] == "42"
    assert metrics["usage_reported"] is False
    assert metrics["response_model"] is None

    responses.append({"choices": [{"message": "text"}]})
    with pytest.raises(HTTPException) as exc:
        await svc._call_vllm_message({"messages": []})
    assert exc.value.status_code == 502

    responses.append(httpx.ReadTimeout("slow"))
    with pytest.raises(HTTPException) as exc:
        await svc._call_vllm_message({"messages": []})
    assert exc.value.status_code == 504

    request = httpx.Request("POST", "http://vllm.test")
    responses.append(
        httpx.HTTPStatusError(
            "bad", request=request, response=httpx.Response(500, request=request)
        )
    )
    with pytest.raises(HTTPException) as exc:
        await svc._call_vllm_message({"messages": []})
    assert exc.value.status_code == 502


def test_tool_arguments_and_embedded_calls() -> None:
    assert svc._tool_arguments({"a": 1}) == {"a": 1}
    assert svc._tool_arguments(5) is None
    assert svc._contains_embedded_tool_call("<tool_call>x") is True
    assert svc._contains_embedded_tool_call("```json\n{bad json}\n```") is False
    nested = '```json\n{"tool_call": {"name": "list_checklist"}}\n```'
    assert svc._contains_embedded_tool_call(nested) is True


def test_reply_payload_object_variants() -> None:
    assert svc._is_reply_payload_object({"conversation_focus": {}}) is True
    leftover, payload = svc._reply_payload_object('前言 {"x": 1} 後記')
    assert payload is None
    assert leftover == '前言 {"x": 1} 後記'


# -------------------------------------------------------- tool dispatching


def _dispatch(
    name: str,
    arguments: dict[str, Any],
    *,
    snapshot: Any = None,
    staged: list[dict[str, Any]] | None = None,
    read_ids: set[str] | None = None,
    machine_entries: list[dict[str, Any]] | None = None,
    template_commands: list[TeacherJudgeTemplateCommand] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tool_calls: list[dict[str, Any]] = []
    result = svc._dispatch_checklist_tool(
        name,
        arguments,
        snapshot_items=snapshot if snapshot is not None else [],
        analysis_revision=1,
        template_key="linux",
        template_commands=template_commands,
        machine_entries=machine_entries,
        ready_only=True,
        read_ids=read_ids if read_ids is not None else set(),
        staged_ops=staged if staged is not None else [],
        rejected_ops=[],
        tool_calls=tool_calls,
    )
    return result, tool_calls


_MACHINES = [{"node_key": "web", "display_label": "P1"}]


def test_dispatch_rejects_bad_read_arguments() -> None:
    assert "error" in _dispatch("list_checklist", {"x": 1})[0]
    assert "error" in _dispatch("get_checklist_item", {"id": "a", "x": 1})[0]
    assert "項目 ID" in _dispatch("get_checklist_item", {"id": " "})[0]["error"]
    assert "error" in _dispatch("unknown_tool", {})[0]


def test_dispatch_create_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "error" in _dispatch("create_checklist_item", {"title": "a", "bogus": 1})[0]
    assert "title" in _dispatch("create_checklist_item", {"title": " "})[0]["error"]
    result, _ = _dispatch(
        "create_checklist_item",
        {"title": "新項目", "target_node_key": "P9"},
        machine_entries=_MACHINES,
    )
    assert "node_key" in result["error"]

    monkeypatch.setattr(svc, "_normalize_rubric_items", lambda *_a, **_k: [])
    result, _ = _dispatch("create_checklist_item", {"title": "新項目"})
    assert "無法解析" in result["error"]


def test_dispatch_edit_rejections(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = [{"id": "i1", "title": "舊", "detectable": "manual"}]
    assert "error" in _dispatch("edit_checklist_item", {"id": "i1", "x": 1})[0]
    assert "ID" in _dispatch("edit_checklist_item", {"id": ""})[0]["error"]
    assert "找不到" in _dispatch("edit_checklist_item", {"id": "zz"})[0]["error"]

    result, _ = _dispatch(
        "edit_checklist_item",
        {"id": "i1", "target_node_key": "P9"},
        snapshot=snapshot,
        read_ids={"i1"},
        machine_entries=_MACHINES,
    )
    assert "node_key" in result["error"]

    original_normalize = svc._normalize_rubric_items
    monkeypatch.setattr(svc, "_normalize_rubric_items", lambda *_a, **_k: [])
    result, _ = _dispatch(
        "edit_checklist_item",
        {"id": "i1", "title": "新"},
        snapshot=snapshot,
        read_ids={"i1"},
    )
    assert "無法解析" in result["error"]

    monkeypatch.setattr(svc, "_normalize_rubric_items", original_normalize)
    monkeypatch.setattr(
        svc,
        "typed_item_issues",
        lambda *_a, **_k: [{"field": "check_steps", "message": "壞掉"}],
    )
    result, _ = _dispatch(
        "edit_checklist_item",
        {"id": "i1", "title": "新"},
        snapshot=snapshot,
        read_ids={"i1"},
    )
    assert result["reason_code"] == "check_plan_contract_invalid"


# ------------------------------------------------------------ tool loop


def _scripted(responses: list[dict[str, Any]]):
    calls: list[dict[str, Any]] = []

    async def fake_call(payload: dict[str, Any], timeout: float = 60.0) -> Any:
        calls.append(payload)
        return responses.pop(0), _metrics()

    return calls, fake_call


async def test_tool_loop_ignores_non_dict_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "settings", _settings())
    _calls, fake = _scripted(
        [{"role": "assistant", "content": "最後回覆", "tool_calls": ["bad"]}]
    )
    monkeypatch.setattr(svc, "_call_vllm_message", fake)
    content, *_rest = await svc._run_proposal_tool_loop(
        {"messages": [], "max_tokens": 512},
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=None,
        machine_entries=None,
        analysis_revision=1,
        rubric_available=True,
    )
    assert content == "最後回覆"


# --------------------------------------------------------- chat orchestration


async def test_model_not_configured_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "settings", _settings(VLLM_MODEL_NAME=""))
    message = TeacherJudgeRubricChatMessage(role="user", content="hi")
    with pytest.raises(HTTPException):
        await svc.summarize_conversation([message])
    with pytest.raises(HTTPException):
        await svc.chat_with_rubric([message], "{}")
    with pytest.raises(HTTPException):
        await svc.extract_attachment_requirements("doc")
    with pytest.raises(HTTPException):
        await svc.analyze_attachments_itemwise(
            rubric_context="{}", attachment_context="x"
        )


def _patch_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    content: str,
    staged: list[dict[str, Any]],
    completion_tokens: int = 10,
) -> None:
    async def fake_loop(*_args: Any, **_kwargs: Any) -> Any:
        return content, _metrics(completion_tokens), staged, [], []

    monkeypatch.setattr(svc, "settings", _settings())
    monkeypatch.setattr(svc, "_run_proposal_tool_loop", fake_loop)


def _staged_entry(title: str = "新項目") -> dict[str, Any]:
    item = _item(id="item-1", title=title)
    return {"item": item, "operation": "add", "raw": item.model_dump(mode="json")}


async def test_chat_blocks_action_when_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_loop(
        monkeypatch,
        content=json.dumps({"reply": "好了", "proposal_status": "ready"}),
        staged=[_staged_entry()],
        completion_tokens=1000,
    )
    result = await svc.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="新增")],
        json.dumps({"items": []}),
        rubric_available=True,
        completion_token_budget=1000,
    )
    assert result.reply == svc.TEACHER_ADHERENCE_FALLBACK
    assert result.proposal is None
    assert result.proposal_status == "none"


async def test_chat_empty_reply_with_staged_ops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_loop(monkeypatch, content="", staged=[_staged_entry("磁碟空間")])
    result = await svc.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="新增")],
        json.dumps({"items": []}),
        rubric_available=True,
    )
    assert "「磁碟空間」整理成提案" in result.reply
    assert result.proposal and result.proposal[0]["operation"] == "add"


async def test_chat_refine_empty_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_loop(monkeypatch, content="", staged=[])
    with pytest.raises(HTTPException) as exc:
        await svc.chat_with_rubric(
            [TeacherJudgeRubricChatMessage(role="user", content="潤飾")],
            json.dumps({"items": []}),
            is_refine=True,
            rubric_available=True,
        )
    assert exc.value.status_code == 502
    assert "無法確認是否需要修改" in exc.value.detail


async def test_chat_reply_falls_back_when_no_budget_for_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_loop(
        monkeypatch,
        content=json.dumps({"reply": "你好", "proposal_status": "none"}),
        staged=[],
        completion_tokens=1000,
    )
    result = await svc.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="你好")],
        json.dumps({"items": []}),
        rubric_available=True,
        completion_token_budget=1000,
    )
    assert result.reply == svc.TEACHER_ADHERENCE_FALLBACK


# ---------------------------------------------------------- attachments


def test_parse_attachment_extraction_errors() -> None:
    assert svc._parse_attachment_extraction("[1]")[1] is not None
    assert svc._parse_attachment_extraction(json.dumps({"error": " 壞 "})) == (
        [],
        "壞",
    )
    assert svc._parse_attachment_extraction(json.dumps({"items": "x"})) == (
        [],
        "AI 拆解結果缺少項目清單",
    )


def _source() -> dict[str, Any]:
    return {
        "source_index": 1,
        "source_label": "第 1 列",
        "source_item_id": "src-1",
        "title": "需求",
        "description": "說明",
    }


def test_itemwise_result_variants() -> None:
    needs = svc.TeacherJudgeChatResult(
        reply="缺資訊",
        proposal=None,
        metrics=_metrics(),  # type: ignore[arg-type]
        conversation_focus={"requirements": ["bad", {"missing_information": [" "]}]},
        proposal_status="needs_information",
    )
    assert svc._itemwise_focus_missing(needs) == []
    assert svc._itemwise_result_from_chat(_source(), needs)["status"] == (
        "needs_information"
    )

    unsupported = svc.TeacherJudgeChatResult(
        reply="不支援",
        proposal=None,
        metrics=_metrics(),  # type: ignore[arg-type]
        proposal_status="unsupported",
    )
    row = svc._itemwise_result_from_chat(_source(), unsupported)
    assert row["status"] == "unsupported"

    error = svc.TeacherJudgeChatResult(
        reply="壞了",
        proposal=None,
        metrics=_metrics(),  # type: ignore[arg-type]
        proposal_status=None,
    )
    assert svc._itemwise_result_from_chat(_source(), error)["status"] == (
        "analysis_error"
    )

    failure = svc._itemwise_error_result(
        _source(), HTTPException(status_code=502, detail={"message": "上游失敗"})
    )
    assert failure["detail"] == "AI 回覆失敗：上游失敗"

    reply = svc._itemwise_reply([{**row, "detail": ""}], 1)
    assert "目前無法安全取證：沒有合適的檢查方式" in reply


async def test_itemwise_rejects_conflicting_merged_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(svc, "settings", _settings())

    async def fake_extract(*_args: Any, **_kwargs: Any) -> Any:
        return [_source()], None, _metrics()

    operation = {"id": "item-9", "title": "需求", "detectable": "manual"}

    async def fake_analyze(**_kwargs: Any) -> Any:
        return svc.TeacherJudgeChatResult(
            reply="ok",
            proposal=[dict(operation, operation="add")],
            metrics=_metrics(),  # type: ignore[arg-type]
            proposal_status="ready",
        )

    monkeypatch.setattr(svc, "extract_attachment_requirements", fake_extract)
    monkeypatch.setattr(svc, "analyze_requirement_item", fake_analyze)
    monkeypatch.setattr(
        svc, "typed_item_issues", lambda *_a, **_k: [{"message": "conflict"}]
    )
    result = await svc.analyze_attachments_itemwise(
        rubric_context=json.dumps({"items": []}),
        attachment_context="附件",
    )
    assert result.item_results[0]["status"] == "analysis_error"
    assert result.proposal is None
