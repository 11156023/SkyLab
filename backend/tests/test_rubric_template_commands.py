from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.schemas import TeacherJudgeRubricItem
from app.ai.teacher_judge.template_command_service import (
    GENERAL_COMMAND,
    format_template_commands_for_prompt,
    get_enabled_template_commands,
    validate_check_steps,
    validate_check_steps_with_issues,
)
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


def _patch_teacher_judge_vllm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        teacher_judge_service,
        "settings",
        SimpleNamespace(
            VLLM_MODEL_NAME="test-model",
            VLLM_ENABLE_THINKING=False,
            VLLM_TIMEOUT=60,
            VLLM_MAX_TOKENS=4096,
            VLLM_CHAT_MAX_TOKENS=4096,
            VLLM_CHAT_TEMPERATURE=0.2,
            VLLM_TOP_P=1.0,
            VLLM_TOP_K=20,
            VLLM_REPETITION_PENALTY=1.0,
        ),
    )


def _session_with_commands() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="n8n",
            command_key="n8n.port_check",
            command_label="n8n 連接埠檢查",
            category="port",
            command_template="ss -lntp | grep ':5678'",
            description="檢查 n8n 預設 5678 連接埠是否正在監聽。",
        )
    )
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="python",
            command_key="python.version",
            command_label="Python 版本",
            category="runtime",
            command_template="python3 --version",
            description="查看 Python 直譯器版本。",
            enabled=False,
        )
    )
    session.commit()
    return session


def _python_entrypoint_command() -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.run_entrypoint",
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="python3 main.py",
        description="受控執行 Python 程式並收集結果。",
        risk_level="executes_code",
        requires_confirmation=True,
    )


def _python_version_command() -> TeacherJudgeTemplateCommand:
    return TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.version",
        command_label="Python 版本",
        category="runtime",
        command_template="python3 --version",
        description="查看 Python 直譯器版本。",
        risk_level="read_only",
        requires_confirmation=True,
    )


def test_get_enabled_template_commands_filters_template_and_enabled() -> None:
    session = _session_with_commands()

    commands = get_enabled_template_commands(session, "n8n")

    assert [command.command_key for command in commands] == ["n8n.port_check"]


def test_get_enabled_template_commands_can_include_cross_template_catalog() -> None:
    session = _session_with_commands()
    session.add(
        TeacherJudgeTemplateCommand(
            template_key="python",
            command_key="python.run_entrypoint",
            command_label="執行 Python 程式入口",
            category="execution",
            command_template="python3 main.py",
            description="受控執行 Python 程式入口。",
        )
    )
    session.commit()

    commands = get_enabled_template_commands(
        session, "n8n", include_cross_template=True
    )

    assert [(command.template_key, command.command_key) for command in commands] == [
        ("n8n", "n8n.port_check"),
        ("linux", "system.run_command"),
        ("python", "python.run_entrypoint"),
    ]


def test_cross_template_catalog_includes_generic_controlled_command() -> None:
    session = _session_with_commands()

    commands = get_enabled_template_commands(
        session, "python", include_cross_template=True
    )
    general = next(
        command for command in commands if command.command_key == "system.run_command"
    )

    assert general.template_key == "linux"
    assert general.requires_confirmation is True
    assert "平台已登錄的通用唯讀診斷能力" in general.description
    assert "依檢查目的選擇 Linux 或 Windows" in general.description
    assert "指定工作目錄" in general.description
    assert "stdout" in general.description
    assert "stderr" in general.description
    assert "禁止修改系統狀態" in general.description
    assert "能以低權限取得資訊時不得要求提權" in general.description
    assert "平台會套用安全逾時" in general.description
    assert "不需指定技術參數或新增權限" in general.description
    assert "cat" not in general.description


def test_validate_check_steps_allows_catalog_backed_cross_template_step() -> None:
    python_command = _python_entrypoint_command()

    items = validate_check_steps(
        "n8n",
        [
            {
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                    },
                    {
                        "template_key": "postgresql",
                        "command_key": "python.run_entrypoint",
                    },
                ]
            }
        ],
        [python_command],
    )

    assert items[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.run_entrypoint",
            "command_label": "執行 Python 程式入口",
        }
    ]


def test_validate_generic_command_applies_platform_timeout_default() -> None:
    items = validate_check_steps(
        "linux",
        [
            {
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                            "argv": ["cat", ".env"],
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ]
            }
        ],
        [GENERAL_COMMAND],
    )

    parameters = items[0]["check_steps"][0]["parameters"]
    assert parameters == {
        "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
        "argv": ["cat", ".env"],
        "success_criteria": "exit code 為 0",
        "timeout_seconds": 30,
    }


def test_validate_check_steps_reports_model_owned_unknown_command() -> None:
    result = validate_check_steps_with_issues(
        "linux",
        [
            {
                "id": "item-cpu",
                "check_steps": [
                    {"template_key": "linux", "command_key": "invented.cpu"}
                ],
            }
        ],
        [GENERAL_COMMAND],
    )

    assert result.items[0]["check_steps"] == []
    assert [(issue.owner, issue.item_id, issue.code) for issue in result.issues] == [
        ("model", "item-cpu", "unknown_command")
    ]


