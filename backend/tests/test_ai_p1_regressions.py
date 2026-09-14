"""Isolated regressions for the backend AI P1 review findings."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from proxmoxer.core import ResourceException
from sqlmodel import Session, SQLModel, create_engine

from app.ai.navigation.service import _extract_first_json_object
from app.ai.pve_log import collector
from app.ai.pve_log.chat import _execute_tool_sync
from app.ai.system_config import system_ai_env
from app.ai.teacher_judge import script_artifact_service as artifacts
from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge import script_result_analysis_service as analysis
from app.ai.teacher_judge import service
from app.ai.teacher_judge.prompt import (
    CHAT_SYSTEM_TEMPLATE,
    SITUATION_NORMAL,
)
from app.ai.teacher_judge.schemas import TeacherJudgeRubricChatMessage
from app.models.teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptStatus,
)
from app.models.teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


@pytest.mark.parametrize("value", ["a } brace", "a { brace", 'escaped \\" quote { }'])
def test_navigation_json_handles_braces_inside_strings(value):
    expected = {"intent": value, "action": "clarify"}
    text = "```json\n" + json.dumps(expected) + "\n```"
    assert json.loads(_extract_first_json_object(text)) == expected


@pytest.mark.parametrize(
    "prompt",
    [
        CHAT_SYSTEM_TEMPLATE,
        artifacts.AI_REVIEWER_SYSTEM_PROMPT,
    ],
)
def test_plain_prompt_json_examples_are_valid(prompt):
    assert "\n{{\n" not in prompt
    start = prompt.index("{\n")
    json.JSONDecoder().raw_decode(prompt[start:])


def test_teacher_judge_chat_prompt_is_scoped_and_clarifies_missing_information():
    assert "使用者問 A，只回答 A" in CHAT_SYSTEM_TEMPLATE
    assert "只詢問最少且具體的問題" in CHAT_SYSTEM_TEMPLATE
    assert "不得猜測後繼續" in CHAT_SYSTEM_TEMPLATE
    assert "不要補充未詢問的" in CHAT_SYSTEM_TEMPLATE
    assert "只協助老師規劃、新增或調整檢查項目" in CHAT_SYSTEM_TEMPLATE
    assert "不會當場連線學生環境、讀取檔案或執行指令" in CHAT_SYSTEM_TEMPLATE
    assert "不是提案白名單" in CHAT_SYSTEM_TEMPLATE
    assert "不得只因沒有專用 `command_key` 就拒絕提案" in CHAT_SYSTEM_TEMPLATE
    assert "要求老師新增權限" in CHAT_SYSTEM_TEMPLATE
    assert "不要求老師先說「新增」" in CHAT_SYSTEM_TEMPLATE
    assert "一則訊息包含多條需求時逐條拆解" in CHAT_SYSTEM_TEMPLATE
    assert "不得因其中一條不完整而忽略其他 Ready 需求" in CHAT_SYSTEM_TEMPLATE
    assert "不得因缺少客觀答案而攔截提案" in CHAT_SYSTEM_TEMPLATE
    assert "只有老師明確要求「重新核查整張檢查表」" in CHAT_SYSTEM_TEMPLATE
    assert "不得只說「資訊不足」" in CHAT_SYSTEM_TEMPLATE
    assert "像助教當面說明的日常繁體中文" in CHAT_SYSTEM_TEMPLATE
    assert "先說已經知道什麼，再說還缺什麼" in CHAT_SYSTEM_TEMPLATE
    assert "完整路徑" in CHAT_SYSTEM_TEMPLATE
    assert "工作目錄與相對路徑" in CHAT_SYSTEM_TEMPLATE
    assert "預期文字、數字、行數、欄位、版本、Port 或狀態" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "老師明確表示想自己檢查時，才說明會先收集結果再由老師查看" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "不得自行改用 `teacher`" in CHAT_SYSTEM_TEMPLATE
    assert "應使用 `auto + teacher`" not in CHAT_SYSTEM_TEMPLATE
    assert "一般回覆不要使用「腳本取證」" in CHAT_SYSTEM_TEMPLATE
    assert "依項目的檢查對象與缺口自然組句" in CHAT_SYSTEM_TEMPLATE
    assert "不要固定套用任何預設開頭、結尾或完整範本" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "不要照抄範例、硬塞檔名或重複固定收尾" in CHAT_SYSTEM_TEMPLATE
    assert "不要複製它的句首、例子或收尾" in CHAT_SYSTEM_TEMPLATE
    assert "我還不知道怎樣才算通過" not in CHAT_SYSTEM_TEMPLATE
    assert "兩者只能選符合本項設定的一種" in CHAT_SYSTEM_TEMPLATE
    assert "不得含糊寫成「由 AI 或導師判斷」" in CHAT_SYSTEM_TEMPLATE
    assert "補充後，我會重新確認並建立提案給你查看" not in CHAT_SYSTEM_TEMPLATE
    assert "`list_checklist` 回傳所有項目的 ID、標題與偵測狀態" in CHAT_SYSTEM_TEMPLATE
    assert "用 ID 查詢單一項目完整內容" in CHAT_SYSTEM_TEMPLATE
    assert "全新、與既有項目無關" in CHAT_SYSTEM_TEMPLATE
    assert "檢查項目提案只能透過 `create_checklist_item`（新增）或" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "只填有變動的欄位" in CHAT_SYSTEM_TEMPLATE
    assert "{rubric_context}" not in CHAT_SYSTEM_TEMPLATE
    assert '"proposal_status": "ready | needs_information | unsupported | none"' in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "你覺得...如何" not in SITUATION_NORMAL


def test_structured_proposal_status_overrides_reply_wording() -> None:
    assert service._proposal_status_claims_ready("ready", "尚未準備就緒") is True
    assert service._proposal_status_claims_ready("needs_information", "Ready") is False
    assert service._proposal_status_claims_ready(None, "Ready") is False


def test_create_tool_rejection_explains_invalid_step_to_model_only() -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="linux",
        command_key="system.run_command",
        command_label="通用受控指令",
        category="inspection",
        command_template="argv + cwd + timeout",
        description="執行單一唯讀診斷指令。",
    )
    read_ids: set[str] = set()
    staged_ops: list[dict[str, object]] = []
    rejected_ops: list[tuple[object, dict[str, object], str]] = []
    tool_calls: list[dict[str, object]] = []

    result = service._execute_checklist_tool(
        service._CREATE_CHECKLIST_ITEM_TOOL_NAME,
        {
            "title": "檢查 answer.txt 內容",
            "detectable": "auto",
            "detection_method": "讀取檔案並確認內容格式。",
            "check_steps": [
                {
                    "template_key": "linux",
                    "command_key": "invented.command",
                }
            ],
        },
        snapshot_items=[],
        analysis_revision=1,
        template_key="linux",
        template_commands=[command],
        ready_only=True,
        read_ids=read_ids,
        staged_ops=staged_ops,
        rejected_ops=rejected_ops,
        tool_calls=tool_calls,
    )

    assert "error" in result
    assert "「檢查 answer.txt 內容」" in result["error"]
    assert "linux/system.run_command" in result["error"]
    assert "這份清單不是提案限制" in result["error"]
    assert "system.run_command" in result["error"]
    assert "單一非空 argv list" in result["error"]
    assert staged_ops == []
    assert any(
        entry.get("status") == "rejected" for entry in tool_calls
    )


def test_teacher_judge_prompt_uses_goal_directed_diagnostic_principles():
    assert "熟悉 Linux、Windows 系統管理與常見 CLI 工具" in CHAT_SYSTEM_TEMPLATE
    assert "根據老師要確認的目的，自行選擇適合的診斷指令" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "優先規劃唯讀、診斷型指令" in CHAT_SYSTEM_TEMPLATE
    assert "能以低權限取得資訊時，不要求 `sudo` 或 Administrator" in (
        CHAT_SYSTEM_TEMPLATE
    )
    assert "避免無目的大量執行指令" in CHAT_SYSTEM_TEMPLATE
    assert "不只回傳原始輸出" in CHAT_SYSTEM_TEMPLATE
    assert "不是提案白名單" in CHAT_SYSTEM_TEMPLATE
    assert "沒有專用項目時使用 `system.run_command`" in CHAT_SYSTEM_TEMPLATE
    assert "systemctl list-units" not in CHAT_SYSTEM_TEMPLATE
    assert "journalctl --since" not in CHAT_SYSTEM_TEMPLATE
    assert "`history` 是 shell builtin" not in CHAT_SYSTEM_TEMPLATE


@pytest.mark.parametrize("content", ["null", "[]", '"text"'])
async def test_non_object_chat_and_script_outputs_fail_cleanly(monkeypatch, content):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")

    async def fake_call(*args, **kwargs):
        return content, {}

    monkeypatch.setattr(service, "_call_vllm_message", fake_call)
    monkeypatch.setattr(artifacts, "_call_vllm", fake_call)
    with pytest.raises(HTTPException) as error:
        await artifacts.fix_script_content(script_content="pass", fix_hints=[])
    assert error.value.status_code == 502
    with pytest.raises(HTTPException) as error:
        await artifacts.generate_script_content(
            rubric_snapshot={}, template_key="linux"
        )
    assert error.value.status_code == 502
    reply, proposal, _ = await service.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="說明")], "{}"
    )
    assert reply == content
    assert proposal is None


@pytest.mark.asyncio
async def test_teacher_judge_session_proposal_keeps_only_ready_changes(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    captured = {}
    calls, fake_call = _scripted_vllm(
        [
            # Round 1: the model creates only the Ready requirement; the
            # partial and manual requirements stay out of the proposal.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "main.py 輸出 20",
                    "description": "在專案目錄執行 main.py 並確認輸出 20。",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "比較 exit code 與 stdout。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "python",
                            "command_key": "python.run_entrypoint",
                            "parameters": {
                                "cwd": "/home/student/project",
                                "argv": ["python3", "main.py"],
                                "timeout_seconds": 30,
                                "success_criteria": (
                                    "exit code 為 0 且 stdout 等於 20"
                                ),
                            },
                        }
                    ],
                },
            ),
            _reply_message(
                "1. main.py 輸出 20：Ready，已放入提案。\n"
                "2. Web 服務：缺少 Port。\n"
                "3. 報告清楚：不支援自動檢測，需人工評閱。",
                "ready",
            ),
        ],
    )

    async def capture_call(payload, timeout=60.0):
        captured.update(payload)
        return await fake_call(payload, timeout=timeout)

    monkeypatch.setattr(service, "_call_vllm_message", capture_call)
    proposal_command = TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.run_entrypoint",
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="python3 main.py",
        description="執行老師指定目錄中的 Python 程式並收集輸出。",
        risk_level="executes_code",
        requires_confirmation=True,
    )

    reply, proposal, _metrics = await service.chat_with_rubric(
        [
            TeacherJudgeRubricChatMessage(
                role="user",
                content=(
                    "執行 main.py 輸出 20；Web 服務回傳 200；報告說明要清楚。"
                ),
            )
        ],
        json.dumps(
            {
                "items": [
                    {
                        "id": "item-existing",
                        "title": "既有檢查",
                        "description": "保留原本設定。",
                        "detectable": "auto",
                        "detection_method": "比較 exit code 與 stdout。",
                        "check_steps": [
                            {
                                "template_key": "python",
                                "command_key": "python.run_entrypoint",
                                "parameters": {
                                    "cwd": "/home/student/existing",
                                    "argv": ["python3", "main.py"],
                                    "timeout_seconds": 30,
                                    "success_criteria": "exit code 為 0",
                                },
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        template_key="python",
        template_commands=[proposal_command],
        rubric_available=True,
    )

    assert "缺少 Port" in reply
    assert proposal is not None
    assert proposal[0]["id"].startswith("item-")
    assert proposal[0]["operation"] == "add"
    assert "多條需求可以分次呼叫工具" in captured["messages"][0][
        "content"
    ]


def _scripted_vllm(steps: list[object]):
    """Build a fake `_call_vllm_message`; steps may carry (response, metrics)."""
    calls: list[dict[str, object]] = []

    async def fake_call(payload, timeout=60.0):
        calls.append(payload)
        step = steps.pop(0)
        if isinstance(step, tuple):
            return step
        return step, {}

    return calls, fake_call


def _tool_call_message(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Assistant message that invokes one checklist proposal tool."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }


def _reply_message(
    reply: str, status: str, focus: dict[str, object] | None = None
) -> str:
    """Final assistant JSON reply without any tool calls."""
    payload: dict[str, object] = {"reply": reply, "proposal_status": status}
    if focus is not None:
        payload["conversation_focus"] = focus
    return json.dumps(payload, ensure_ascii=False)


def _requirement_focus(status: str, *, key: str, title: str) -> dict[str, object]:
    return {
        "turn_kind": "requirement",
        "requirements": [
            {
                "focus_key": key,
                "status": status,
                "known_information": [title],
                "missing_information": [],
                "target_item_id": None,
            }
        ],
    }


@pytest.mark.asyncio
async def test_teacher_judge_repairs_missing_status_without_turning_question_into_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    calls, fake_call = _scripted_vllm(
        [
            # A pure question with no proposal_status and no proposal: no
            # reminder is sent and no proposal is invented.
            _reply_message(
                "system.run_command 可讀取指定檔案，但不會在聊天室立即執行。",
                "none",
            ),
        ],
    )

    monkeypatch.setattr(service, "_call_vllm_message", fake_call)

    reply, proposal, _metrics = await service.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="可以讀取檔案嗎？")],
        json.dumps({"items": []}),
        template_commands=[],
    )

    assert len(calls) == 1
    assert proposal is None
    assert reply == "system.run_command 可讀取指定檔案，但不會在聊天室立即執行。"


@pytest.mark.asyncio
async def test_teacher_judge_does_not_keep_false_ready_reply_after_failed_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    calls, fake_call = _scripted_vllm(
        [
            # Round 1: the model claims Ready but submits a partial candidate;
            # the tool rejects it because it cannot produce an executable step.
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "檔案格式檢查",
                    "detectable": "partial",
                    "detection_method": "讀取檔案並逐行驗證。",
                    "missing_information": ["要檢查的檔案位置"],
                    "check_steps": [],
                },
            ),
            # Round 2: the model still claims Ready without a proposal.
            _reply_message("狀態：Ready，已放入提案。", "ready"),
        ],
    )

    monkeypatch.setattr(service, "_call_vllm_message", fake_call)

    reply, proposal, _metrics = await service.chat_with_rubric(
        [TeacherJudgeRubricChatMessage(role="user", content="檢查檔案格式")],
        json.dumps({"items": []}),
        template_commands=[],
    )

    assert len(calls) == 2
    assert proposal is None
    assert "檔案格式檢查" in reply
    assert "檢查位置" in reply
    assert "已放入提案" not in reply


@pytest.mark.asyncio
async def test_teacher_judge_recovers_read_file_alias_as_generic_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    calls, fake_call = _scripted_vllm(
        [
            _tool_call_message(
                "create_checklist_item",
                {
                    "title": "確認 answer.txt 內容格式",
                    "description": "確認 answer.txt 內容格式正確。",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取檔案並逐行驗證。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "invented.read_file",
                            "parameters": {
                                "argv": ["cat", "answer.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": "內容格式正確",
                            },
                        }
                    ],
                },
            ),
            _reply_message("answer.txt 內容格式：Ready，已放入提案。", "ready"),
        ],
    )

    monkeypatch.setattr(service, "_call_vllm_message", fake_call)
    command = TeacherJudgeTemplateCommand(
        template_key="linux",
        command_key="system.run_command",
        command_label="通用受控指令",
        category="inspection",
        command_template="argv + cwd + timeout",
        description="執行單一唯讀診斷指令。",
    )

    reply, proposal, _metrics = await service.chat_with_rubric(
        [
                TeacherJudgeRubricChatMessage(
                    role="user",
                    content="確認 answer.txt 內容格式。",
                )
        ],
        json.dumps({"items": []}),
        template_commands=[command],
    )

    assert len(calls) == 2
    assert proposal is not None
    assert "answer.txt 內容格式" in reply
    assert proposal[0]["operation"] == "add"
    assert proposal[0]["check_steps"] == [
        {
            "template_key": "linux",
            "command_key": "system.run_command",
            "command_label": "通用受控指令",
            "parameters": {
                "argv": ["cat", "answer.txt"],
                "timeout_seconds": 30,
                "success_criteria": "內容格式正確",
            },
        }
    ]


@pytest.mark.asyncio
async def test_teacher_judge_summary_uses_dedicated_low_budget_prompt(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    captured = {}

    async def fake_call(payload, timeout=60.0):
        captured["payload"] = payload
        captured["timeout"] = timeout
        return "  已確認只保留 Python 檢查。  ", {}

    monkeypatch.setattr(service, "_call_vllm", fake_call)
    summary, metrics = await service.summarize_conversation(
        [
            TeacherJudgeRubricChatMessage(role="user", content="保留 Python 檢查"),
            TeacherJudgeRubricChatMessage(role="assistant", content="好的"),
        ],
        previous_summary="舊方向",
    )

    payload = captured["payload"]
    assert summary == "已確認只保留 Python 檢查。"
    assert metrics == {}
    assert payload["max_tokens"] <= 768
    assert "response_format" not in payload
    assert "不要新增、刪除或修改任何檢查項目" in payload["messages"][0]["content"]
    assert "舊方向" in payload["messages"][1]["content"]
    assert payload["messages"][-1]["role"] == "user"


async def test_truncated_model_output_is_not_accepted_as_complete_json(monkeypatch):
    async def fake_completion(*args, **kwargs):
        return {
            "choices": [
                {"finish_reason": "length", "message": {"content": '{"items": []}'}}
            ]
        }

    monkeypatch.setattr(
        service.teacher_judge_client, "create_chat_completion", fake_completion
    )
    with pytest.raises(HTTPException) as error:
        await service._call_vllm({"response_format": {"type": "json_object"}})
    assert error.value.status_code == 502


def _fake_pve(monkeypatch, cluster_get):
    proxmox = SimpleNamespace(
        cluster=SimpleNamespace(
            status=SimpleNamespace(get=cluster_get),
            resources=SimpleNamespace(get=lambda **kwargs: []),
        ),
        nodes=SimpleNamespace(get=lambda: []),
    )
    monkeypatch.setattr(collector, "get_proxmox_api", lambda: proxmox)
    monkeypatch.setattr(collector.settings, "collector_retry_attempts", 3)
    monkeypatch.setattr(collector.settings, "collector_retry_backoff", 0)
    return proxmox


def test_collector_retries_before_returning_partial_snapshot(monkeypatch):
    calls = []

    def unavailable():
        calls.append(1)
        raise OSError("synthetic unavailable")

    _fake_pve(monkeypatch, unavailable)
    snapshot = collector.collect_snapshot()
    assert len(calls) == 3
    assert snapshot.cluster.quorate is False
    assert snapshot.errors
    tool = _execute_tool_sync(snapshot, "get_cluster", {})
    assert tool.get("error")
    assert tool["quorate"] is False


def test_collector_transient_cluster_failure_recovers(monkeypatch):
    calls = []

    def recover():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("temporary")
        return [{"type": "cluster", "name": "test", "nodes": 2, "quorate": 1}]

    _fake_pve(monkeypatch, recover)
    snapshot = collector.collect_snapshot()
    assert len(calls) == 2
    assert snapshot.cluster.quorate is True
    assert snapshot.errors == []


@pytest.mark.parametrize(
    "fetch,args",
    [
        ("_collect_storages_for_node", ("node",)),
        ("_collect_resource_status", ("node", 1, "qemu")),
        ("_collect_resource_config", ("node", 1, "qemu")),
        ("_collect_lxc_interfaces", ("node", 1)),
    ],
)
def test_collector_fetch_errors_reach_retry_layer(fetch, args):
    def unavailable(*args):
        raise OSError("synthetic unavailable")

    with pytest.raises(OSError):
        getattr(collector, fetch)(SimpleNamespace(nodes=unavailable), *args)


@pytest.mark.parametrize(
    "status,expected_calls", [(401, 1), (403, 1), (404, 1), (429, 3), (503, 3)]
)
def test_collector_retries_only_transient_http_failures(
    monkeypatch, status, expected_calls
):
    calls = []

    def unavailable():
        calls.append(1)
        raise ResourceException(status, "synthetic", "unavailable")

    _fake_pve(monkeypatch, unavailable)
    snapshot = collector.collect_snapshot()
    assert len(calls) == expected_calls
    assert snapshot.errors


async def test_cancelled_analysis_waiter_does_not_consume_released_slot(monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(analysis, "_AI_ANALYSIS_SLOTS", slots)
    task = asyncio.create_task(analysis._acquire_ai_slot())
    try:
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        slots.release()
        await asyncio.sleep(0.05)
        acquired = slots.acquire(blocking=False)
        assert acquired
    finally:
        # Also unblock the old implementation's worker after a regression failure.
        slots.release()


def test_rubric_excerpt_preserves_items_after_twenty_and_partial_id_matches():
    items = [{"id": f"item-{i}", "title": f"題目 {i}"} for i in range(25)]
    result = analysis._rubric_excerpt({"items": items})
    assert [item["id"] for item in result] == [item["id"] for item in items]


def _judgement():
    return {
        "score": 5,
        "max_score": 5,
        "summary": "符合要求",
        "item_judgements": [
            {
                "item_id": "item-1",
                "title": "服務",
                "status": "pass",
                "score": 1,
                "max_score": 1,
                "evidence_refs": ["service.http"],
                "comment": "回應正常",
            }
        ],
    }


def _analysis_payload():
    return {
        "rubric_items": [{"id": "item-1", "title": "服務"}],
        "script_result": {"checks": [{"id": "service.http", "status": "pass"}]},
    }


@pytest.mark.parametrize(
    "invalid", ["empty", "item", "ref", "status", "no_refs", "unknown_evidence"]
)
async def test_invalid_judgements_are_rejected(monkeypatch, invalid):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    judgement = _judgement()
    payload = _analysis_payload()
    item = judgement["item_judgements"][0]
    if invalid == "empty":
        judgement = {}
    elif invalid == "item":
        item["item_id"] = "nonexistent"
    elif invalid == "ref":
        item["evidence_refs"] = ["nonexistent"]
    elif invalid == "status":
        item["status"] = "perfect"
    elif invalid == "no_refs":
        item["evidence_refs"] = []
    else:
        payload["script_result"]["checks"][0]["status"] = "unknown"

    async def fake_call(*args, **kwargs):
        return json.dumps(judgement), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    with pytest.raises(HTTPException) as error:
        await analysis._call_ai_judgement(payload)
    assert error.value.status_code == 502


async def test_valid_judgement_accepts_different_rubric_and_check_ids(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")

    async def fake_call(*args, **kwargs):
        return json.dumps(_judgement()), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    result = await analysis._call_ai_judgement(_analysis_payload())
    assert "score" not in result
    assert "max_score" not in result
    assert result["item_judgements"][0]["evidence_refs"] == ["service.http"]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"approved": True, "risk_level": "invalid", "issues": []},
        {"approved": "true", "risk_level": "low", "issues": []},
        {"approved": True, "risk_level": "low", "issues": ["unsafe"]},
    ],
)
def test_reviewer_invalid_or_contradictory_output_never_approves(payload):
    assert artifacts._normalize_ai_review(payload)["approved"] is False


@pytest.mark.parametrize("status", ["unknown", "skipped"])
async def test_unknown_judgement_preserves_status_without_fabricated_refs(
    monkeypatch, status
):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    result = _judgement()
    result["score"] = 0
    result["item_judgements"][0].update(status=status, score=0, evidence_refs=[])

    async def fake_call(*args, **kwargs):
        return json.dumps(result), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    judgement = await analysis._call_ai_judgement(_analysis_payload())
    assert judgement["item_judgements"][0]["status"] == status


async def test_teacher_judgement_never_becomes_ai_pass(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    payload = _analysis_payload()
    payload["rubric_items"][0]["judgement_mode"] = "teacher"
    result = _judgement()
    result["score"] = 0
    result["item_judgements"][0].update(status="unknown", score=0)

    async def fake_call(*args, **kwargs):
        return json.dumps(result), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    judgement = await analysis._call_ai_judgement(payload)

    assert judgement["requires_teacher_review"] is True
    assert judgement["teacher_review_item_ids"] == ["item-1"]
    assert judgement["item_judgements"][0]["judgement_mode"] == "teacher"


async def test_teacher_judgement_rejects_ai_pass(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    payload = _analysis_payload()
    payload["rubric_items"][0]["judgement_mode"] = "teacher"

    async def fake_call(*args, **kwargs):
        return json.dumps(_judgement()), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    with pytest.raises(HTTPException):
        await analysis._call_ai_judgement(payload)


async def test_judgement_cannot_silently_omit_rubric_items(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    payload = _analysis_payload()
    payload["rubric_items"].append({"id": "item-2", "title": "another item"})

    async def fake_call(*args, **kwargs):
        return json.dumps(_judgement()), {}

    monkeypatch.setattr(analysis, "_call_vllm", fake_call)
    with pytest.raises(HTTPException):
        await analysis._call_ai_judgement(payload)


async def test_analysis_slot_released_after_http_failure(monkeypatch):
    monkeypatch.setattr(system_ai_env, "vllm_model_name", "test-model")
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(analysis, "_AI_ANALYSIS_SLOTS", slots)

    async def unavailable(*args, **kwargs):
        raise HTTPException(status_code=504, detail="synthetic timeout")

    monkeypatch.setattr(analysis, "_call_vllm", unavailable)
    with pytest.raises(HTTPException):
        await analysis._call_ai_judgement(_analysis_payload())
    assert slots.acquire(blocking=False)
    slots.release()


async def test_executor_sync_stage_does_not_block_loop(monkeypatch):
    entered = threading.Event()
    released = threading.Event()
    loop_thread = threading.get_ident()
    worker_threads = []

    def unexpected_load(**kwargs):
        raise AssertionError("Synchronous execution must run in the worker stage")

    monkeypatch.setattr(executor, "_load_run_and_artifact", unexpected_load)

    def execute_targets(run_id):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert released.wait(2)
        return None

    monkeypatch.setattr(executor, "_execute_targets", execute_targets, raising=False)
    task = asyncio.create_task(executor._execute_script_run(uuid.uuid4()))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        assert len(worker_threads) == 1
        assert worker_threads[0] != loop_thread
    finally:
        released.set()
        await task


@pytest.mark.parametrize("stage", ["execute", "save"])
async def test_executor_cancellation_drains_worker_before_recording_failure(
    monkeypatch, stage
):
    entered = threading.Event()
    released = threading.Event()
    events = []

    def block():
        entered.set()
        assert released.wait(2)
        events.append("worker_finished")

    def execute_targets(run_id):
        if stage == "execute":
            block()
        return executor._ExecutedTargets({}, {}, [])

    def save(run_id, results):
        assert stage == "save"
        block()

    async def analyze(**kwargs):
        return []

    monkeypatch.setattr(executor, "_execute_targets", execute_targets)
    monkeypatch.setattr(executor, "analyze_target_results", analyze)
    monkeypatch.setattr(executor, "_save_analyzed_results", save)
    monkeypatch.setattr(
        executor, "_mark_run_executor_failed", lambda *args: events.append("failed")
    )
    task = asyncio.create_task(executor.execute_script_run(uuid.uuid4()))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert events == []
    finally:
        released.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert events == ["worker_finished", "failed"]


async def test_executor_sessions_and_ssh_wait_stay_off_loop(monkeypatch, tmp_path):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'executor.sqlite'}")
    SQLModel.metadata.create_all(db_engine)
    with Session(db_engine) as session:
        artifact = TeacherJudgeScriptArtifact(
            teaching_class_id=uuid.uuid4(),
            name="test",
            template_key="linux",
            rubric_snapshot_json={},
            script_content="pass",
            status=TeacherJudgeScriptStatus.approved,
        )
        session.add(artifact)
        session.flush()
        run = TeacherJudgeScriptRun(
            teaching_class_id=artifact.teaching_class_id,
            artifact_id=artifact.id,
            target_snapshot_json={"targets": [{"vmid": 101}]},
        )
        session.add(run)
        session.commit()
        run_id = run.id

    entered = threading.Event()
    released = threading.Event()
    loop_thread = threading.get_ident()
    session_threads = []

    def owned_session(*args, **kwargs):
        session_threads.append(threading.get_ident())
        return Session(*args, **kwargs)

    def fake_ssh(**kwargs):
        entered.set()
        assert released.wait(2)
        return executor.RemoteScriptResult(
            0,
            json.dumps(
                {
                    "schema_version": "teacher_judge_result.v1",
                    "metadata": {
                        "timestamp": "2026-09-07T00:00:00Z",
                        "platform": "linux",
                    },
                    "summary": "done",
                    "checks": [],
                    "errors": [],
                }
            ),
            "",
        )

    async def fake_analysis(**kwargs):
        assert threading.get_ident() == loop_thread
        return kwargs["target_results"]

    monkeypatch.setattr(executor, "engine", db_engine)
    monkeypatch.setattr(executor, "Session", owned_session)
    monkeypatch.setattr(executor, "_live_running_by_vmid", lambda: {})
    monkeypatch.setattr(
        executor, "_resolve_runtime_target", lambda **kwargs: dict(kwargs["target"])
    )
    monkeypatch.setattr(executor, "_execute_target_script", fake_ssh)
    monkeypatch.setattr(executor, "analyze_target_results", fake_analysis)
    task = asyncio.create_task(executor.execute_script_run(run_id))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        assert not task.done()
    finally:
        released.set()
        await task
    assert session_threads and loop_thread not in session_threads
    with Session(db_engine) as session:
        stored = session.get(TeacherJudgeScriptRun, run_id)
        assert stored.status == TeacherJudgeScriptRunStatus.completed
        assert stored.target_results_json["targets"][0]["vmid"] == 101
    # A late cancellation/failure cannot overwrite a committed completion.
    await asyncio.to_thread(executor._mark_run_executor_failed, run_id, "late failure")
    with Session(db_engine) as session:
        assert (
            session.get(TeacherJudgeScriptRun, run_id).status
            == TeacherJudgeScriptRunStatus.completed
        )
    db_engine.dispose()
