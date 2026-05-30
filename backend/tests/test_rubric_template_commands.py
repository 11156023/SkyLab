from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import Session, SQLModel, create_engine

from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.schemas import RubricItem
from app.ai.teacher_judge.template_command_service import (
    format_template_commands_for_prompt,
    get_enabled_template_commands,
)
from app.api.routes import rubric as rubric_route
from app.models.teacher_judge_template_command import TeacherJudgeTemplateCommand


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


def test_get_enabled_template_commands_filters_template_and_enabled() -> None:
    session = _session_with_commands()

    commands = get_enabled_template_commands(session, "n8n")

    assert [command.command_key for command in commands] == ["n8n.port_check"]


@pytest.mark.asyncio
async def test_analyze_rubric_injects_catalog_and_normalizes_check_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = TeacherJudgeTemplateCommand(
        template_key="n8n",
        command_key="n8n.port_check",
        command_label="n8n 連接埠檢查",
        category="port",
        command_template="ss -lntp | grep ':5678'",
        description="檢查 n8n 預設 5678 連接埠是否正在監聽。",
    )
    captured_payload = {}

    async def fake_call_vllm(payload, timeout=60.0):
        captured_payload.update(payload)
        return (
            json.dumps(
                {
                    "items": [
                        {
                            "id": "item-1",
                            "title": "n8n 服務可啟動",
                            "checked": True,
                            "detectable": "auto",
                            "detection_method": "檢查 n8n port",
                            "check_steps": [
                                {
                                    "template_key": "n8n",
                                    "command_key": "n8n.port_check",
                                },
                                {
                                    "template_key": "n8n",
                                    "command_key": "n8n.missing",
                                },
                            ],
                        }
                    ],
                    "summary": "ok",
                }
            ),
            {"total_tokens": 1},
        )

    monkeypatch.setattr(teacher_judge_service, "_call_vllm", fake_call_vllm)

    analysis, _metrics = await teacher_judge_service.analyze_rubric(
        "rubric text",
        template_key="n8n",
        template_commands=[command],
    )

    system_prompt = captured_payload["messages"][0]["content"]
    assert "目前選定 template：n8n" in system_prompt
    assert "n8n.port_check" in system_prompt
    assert "不得自行發明" in system_prompt
    assert analysis.items[0].check_steps[0].command_key == "n8n.port_check"
    assert analysis.items[0].check_steps[0].command_label == "n8n 連接埠檢查"
    assert len(analysis.items[0].check_steps) == 1
    assert analysis.items[0].checked is False


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

    async def fake_call_vllm(payload, timeout=60.0):
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

    monkeypatch.setattr(teacher_judge_service, "_call_vllm", fake_call_vllm)

    _reply, updated_items, _metrics = await teacher_judge_service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="照這樣改")],
        rubric_context=json.dumps({"items": [{"id": "item-1"}]}),
        template_key="n8n",
        template_commands=[command],
    )

    assert updated_items is not None
    assert updated_items[0]["check_steps"] == [
        {
            "template_key": "n8n",
            "command_key": "n8n.http_check",
            "command_label": "n8n HTTP 檢查",
        }
    ]


def test_normalize_downgrades_auto_without_valid_check_steps() -> None:
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
        RubricItem(
            id="item-1",
            title="未知檢查",
            description="",
            checked=False,
            detectable="partial",
            detection_method="目前沒有可引用的有效 command_key，需人工或後續檢查輔助判斷",
            fallback=None,
            check_steps=[],
        )
    ]


@pytest.mark.asyncio
async def test_upload_rubric_defaults_linux_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session_with_commands()

    async def fake_analyze_rubric(raw_text, template_key, template_commands):
        assert raw_text == "parsed text"
        assert template_key == "linux"
        assert template_commands == []
        return (
            SimpleNamespace(model_dump=lambda: {"items": []}),
            {"total_tokens": 0},
        )

    monkeypatch.setattr(rubric_route, "parse_document", lambda *_args: "parsed text")
    monkeypatch.setattr(rubric_route, "analyze_rubric", fake_analyze_rubric)

    response = await rubric_route.upload_rubric(
        current_user=SimpleNamespace(email="teacher@example.com"),
        session=session,
        file=UploadFile(filename="rubric.pdf", file=BytesIO(b"pdf")),
        template_key="linux",
    )

    assert response["template_key"] == "linux"


@pytest.mark.asyncio
async def test_upload_rubric_rejects_unknown_template() -> None:
    session = _session_with_commands()

    with pytest.raises(HTTPException) as exc_info:
        await rubric_route.upload_rubric(
            current_user=SimpleNamespace(email="teacher@example.com"),
            session=session,
            file=UploadFile(filename="rubric.pdf", file=BytesIO(b"pdf")),
            template_key="unknown",
        )

    assert exc_info.value.status_code == 400


def test_prompt_formatter_handles_empty_catalog() -> None:
    assert "沒有 template command catalog" in format_template_commands_for_prompt([])