def test_normalize_repairs_flattened_system_command_shape() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "check-main-log-success",
                "title": "檢查日誌關鍵字",
                "description": (
                    "確認 /home/student/main.log 是否存在 successful 字樣。"
                ),
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "content_search",
                "missing_information": [],
                "check_steps": [
                    {
                        "command_key": "system.run_command",
                        "argv": [
                            "grep",
                            "successful",
                            "/home/student/main.log",
                        ],
                        "cwd": "/home/student",
                        "success_criteria": "exit_code == 0",
                    }
                ],
            }
        ],
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "auto"
    assert items[0].missing_information == []
    assert items[0].check_steps[0].template_key == "linux"
    assert items[0].check_steps[0].command_key == "system.run_command"
    assert items[0].check_steps[0].parameters == {
        "argv": ["grep", "successful", "/home/student/main.log"],
        "cwd": "/home/student",
        "success_criteria": "exit_code == 0",
        "timeout_seconds": 30,
    }


def test_normalize_converges_uncatalogued_readonly_argv_to_general_command() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-jq",
                "title": "檢查 jq 工具版本",
                "description": "確認環境中的 jq 可以執行。",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "執行版本查詢。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "jq.version",
                        "parameters": {
                            "argv": ["jq", "--version"],
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "auto"
    assert items[0].missing_information == []
    assert items[0].check_steps[0].template_key == "linux"
    assert items[0].check_steps[0].command_key == "system.run_command"
    assert items[0].check_steps[0].parameters == {
        "argv": ["jq", "--version"],
        "success_criteria": "exit code 為 0",
        "timeout_seconds": 30,
    }


@pytest.mark.asyncio
async def test_uncatalogued_tool_with_complete_argv_still_forms_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 2:
            return (
                json.dumps(
                    {
                        "reply": (
                            "「檢查 jq 工具版本」已整理成提案。"
                            "系統會確認指令可以執行；請先查看提案，確認後再套用。"
                        )
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        return (
            json.dumps(
                {
                    "reply": "已把 jq 工具版本檢查整理成提案。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-jq",
                            "title": "檢查 jq 工具版本",
                            "description": "確認環境中的 jq 可以執行。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "執行版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "jq.version",
                                    "parameters": {
                                        "argv": ["jq", "--version"],
                                        "success_criteria": "exit code 為 0",
                                    },
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 jq 工具版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "jq",
        "--version",
    ]


def test_validate_check_steps_fills_omitted_template_from_unique_command() -> None:
    python_command = _python_version_command()

    items = validate_check_steps(
        "n8n",
        [{"check_steps": [{"command_key": "python.version"}]}],
        [python_command],
    )

    assert items[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.version",
            "command_label": "Python 版本",
        }
    ]


def test_backend_does_not_classify_issue_owner_from_missing_info_prose() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "讀取環境設定",
                "description": "在目前目錄讀取 .env。",
                "detectable": "partial",
                "detection_method": "以 exit code 判定檔案是否可讀",
                "missing_information": [
                    "唯讀命令與參數",
                    "1 至 300 秒的逾時限制",
                ],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                            "argv": ["cat", ".env"],
                            "success_criteria": "exit code 為 0",
                        },
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert normalized[0].detectable == "partial"
    assert normalized[0].missing_information == [
        "唯讀命令與參數",
        "1 至 300 秒的逾時限制",
    ]
    assert normalized[0].check_steps[0].parameters["timeout_seconds"] == 30


def test_generic_command_missing_target_uses_teacher_facing_description() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "讀取資料",
                "detectable": "partial",
                "detection_method": "以 exit code 判定",
                "missing_information": ["唯讀命令與參數"],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {"success_criteria": "exit code 為 0"},
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert normalized[0].detectable == "partial"
    assert normalized[0].missing_information == [
        "唯讀命令與參數",
        "要檢查的檔案、服務或記錄範圍"
    ]


def test_backend_does_not_infer_config_semantics_from_teacher_text() -> None:
    normalized = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認 Web URL 設定",
                "description": "讀取 .env，確認有 web_URL=True 這條設定。",
                "detectable": "partial",
                "detection_method": "使用 cat 讀取 .env",
                "missing_information": [
                    "客觀成功條件",
                    "「成功條件」尚未定義為「包含 web_URL=True 字樣」",
                ],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "cwd": r"C:\Users\陳洋\Desktop\Campus-Cloud",
                            "argv": ["cat", ".env"],
                        },
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    item = normalized[0]
    assert item.detectable == "partial"
    assert item.missing_information == [
        "客觀成功條件",
        "「成功條件」尚未定義為「包含 web_URL=True 字樣」",
    ]
    assert "success_criteria" not in item.check_steps[0].parameters
    assert item.check_steps[0].parameters["timeout_seconds"] == 30


def test_backend_has_no_text_file_intent_parser() -> None:
    assert not hasattr(teacher_judge_service, "_explicit_text_file_item")


@pytest.mark.asyncio
async def test_explicit_env_assignment_forms_proposal_when_model_claims_missing_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 2:
            return (
                json.dumps(
                    {
                        "reply": (
                            "「檢查 .env 檔案內容」已整理成提案。"
                            "系統會確認指定設定行是否存在；請先查看提案，確認後再套用。"
                        )
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        return (
            json.dumps(
                {
                        "reply": "已把 .env 設定檢查整理成提案。",
                        "proposal_status": "ready",
                        "updated_items": [
                            {
                                "operation": "add",
                                "id": "item-env",
                                "title": "檢查 .env 檔案內容",
                                "description": "確認 web_url=True 設定行存在。",
                                "detectable": "auto",
                                "judgement_mode": "ai",
                                "detection_method": "讀取指定檔案並比對設定行。",
                                "missing_information": [],
                                "check_steps": [
                                    {
                                        "template_key": "linux",
                                        "command_key": "system.run_command",
                                        "parameters": {
                                            "argv": ["cat", "--", "/home/student/.env"],
                                            "timeout_seconds": 30,
                                            "success_criteria": "輸出含 web_url=True",
                                        },
                                    }
                                ],
                            }
                        ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content="我想和/home/student/.env 內容有 web_url=True 這行就給過",
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "整理成提案" in reply
    assert "重新產生" not in reply
    assert "管理員" not in reply
    assert proposal is not None
    assert proposal[0]["operation"] == "add"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "cat",
        "--",
        "/home/student/.env",
    ]


@pytest.mark.asyncio
async def test_chat_with_rubric_validates_returned_check_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="n8n",
        command_key="n8n.http_check",
        command_label="n8n HTTP 檢查",
        category="service",
        command_template="curl -I --max-time 5 http://127.0.0.1:5678",
        description="檢查本機 n8n Web 服務是否有 HTTP 回應。",
    )
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "reply": "已更新",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "n8n Web UI",
                            "detectable": "auto",
                            "check_steps": [
                                {
                                    "template_key": "n8n",
                                    "command_key": "n8n.http_check",
                                },
                                {
                                    "template_key": "n8n",
                                    "command_key": "n8n.missing",
                                },
                            ],
                        }
                    ],
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="照這樣改")],
        rubric_context=json.dumps({"items": [{"id": "item-1"}]}),
        is_refine=True,
        template_key="n8n",
        template_commands=[command],
    )

    assert updated_items is not None
    system_prompt = captured_payload["messages"][0]["content"]
    assert "目前主要 template：n8n" in system_prompt
    assert "n8n.http_check" in system_prompt
    assert "curl -I" not in system_prompt
    assert updated_items[0]["check_steps"] == [
            {
                "template_key": "n8n",
                "command_key": "n8n.http_check",
                "command_label": "n8n HTTP 檢查",
                "parameters": {},
            }
    ]


@pytest.mark.asyncio
async def test_chat_prompt_accepts_objectively_verifiable_main_py_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="python",
        command_key="python.run_entrypoint",
        command_label="執行 Python 程式入口",
        category="execution",
        command_template="python3 main.py",
        description=(
            "在老師提供的工作目錄執行 Python 程式入口，收集 exit code、stdout、stderr；"
            "缺少工作目錄或成功條件時先向老師詢問。"
        ),
        risk_level="executes_code",
        requires_confirmation=True,
    )
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "reply": "已新增可自動檢查的項目。",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "main.py 執行結果",
                            "description": "執行 main.py，確認無錯誤並輸出整數 20。",
                            "detectable": "auto",
                            "detection_method": (
                                "執行 main.py，依 exit code 與 stderr 判斷錯誤，"
                                "並精確比對 stdout 是否為整數 20。"
                            ),
                            "fallback": None,
                            "check_steps": [
                                {
                                    "template_key": "python",
                                    "command_key": "python.run_entrypoint",
                                    "parameters": {
                                        "cwd": "/home/student/project",
                                        "argv": ["python3", "main.py"],
                                        "timeout_seconds": 30,
                                        "success_criteria": "exit code 為 0 且 stdout 等於 20",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content=(
                    "幫我新增檢查點：在 /home/student/project 執行 python3 main.py，"
                    "確認無錯誤並輸出整數 20。"
                ),
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[command],
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "`auto` 表示「腳本取證支援完整」" in system_prompt
    assert (
        "缺少無法由上下文得知的工作目錄、檔案、服務名稱、Port 或記錄範圍"
        in system_prompt
    )
    assert "主觀作品品質、程式架構或開放式答案" in system_prompt
    assert "不得因缺少客觀答案而攔截提案" in system_prompt
    assert "catalog 有對應能力時" in system_prompt
    assert "用無關檢查替換原目標" in system_prompt
    assert "`auto` 項目的 `check_steps` 應優先引用該 `command_key`" in system_prompt
    assert "沒有專用項目時使用 `system.run_command`" in system_prompt
    assert "`template_key` 只是環境提示，可以省略" in system_prompt
    assert "不得因老師或模型沒有填 `template_key` 而拒絕提案" in system_prompt
    assert "`checked` 表示是否已達成" in system_prompt
    assert "`auto` 項目的 `missing_information` 必須是空陣列" in system_prompt
    assert "python.run_entrypoint" in system_prompt
    assert updated_items is not None
    assert updated_items[-1]["detectable"] == "auto"
    assert updated_items[-1]["check_steps"][0]["command_key"] == (
        "python.run_entrypoint"
    )


@pytest.mark.asyncio
async def test_chat_prompt_accepts_generic_cat_env_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "reply": "已新增可自動檢查的項目。",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "讀取環境設定",
                            "description": "在專案目錄執行 cat .env 並回傳內容。",
                            "detectable": "auto",
                            "detection_method": (
                                "以 argv ['cat', '.env']、指定 cwd 與 timeout 執行，"
                                "並原樣取得 exit code、stdout、stderr。"
                            ),
                            "fallback": None,
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "cwd": "/home/student/project",
                                        "argv": ["cat", ".env"],
                                        "timeout_seconds": 10,
                                        "success_criteria": "exit code 為 0",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
            messages=[
                SimpleNamespace(
                    role="user",
                    content="新增檢查點：在 /home/student/project 執行 cat .env",
                )
            ],
        rubric_context=json.dumps({"items": []}),
        template_key="n8n",
        template_commands=[GENERAL_COMMAND],
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "system.run_command" in system_prompt
    assert "這個環境已確認具備、可以優先使用的工具" in system_prompt
    assert "不是允許產出提案的完整清單" in system_prompt
    assert "AI 仍應用 `system.run_command` 規劃其他唯讀診斷工具" in system_prompt
    assert "依檢查目的選擇 Linux 或 Windows" in system_prompt
    assert "終端提示字串已包含目前目錄時" in system_prompt
    assert "timeout_seconds 由平台補齊" in system_prompt
    assert 'file_read_example' not in system_prompt
    assert '["cat", ".env"]' not in system_prompt
    assert updated_items is not None
    assert updated_items[0]["detectable"] == "auto"
    assert updated_items[0]["check_steps"][0]["command_key"] == (
        "system.run_command"
    )


@pytest.mark.asyncio
async def test_follow_up_natural_answer_is_audited_before_repeating_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        return (
            json.dumps(
                {
                    "reply": (
                        "了解，answer.txt 每一行都要是整數，而且至少要有 20 行。"
                        "我已依這個規則整理成提案，請確認後再套用。"
                    ),
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "id": "item-answer",
                            "title": "檢查 answer.txt 內容",
                            "description": "確認每一行都是整數，且至少有 20 行。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "讀取檔案並逐行檢查內容與行數。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "argv": ["cat", "/home/student/answer.txt"],
                                        "timeout_seconds": 30,
                                        "success_criteria": (
                                            "每一行都是整數，且總行數至少為 20"
                                        ),
                                    },
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(
        teacher_judge_service, "_call_vllm_message", fake_call_vllm
    )
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(role="user", content="我要檢查 answer.txt"),
            SimpleNamespace(
                role="assistant",
                content="我還不知道怎樣才算通過，請告訴我預期內容。",
            ),
            SimpleNamespace(
                role="user",
                content="只要每一行都是整數，而且至少要有 20 行",
            ),
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "每一行都要是整數，而且至少要有 20 行" in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["missing_information"] == []
    assert proposal[0]["check_steps"][0]["parameters"]["success_criteria"] == (
        "每一行都是整數，且總行數至少為 20"
    )


@pytest.mark.asyncio
async def test_follow_up_audit_asks_only_the_remaining_real_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        reply = (
            "還需要客觀成功條件，請再補充。"
            if len(calls) != 1
            else (
                "我知道你要確認 web 服務正常，但『正常』有兩種檢查方式："
                "你要確認服務程序正在執行，還是網頁可以正常開啟？"
            )
        )
        return (
            json.dumps(
                {
                    "reply": reply,
                    "proposal_status": "needs_information",
                    "updated_items": None,
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(role="user", content="檢查 web 服務"),
            SimpleNamespace(role="assistant", content="請告訴我怎樣才算正常。"),
            SimpleNamespace(role="user", content="正常運作就可以"),
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is None
    assert "服務程序正在執行，還是網頁可以正常開啟" in reply
    assert "客觀成功條件" not in reply


@pytest.mark.asyncio
async def test_chat_prompt_treats_attachment_as_concrete_rubric_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "reply": "已依附件整理評分項目。",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "服務 Port",
                            "description": "確認預期服務 Port 正在監聽。",
                            "checked": False,
                            "detectable": "auto",
                            "detection_method": "檢查 listening ports。",
                            "fallback": None,
                        }
                    ],
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="幫我增加這些項目")],
        rubric_context=json.dumps({"items": []}),
        attachment_context=(
            "--- 附件：rubric.md ---\n"
            "| 審查重點 | AI 可以參考的線索 |\n"
            "| 服務 Port | Listening ports |\n"
            "--- 附件結束 ---"
        ),
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "附件中的可讀文字就是老師提供的具體內容" in system_prompt
    assert "不要因目前項目數為 0 就回覆尚未提供內容" in system_prompt
    assert "附件表格的每一列可轉成一個檢查項目" in system_prompt
    assert captured_payload["messages"][-1]["role"] == "user"
    assert "請直接逐條核查，不要求教師再使用「新增」句型" in (
        captured_payload["messages"][-1]["content"]
    )
    assert "附件中有 Ready 變更時請依提案輸出模式回傳 updated_items" in (
        captured_payload["messages"][-1]["content"]
    )
    assert updated_items is not None
    assert updated_items[0]["title"] == "服務 Port"


def test_normalize_allows_teacher_judgement_when_script_inputs_are_complete() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "收集 main.py 輸出供老師評閱",
                "detectable": "auto",
                "judgement_mode": "teacher",
                "detection_method": "執行程式並收集 stdout 與 stderr",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "teacher"
    assert items[0].missing_information == []


@pytest.mark.asyncio
async def test_teacher_judgement_requirement_can_form_proposal_without_objective_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vllm(payload, timeout=60.0):
        return (
            json.dumps(
                {
                    "reply": "已建立取證提案，結果交由導師人工審核。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "程式架構品質",
                            "description": "檢視 main.py 原始碼的架構與可讀性。",
                            "checked": False,
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "讀取 main.py 內容供導師審核。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "cwd": "/home/student/project",
                                        "argv": ["cat", "main.py"],
                                        "timeout_seconds": 30,
                                    },
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content=(
                    "在 /home/student/project 讀取 main.py，"
                    "把程式碼交給我人工審核架構品質。"
                ),
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "teacher"
    assert "success_criteria" not in proposal[0]["check_steps"][0]["parameters"]


def test_normalize_marks_auto_without_valid_check_steps_as_unsupported() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "未知檢查",
                "detectable": "auto",
                "check_steps": [{"template_key": "n8n", "command_key": "missing"}],
            }
        ],
        template_key="n8n",
        template_commands=[],
    )

    assert items == [
        TeacherJudgeRubricItem(
            id="item-1",
            title="未知檢查",
            description="",
            checked=False,
            detectable="manual",
            detection_method="目前沒有可引用的有效 command_key，缺少自動取得客觀證據的能力",
            fallback="目前平台不支援此項目的安全腳本取證。",
            check_steps=[],
        )
    ]


@pytest.mark.asyncio
async def test_new_item_proposal_does_not_load_current_rubric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        assert payload["tool_choice"] == "auto"
        assert "既有機密項目" not in json.dumps(payload["messages"], ensure_ascii=False)
        return (
            json.dumps(
                {
                    "reply": "已把新的檔案檢查整理成提案。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-new",
                            "title": "檢查 result.txt",
                            "description": "確認 result.txt 包含 OK。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "讀取檔案並確認內容。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "argv": ["cat", "/tmp/result.txt"],
                                        "timeout_seconds": 30,
                                        "success_criteria": "stdout 包含 OK",
                                    },
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="新增檢查 result.txt 包含 OK")],
        rubric_context=json.dumps(
            {"items": [{"id": "item-existing", "title": "既有機密項目"}]},
            ensure_ascii=False,
        ),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=7,
        rubric_available=True,
    )

    assert len(calls) == 1
    assert proposal is not None
    assert proposal[0]["id"] == "item-new"
    assert proposal[0]["operation"] == "add"


@pytest.mark.asyncio
async def test_existing_item_update_loads_current_rubric_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            assert payload["tool_choice"] == "auto"
            assert "既有 Port 檢查" not in json.dumps(
                payload["messages"], ensure_ascii=False
            )
            return (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_rubric",
                            "type": "function",
                            "function": {
                                "name": "get_current_checklist",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {},
            )
        tool_result = json.loads(payload["messages"][-1]["content"])
        assert tool_result["analysis_revision"] == 7
        assert tool_result["items"][0]["title"] == "既有 Port 檢查"
        assert "tools" not in payload
        return (
            json.dumps(
                {
                    "reply": "已把 Port 調整整理成提案。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "operation": "update",
                            "id": "item-port",
                            "title": "既有 Port 檢查",
                            "description": "確認服務監聽 Port 8080。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "檢查指定 Port。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "argv": ["ss", "-ltn"],
                                        "timeout_seconds": 30,
                                        "success_criteria": "存在 8080 監聽 Port",
                                    },
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)
    current = {
        "items": [
            {
                "id": "item-port",
                "title": "既有 Port 檢查",
                "description": "確認服務監聽 Port 3000。",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "檢查指定 Port。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {
                            "argv": ["ss", "-ltn"],
                            "timeout_seconds": 30,
                            "success_criteria": "存在 3000 監聽 Port",
                        },
                    }
                ],
            }
        ]
    }

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="把第一項改成 Port 8080")],
        rubric_context=json.dumps(current, ensure_ascii=False),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=7,
        rubric_available=True,
    )

    assert len(calls) == 2
    assert proposal is not None
    assert proposal[0]["id"] == "item-port"
    assert proposal[0]["operation"] == "update"


