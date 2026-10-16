"""Exercise Finalizer transitions through the real client and model adapter."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import service
from app.infrastructure.ai.vllm_client import VLLMClient
from tests.ai.teacher_judge.helpers import (
    patch_teacher_judge_vllm_settings,
    reply_message,
    tool_call_message,
)


def item():
    return {
        "id": "python", "title": "Python version", "detectable": "auto",
        "judgement_mode": "ai", "target_node_key": "web",
        "detection_method": "Read the Python version", "missing_information": [],
        "check_steps": [{
            "id": "version", "title": "Read version",
            "collector": {"type": "command", "argv": ["python3", "--version"]},
            "assertion": {"type": "returncode_equals", "expected": 0},
        }],
    }


def completion(message, *, finish=None, tokens=10, usage=True):
    if isinstance(message, str):
        message = {"role": "assistant", "content": message}
    return {
        "model": "gpt-oss-120B",
        "choices": [{
            "message": message,
            "finish_reason": finish or ("tool_calls" if message.get("tool_calls") else "stop"),
        }],
        **({"usage": {"prompt_tokens": 100, "completion_tokens": tokens,
                      "total_tokens": 100 + tokens}} if usage else {}),
    }


@pytest.fixture
async def transport(monkeypatch):
    patch_teacher_judge_vllm_settings(monkeypatch)
    service.settings.VLLM_MODEL_NAME = "gpt-oss-120B"
    service.settings.VLLM_BASE_URL = "http://vllm.test/v1"
    responses = []
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "gpt-oss-120B"
        assert payload["reasoning_effort"] == "low"
        assert responses, "Unexpected extra generation"
        return httpx.Response(200, json=responses.pop(0))

    client = VLLMClient(base_url=service.settings.VLLM_BASE_URL, api_key="")
    client._http_client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(service, "teacher_judge_client", client)
    yield responses, requests
    await client.aclose()


async def refine():
    return await service.chat_with_rubric(
        [SimpleNamespace(role="user", content="核對目前檢查表。")],
        json.dumps({"items": [item()]}),
        is_refine=True, rubric_available=True, template_commands=[],
        machine_entries=[{"node_key": "web"}],
    )


@pytest.mark.parametrize(("reply", "status"), [
    ("依目前列表無須調整；未重新檢查執行細節。", "none"),
    ("請補充要檢查的 Python 版本範圍。", "needs_information"),
])
async def test_list_then_direct_explanation(transport, reply, status):
    responses, requests = transport
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion(reply_message(reply, status)),
    ])
    result = await refine()
    assert requests[0]["tool_choice"]["function"]["name"] == "list_checklist"
    assert requests[1]["tool_choice"] == "auto"
    assert result.reply == reply
    assert result.proposal_status == status
    assert result.proposal == []
    assert [outcome["tool"] for outcome in result.tool_calls] == ["list_checklist"]


async def test_list_detail_edit_then_explanation(transport):
    responses, requests = transport
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion(tool_call_message("get_checklist_item", {"id": "python"})),
        completion(tool_call_message("edit_checklist_item", {"id": "python", "title": "Python 版本"})),
        completion(reply_message("已整理標題調整提案。", "ready")),
    ])
    result = await refine()
    assert all(request["tool_choice"] == "auto" for request in requests[1:])
    assert result.proposal[0]["title"] == "Python 版本"
    assert [outcome["status"] for outcome in result.tool_calls] == ["read", "read", "staged"]
    detail = json.loads(requests[2]["messages"][-1]["content"])
    assert detail["item"]["check_steps"] == item()["check_steps"]


async def test_edit_still_requires_detail_read_and_can_repair(transport):
    responses, requests = transport
    edit = tool_call_message("edit_checklist_item", {"id": "python", "title": "Python 版本"})
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion(copy.deepcopy(edit)),
        completion(tool_call_message("get_checklist_item", {"id": "python"})),
        completion(edit),
        completion(reply_message("已整理標題調整提案。", "ready")),
    ])
    result = await refine()
    error = json.loads(requests[2]["messages"][-1]["content"])
    assert error["reason_code"] == "checklist_item_not_read"
    assert result.proposal[0]["title"] == "Python 版本"
    assert result.tool_calls[1]["resolved"] is True


async def test_empty_response_is_repaired_without_replaying_tools(transport):
    responses, requests = transport
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion({"content": None, "reasoning": "private reasoning"}, tokens=15),
        completion(reply_message("依目前列表無須修改。", "none")),
    ])
    result = await refine()
    assert len(requests) == 3
    assert requests[2]["tool_choice"] == "auto"
    assert requests[2]["max_tokens"] == requests[1]["max_tokens"] - 15
    assert requests[2]["messages"][:-1] == requests[1]["messages"]
    assert "private reasoning" not in json.dumps(requests[2])
    assert result.reply == "依目前列表無須修改。"
    assert result.metrics["completion_tokens"] == 35
    assert result.metrics["prompt_tokens"] == 300
    assert len(result.tool_calls) == 1


async def test_round_exhaustion_requests_explicit_reply_and_repairs_once(transport):
    responses, requests = transport
    responses.extend([
        *[completion(tool_call_message("list_checklist", {})) for _ in range(6)],
        completion({"content": " "}),
        completion(reply_message("本次未完成細查，尚未建立修改提案。", "none")),
    ])
    result = await refine()
    assert len(requests) == 8
    assert all(request["tool_choice"] == "auto" for request in requests[1:6])
    assert "tools" not in requests[6] and "tools" not in requests[7]
    assert requests[6]["messages"][-1]["role"] == "system"
    assert "工具階段已結束" in requests[6]["messages"][-1]["content"]
    assert result.reply == "本次未完成細查，尚未建立修改提案。"
    assert result.proposal == []


async def test_repeated_empty_response_fails_without_claiming_success(transport):
    responses, requests = transport
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion({"content": None}),
        completion({"content": None}),
    ])
    with pytest.raises(HTTPException) as exc:
        await refine()
    assert exc.value.status_code == 502
    assert len(requests) == 3


@pytest.mark.parametrize("empty_call_seconds", [2, 61])
async def test_empty_repair_shares_original_deadline(transport, monkeypatch, empty_call_seconds):
    responses, requests = transport
    responses.extend([
        completion(tool_call_message("list_checklist", {})),
        completion({"content": None}),
        completion(reply_message("依目前列表無須修改。", "none")),
    ])
    now = [0.0]
    timeouts = []
    original = service.teacher_judge_client.create_chat_completion

    async def timed_call(payload, **kwargs):
        timeouts.append(kwargs["timeout"])
        try:
            return await original(payload, **kwargs)
        finally:
            if len(timeouts) == 2:
                now[0] += empty_call_seconds

    monkeypatch.setattr(service, "perf_counter", lambda: now[0])
    monkeypatch.setattr(service.teacher_judge_client, "create_chat_completion", timed_call)
    if empty_call_seconds > 60:
        with pytest.raises(HTTPException) as exc:
            await refine()
        assert exc.value.status_code == 504
        assert len(requests) == 2
    else:
        await refine()
        assert timeouts == [60, 60, 58]


async def test_empty_initial_read_retries_same_tool_then_releases_it(transport):
    responses, requests = transport
    responses.extend([
        completion({"content": None}),
        completion(tool_call_message("list_checklist", {})),
        completion(reply_message("依目前列表無須修改。", "none")),
    ])
    result = await refine()
    assert requests[0]["tool_choice"] == requests[1]["tool_choice"]
    assert requests[2]["tool_choice"] == "auto"
    assert len(result.tool_calls) == 1


@pytest.mark.parametrize("body", [
    completion({"content": None}, finish="length"),
    completion({"content": [{"type": "text", "text": "ok"}]}),
    completion({"content": None}, usage=False),
    completion({"content": None}, tokens=4096),
])
async def test_invalid_or_unbudgeted_response_is_not_retried(transport, body):
    responses, requests = transport
    responses.extend([completion(tool_call_message("list_checklist", {})), body])
    with pytest.raises(HTTPException) as exc:
        await refine()
    assert exc.value.status_code == 502
    assert len(requests) == 2