@pytest.mark.asyncio
async def test_existing_item_proposal_without_tool_is_retried_with_forced_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    update_response = json.dumps(
        {
            "reply": "已整理修改提案。",
            "proposal_status": "ready",
            "updated_items": [
                {
                    "operation": "update",
                    "id": "item-existing",
                    "title": "既有項目",
                    "description": "更新後的說明。",
                    "detectable": "auto",
                    "judgement_mode": "ai",
                    "detection_method": "讀取指定檔案。",
                    "missing_information": [],
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.run_command",
                            "parameters": {
                                "argv": ["cat", "/tmp/result.txt"],
                                "timeout_seconds": 30,
                                "success_criteria": "exit code 為 0",
                            },
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return update_response, {}
        if len(calls) == 2:
            assert payload["tool_choice"] == {
                "type": "function",
                "function": {"name": "get_current_checklist"},
            }
            return (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_forced",
                            "type": "function",
                            "function": {
                                "name": "get_current_checklist",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {},
            )
        assert json.loads(payload["messages"][-1]["content"])["items"][0][
            "id"
        ] == "item-existing"
        return update_response, {}

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="修改既有項目的說明")],
        rubric_context=json.dumps(
            {"items": [{"id": "item-existing", "title": "既有項目"}]},
            ensure_ascii=False,
        ),
        template_commands=[GENERAL_COMMAND],
        analysis_revision=4,
        rubric_available=True,
    )

    assert len(calls) == 3
    assert proposal is not None
    assert proposal[0]["operation"] == "update"


def test_normalize_does_not_infer_python_version_intent_from_text() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "檢查 Python 版本",
                "description": "我想看學生 Python 的版本號。",
                "detectable": "manual",
                "check_steps": [],
            }
        ],
        template_key="linux",
        template_commands=[_python_version_command(), GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", ["ready", "unsupported"])
async def test_complete_manual_system_info_candidate_reselects_generic_capability(
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return (
                json.dumps(
                    {
                        "reply": "目前無法取得系統版本。",
                        "proposal_status": initial_status,
                        "conversation_focus": {
                            "turn_kind": "requirement",
                            "requirements": [
                                {
                                    "focus_key": "system-version",
                                    "status": initial_status,
                                    "known_information": ["查看學生系統版本"],
                                    "missing_information": [],
                                    "target_item_id": None,
                                }
                            ],
                        },
                        "updated_items": [
                            {
                                "operation": "add",
                                "id": "item-system-version",
                                "title": "查看學生系統版本",
                                "description": "取得學生環境的系統版本資訊。",
                                "detectable": "manual",
                                "judgement_mode": "teacher",
                                "detection_method": "查看系統資訊。",
                                "missing_information": [],
                                "check_steps": [],
                                "fallback": "由老師自行查看。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        repair_instruction = payload["messages"][-1]["content"]
        assert payload["temperature"] == 0.0
        assert len(payload["messages"]) == 2
        assert json.loads(repair_instruction)["task"] == (
            "repair_manual_capability_selection"
        )
        assert "已提供 system.run_command" in repair_instruction
        assert "只有確實無法用安全唯讀命令取得" in repair_instruction
        return (
            json.dumps(
                {
                    "reply": "我已把系統版本查詢整理成提案，請確認後再套用。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-system-version",
                            "title": "查看學生系統版本",
                            "description": "取得學生環境的系統版本資訊。",
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "執行唯讀系統版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {"argv": ["uname", "-a"]},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 2
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "teacher"
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == ["uname", "-a"]


@pytest.mark.asyncio
async def test_invalid_step_then_manual_uses_distinct_capability_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def response(item: dict[str, object]) -> tuple[str, dict[str, object]]:
        return (
            json.dumps(
                {
                    "reply": "我已整理成提案。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [item],
                },
                ensure_ascii=False,
            ),
            {},
        )

    common_item: dict[str, object] = {
        "operation": "add",
        "id": "item-system-version",
        "title": "查看學生系統版本",
        "description": "取得學生環境的系統版本資訊。",
        "judgement_mode": "teacher",
        "detection_method": "執行唯讀系統版本查詢。",
        "missing_information": [],
        "fallback": None,
    }

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return response(
                {
                    **common_item,
                    "detectable": "auto",
                    "check_steps": [
                        {
                            "template_key": "linux",
                            "command_key": "system.info",
                            "parameters": {},
                        }
                    ],
                }
            )
        if len(calls) == 2:
            assert "check_steps 沒有通過驗證" in payload["messages"][-1]["content"]
            return response(
                {
                    **common_item,
                    "detectable": "manual",
                    "check_steps": [],
                    "fallback": "由老師自行查看。",
                }
            )
        assert payload["temperature"] == 0.0
        assert len(payload["messages"]) == 2
        focused_context = json.loads(payload["messages"][-1]["content"])
        assert focused_context["task"] == "repair_manual_capability_selection"
        assert "已提供 system.run_command" in focused_context["validation_instruction"]
        return response(
            {
                **common_item,
                "detectable": "auto",
                "check_steps": [
                    {
                        "template_key": "linux",
                        "command_key": "system.run_command",
                        "parameters": {"argv": ["uname", "-a"]},
                    }
                ],
            }
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 3
    assert "整理成提案" in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"


@pytest.mark.asyncio
async def test_none_response_without_focus_does_not_invent_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return (
                json.dumps(
                    {
                        "reply": "老師您好，請問有什麼我可以幫您的嗎？",
                        "proposal_status": "none",
                        "updated_items": None,
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        assert payload["temperature"] == 0.0
        assert len(payload["messages"]) == 2
        assert "判斷 turn_kind" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "查看學生系統版本"
        return (
            json.dumps(
                {
                    "reply": "我已把系統版本查詢整理成提案，請確認後再套用。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-system-version",
                            "title": "查看學生系統版本",
                            "description": "取得學生環境的系統版本資訊。",
                            "checked": False,
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "執行唯讀系統版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {"argv": ["uname", "-a"]},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "有什麼我可以幫您" in reply
    assert proposal is None


@pytest.mark.asyncio
async def test_empty_question_classification_remains_plain_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 1:
            return (
                json.dumps(
                    {
                        "reply": "老師您好，請問有什麼我可以幫您的嗎？",
                        "proposal_status": "none",
                        "conversation_focus": {
                            "turn_kind": "question",
                            "requirements": [],
                        },
                        "updated_items": None,
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        assert payload["temperature"] == 0.0
        assert "判斷 turn_kind" in payload["messages"][0]["content"]
        assert payload["messages"][-1]["content"] == "查看學生系統版本"
        return (
            json.dumps(
                {
                    "reply": "我已把系統版本查詢整理成提案，請確認後再套用。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "requirement",
                        "requirements": [
                            {
                                "focus_key": "system-version",
                                "status": "ready",
                                "known_information": ["查看學生系統版本"],
                                "missing_information": [],
                                "target_item_id": None,
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-system-version",
                            "title": "查看學生系統版本",
                            "description": "取得學生環境的系統版本資訊。",
                            "checked": False,
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "執行唯讀系統版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {"argv": ["uname", "-a"]},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="查看學生系統版本")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is None


@pytest.mark.asyncio
async def test_boolean_detectable_and_flat_generic_argv_form_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        return (
            json.dumps(
                {
                    "reply": "我已把 Python 版本檢查整理成提案。",
                    "proposal_status": "ready",
                    "conversation_focus": {
                        "turn_kind": "follow_up",
                        "requirements": [
                            {
                                "focus_key": "python-version",
                                "status": "ready",
                                "known_information": ["檢查 Python 版本"],
                                "missing_information": [],
                            }
                        ],
                    },
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "python-version",
                            "title": "檢查 Python 版本",
                            "description": "取得學生環境的 Python 版本。",
                            "checked": False,
                            "detectable": True,
                            "judgement_mode": "teacher",
                            "detection_method": "command_output",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "command_key": "system.run_command",
                                    "argv": ["python3", "--version"],
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="再發提案")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "python3",
        "--version",
    ]


def test_normalize_preserves_structured_python_judgement_mode() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認學生環境中安裝的 Python 版本",
                "description": "取得目前安裝的 Python 版本。",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "執行 Python 版本查詢。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.version",
                        "parameters": {},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "ai"
    assert items[0].missing_information == []


def test_normalize_python_version_with_expected_answer_keeps_ai_judgement() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-1",
                "title": "確認 Python 版本至少為 3.11",
                "description": "學生環境必須安裝 Python 3.11 以上版本。",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "取得版本後與 3.11 比較。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.version",
                        "parameters": {},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert items[0].detectable == "auto"
    assert items[0].judgement_mode == "ai"


@pytest.mark.asyncio
async def test_python_version_lookup_proposal_preserves_model_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_call_vllm(payload, timeout=60.0):
        return (
            json.dumps(
                {
                    "reply": "已建立 Python 版本檢查提案。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "確認學生環境中安裝的 Python 版本",
                            "description": "取得目前安裝的 Python 版本。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "執行 Python 版本查詢。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "python",
                                    "command_key": "python.version",
                                    "parameters": {},
                                }
                            ],
                            "fallback": None,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    _reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[
            SimpleNamespace(
                role="user",
                content="確認學生環境中安裝的 Python 版本",
            )
        ],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[_python_version_command()],
    )

    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "ai"
    assert proposal[0]["check_steps"][0]["command_key"] == "python.version"


@pytest.mark.asyncio
async def test_python_version_requirement_forms_proposal_when_model_marks_it_manual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 2:
            facts = json.loads(payload["messages"][-1]["content"])
            assert facts["outcome"] == "proposal_ready"
            assert facts["proposal_created"] is True
            assert facts["items"][0]["result_handling"] == "teacher_review"
            return (
                json.dumps(
                    {
                        "reply": (
                            "「檢查 Python 版本」已整理成提案。"
                            "執行後會顯示學生使用的版本，供你查看；確認內容後再套用即可。"
                        )
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        return (
            json.dumps(
                {
                    "reply": "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "id": "item-1",
                            "title": "檢查 Python 版本",
                            "description": "查看學生使用的 Python 版本號。",
                            "detectable": "auto",
                            "judgement_mode": "teacher",
                            "detection_method": "查詢 Python 版本。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "python",
                                    "command_key": "python.version",
                                    "parameters": {},
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="我想看學生 Python 的版本號")],
        rubric_context=json.dumps({"items": []}),
        template_key="linux",
        template_commands=[_python_version_command(), GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert reply == "我已把「檢查 Python 版本」整理成提案。請先查看提案內容，確認後再套用。"
    assert "腳本取證" not in reply
    assert "AI 或導師判斷" not in reply
    assert proposal is not None
    assert proposal[0]["detectable"] == "auto"
    assert proposal[0]["judgement_mode"] == "teacher"
    assert proposal[0]["check_steps"] == [
        {
            "template_key": "python",
            "command_key": "python.version",
            "command_label": "Python 版本",
            "parameters": {},
        }
    ]


def test_normalize_rejects_unknown_python_package_command() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-torch",
                "title": "檢查 torch 套件安裝情況",
                "description": "確認學生環境是否已安裝 torch。",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "查詢套件安裝狀態。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.package_status",
                        "parameters": {"package": "torch"},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


def test_normalize_does_not_replace_python_package_version_requirement() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "id": "item-torch-version",
                "title": "確認 torch 套件版本至少 2.0",
                "detectable": "auto",
                "judgement_mode": "ai",
                "detection_method": "查詢套件版本。",
                "missing_information": [],
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.package_status",
                        "parameters": {"package": "torch"},
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


@pytest.mark.asyncio
async def test_python_package_status_forms_proposal_instead_of_system_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def fake_call_vllm(payload, timeout=60.0):
        calls.append(payload)
        if len(calls) == 2:
            return (
                json.dumps(
                    {
                        "reply": (
                            "「檢查 torch 套件安裝情況」已整理成提案。"
                            "系統會確認套件是否已安裝；請先查看提案，確認後再套用。"
                        )
                    },
                    ensure_ascii=False,
                ),
                {},
            )
        return (
            json.dumps(
                {
                    "reply": "已把 torch 套件安裝檢查整理成提案。",
                    "proposal_status": "ready",
                    "updated_items": [
                        {
                            "operation": "add",
                            "id": "item-torch",
                            "title": "檢查 torch 套件安裝情況",
                            "description": "確認學生環境是否已安裝 torch。",
                            "detectable": "auto",
                            "judgement_mode": "ai",
                            "detection_method": "查詢套件安裝狀態。",
                            "missing_information": [],
                            "check_steps": [
                                {
                                    "template_key": "linux",
                                    "command_key": "system.run_command",
                                    "parameters": {
                                        "argv": ["python3", "-m", "pip", "show", "torch"],
                                        "timeout_seconds": 30,
                                        "success_criteria": "exit code 為 0",
                                    },
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm_message", fake_call_vllm)
    _patch_teacher_judge_vllm_settings(monkeypatch)

    reply, proposal, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="檢查 torch 套件安裝情況")],
        rubric_context=json.dumps({"items": []}),
        template_key="python",
        template_commands=[GENERAL_COMMAND],
    )

    assert len(calls) == 1
    assert "整理成提案" in reply
    assert "重新產生" not in reply
    assert "管理員" not in reply
    assert proposal is not None
    assert proposal[0]["check_steps"][0]["command_key"] == "system.run_command"
    assert proposal[0]["check_steps"][0]["parameters"]["argv"] == [
        "python3",
        "-m",
        "pip",
        "show",
        "torch",
    ]


def test_unavailable_reply_explains_invalid_catalog_reference() -> None:
    raw_items = [
        {
            "id": "item-1",
            "title": "未知檢查",
            "detectable": "auto",
            "check_steps": [
                {"template_key": "n8n", "command_key": "missing.command"}
            ],
        }
    ]
    normalized = teacher_judge_service._normalize_rubric_items(
        raw_items,
        template_key="n8n",
        template_commands=[],
    )

    reply = teacher_judge_service._proposal_unavailable_reply(
        normalized,
        raw_items,
        [],
    )

    assert "缺少可執行的檢查內容" in reply
    assert "n8n/missing.command" in reply
    assert "工具清單只是優先建議，不會限制提案" in reply
    assert "沒有提供完整 argv" in reply
    assert "不是老師需要補充答案" in reply


def test_unavailable_reply_explains_missing_result_in_plain_language() -> None:
    item = TeacherJudgeRubricItem(
        id="item-answer",
        title="檢查 answer.txt 內容",
        detectable="partial",
        judgement_mode="ai",
        detection_method="讀取 answer.txt 並檢查內容。",
        missing_information=["客觀成功條件"],
    )

    reply = teacher_judge_service._proposal_unavailable_reply([item], [item.model_dump()])

    assert "關於「檢查 answer.txt 內容」" in reply
    assert "還不知道怎樣才算通過" in reply
    assert "必須包含的文字、行數、欄位值、版本或狀態" in reply
    assert "如果沒有固定答案，也可以直接說由你查看" in reply
    assert "補充後，我會重新確認並建立提案給你查看" in reply
    assert "客觀成功條件" not in reply


def test_normalize_preserves_objectively_verifiable_main_py_checkpoint() -> None:
    command = _python_entrypoint_command()
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "main.py 執行結果",
                "description": "執行 main.py，確認無錯誤並輸出整數 20。",
                "detectable": "auto",
                "detection_method": (
                    "以 exit code 與 stderr 判斷錯誤，並精確比對 stdout 是否為整數 20。"
                ),
                "fallback": "請人工執行。",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "cwd": "/home/student/project",
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                            "success_criteria": "exit code 為 0 且 stdout 等於 20",
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[command],
    )

    assert items[0].detectable == "auto"
    assert items[0].check_steps[0].command_key == "python.run_entrypoint"
    assert "stdout" in (items[0].detection_method or "")
    assert items[0].fallback is None


def test_normalize_marks_missing_python_parameters_as_missing_information() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [
            {
                "title": "main.py 執行結果",
                "detectable": "auto",
                "detection_method": "執行 main.py 並檢查 stdout",
                "check_steps": [
                    {
                        "template_key": "python",
                        "command_key": "python.run_entrypoint",
                        "parameters": {
                            "argv": ["python3", "main.py"],
                            "timeout_seconds": 30,
                            "success_criteria": "stdout 等於 20",
                        },
                    }
                ],
            }
        ],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "partial"
    assert items[0].missing_information == ["main.py 所在的工作目錄"]


def test_normalize_python_code_quality_stays_manual() -> None:
    items = teacher_judge_service._normalize_rubric_items(
        [{"title": "評估 main.py 的架構品質", "detectable": "manual"}],
        template_key="python",
        template_commands=[_python_entrypoint_command()],
    )

    assert items[0].detectable == "manual"
    assert items[0].check_steps == []


def test_prompt_formatter_handles_empty_catalog() -> None:
    assert "沒有 template command catalog" in format_template_commands_for_prompt([])


def test_prompt_formatter_does_not_expose_raw_shell_command() -> None:
    formatted = format_template_commands_for_prompt(
        [
            TeacherJudgeTemplateCommand(
                template_key="n8n",
                command_key="n8n.http_check",
                command_label="n8n HTTP 檢查",
                category="service",
                command_template="curl -I --max-time 5 http://127.0.0.1:5678",
                description="檢查本機 n8n Web 服務是否有 HTTP 回應。",
            )
        ]
    )

    assert "n8n.http_check" in formatted
    assert "template_key: n8n" in formatted
    assert "curl -I" not in formatted
