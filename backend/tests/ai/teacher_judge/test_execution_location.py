"""Location gaps must agree across proposals, Finalizer, and script creation."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.ai.teacher_judge import service
from app.ai.teacher_judge.automation_support import get_script_generation_blockers
from app.ai.teacher_judge.deterministic_compiler import (
    CheckPlanContractError,
    canonicalize_check_plan,
    compile_check_plan,
    missing_execution_location,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeSessionMessageCreateRequest,
)
from app.ai.teacher_judge.template_command_service import GENERAL_COMMAND
from app.api.routes import teacher_judge_sessions as routes
from app.models.teacher_judge_session import (
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
)
from tests.ai.teacher_judge.helpers import (
    make_session,
    make_teacher_judge_file,
    patch_teacher_judge_vllm_settings,
    reply_message,
    scripted_vllm,
    tool_call_message,
)


def _candidate(step: dict) -> dict:
    return {
        "id": "program",
        "title": "收集作業程式輸出",
        "detectable": "auto",
        "judgement_mode": "teacher",
        "detection_method": "收集輸出供老師檢查",
        "check_steps": [step],
    }


def _typed(collector: dict) -> dict:
    return {"id": "output", "title": "取得作業結果", "collector": collector}


@pytest.mark.parametrize(
    "collector",
    [
        {"type": "command", "argv": ["python3", "main.py"]},
        {"type": "command", "argv": ["python3", "-W", "ignore", "main.py"]},
        {"type": "command", "argv": ["node", "app.js"]},
        {"type": "command", "argv": ["php", "-f", "app.php"]},
        {"type": "command", "argv": ["go", "run", "main.go"]},
        {"type": "command", "argv": ["cat", "result.txt"]},
        {"type": "command", "argv": ["head", "-n", "5", "result.txt"]},
        {"type": "command", "argv": ["tail", "-n5", "result.txt"]},
        {"type": "command", "argv": ["grep", "-e", "passed", "result.txt"]},
        {"type": "command", "argv": ["grep", "-fpatterns.txt", "/tmp/result.txt"]},
        {"type": "command", "argv": ["ls", "-la"]},
        {"type": "command", "argv": ["find", ".", "-name", "*.py"]},
        {"type": "command", "argv": ["test", "-f", "main.py"]},
        {"type": "command", "argv": ["awk", "{print $1}", "result.txt"]},
        {"type": "command", "argv": ["yq", "eval", ".status", "result.yml"]},
        {"type": "command", "argv": ["python3", "/srv/main.py"], "cwd": "."},
        {"type": "command", "argv": ["cat", "~/result.txt"], "cwd": "/srv"},
        {"type": "file_text", "path": "result.txt"},
        {"type": "file_stat", "path": "./result.txt"},
        {"type": "command", "argv": ["cat", ".env"], "cwd": ""},
        {"type": "command", "argv": ["python3", "main.py"], "cwd": "  "},
        {"type": "file_text", "path": ".env", "cwd": ""},
        {"type": "file_stat", "path": ".env", "cwd": "  "},
        {"type": "file_text", "path": ".env", "cwd": "~/project"},
        {"type": "file_stat", "path": ".env", "cwd": "project"},
        {"type": "file_text", "path": "~/.env", "cwd": "/srv/student"},
        {"type": "file_stat", "path": "$HOME/.env", "cwd": "/srv/student"},
    ],
)
def test_proposal_readiness_and_compiler_agree_on_unresolved_paths(collector) -> None:
    raw = _candidate(_typed(collector))
    normalized = service._normalize_rubric_items([raw])[0]
    assert normalized.detectable == "partial"
    assert normalized.missing_information
    # Also validate raw persisted auto items; don't rely on Chat normalization.
    analysis = TeacherJudgeRubricAnalysis(items=[raw])
    blockers = get_script_generation_blockers(analysis, [], require_typed_plan=True)
    assert blockers[0]["status"] == "missing_info"
    assert blockers[0]["missing_information"] == normalized.missing_information
    with pytest.raises(CheckPlanContractError) as exc:
        canonicalize_check_plan(analysis, require_target_node=False)
    assert any("路徑" in issue["message"] for issue in exc.value.issues)


@pytest.mark.parametrize(
    "collector",
    [
        {"type": "command", "argv": ["python3", "main.py"], "cwd": "/srv/student"},
        {"type": "command", "argv": ["python3", "/srv/student/main.py"]},
        {"type": "command", "argv": ["python3", "--version"]},
        {"type": "command", "argv": ["systemctl", "is-active", "nginx"]},
        {"type": "command", "argv": ["journalctl", "-u", "nginx", "-n", "20"]},
        {"type": "command", "argv": ["head", "-n", "20", "/tmp/result.txt"]},
        {"type": "command", "argv": ["grep", "-A2", "passed", "/tmp/result.txt"]},
        {"type": "command", "argv": ["grep", "-epassed", "/tmp/result.txt"]},
        {"type": "command", "argv": ["sed", "-n", "1,5p", "/tmp/result.txt"]},
        {"type": "command", "argv": ["stat", "--format=%s", "/tmp/result.txt"]},
        {"type": "command", "argv": ["jq", ".status", "/tmp/result.json"]},
        {"type": "command", "argv": ["find", "/srv/student", "-name", "*.py"]},
        {"type": "command", "argv": ["test", "-f", "/srv/student/main.py"]},
        {
            "type": "command",
            "argv": ["awk", "-F", ",", "{print $1}", "/tmp/result.txt"],
        },
        {"type": "command", "argv": ["yq", "eval", ".status", "/tmp/result.yml"]},
        {"type": "command", "argv": ["cat", "作業.txt"], "cwd": "/srv/學生作業"},
        {"type": "command", "argv": ["cat", "result.txt"], "cwd": r"C:\Users\student"},
        {"type": "file_text", "path": "/srv/student/result.txt"},
        {"type": "file_stat", "path": "/srv/student/result.txt"},
    ],
)
def test_known_paths_and_location_independent_queries_remain_ready(collector) -> None:
    analysis = TeacherJudgeRubricAnalysis(items=[_candidate(_typed(collector))])
    assert get_script_generation_blockers(analysis, [], require_typed_plan=True) == []


@pytest.mark.parametrize("cwd", [None, "", "  \t"])
@pytest.mark.parametrize("argv", [
    ["python3", "--version"],
    ["systemctl", "is-active", "nginx"],
    ["lscpu"],
    ["cat", "/srv/student/.env"],
])
def test_optional_directory_survives_normalization_save_and_compile(cwd, argv, monkeypatch):
    from app.ai.teacher_judge import file_service

    raw = _candidate(_typed({"type": "command", "argv": argv, "cwd": cwd}))
    item = service._normalize_rubric_items([raw])[0]
    assert item.detectable == "auto"
    assert item.missing_information == []
    analysis = TeacherJudgeRubricAnalysis(items=[item])
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *_: [])
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id, analysis=analysis,
        )
        restored = TeacherJudgeRubricAnalysis.model_validate(saved.analysis_json)
    assert restored.items[0].check_steps[0].collector.cwd is None
    assert get_script_generation_blockers(restored, [], require_typed_plan=True) == []
    restored.items[0].target_node_key = "web"
    _, policy, _, _ = compile_check_plan(restored, target_node_key="web")
    assert policy["approved"] is True


@pytest.mark.parametrize("kind", ["file_text", "file_stat"])
def test_file_collectors_accept_explicit_directory(kind, monkeypatch):
    from app.ai.teacher_judge import file_service

    raw = _candidate(_typed({"type": kind, "path": ".env", "cwd": "/srv/student"}))
    item = service._normalize_rubric_items([raw])[0]
    assert item.detectable == "auto"
    analysis = TeacherJudgeRubricAnalysis(items=[item])
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        monkeypatch.setattr(file_service, "load_class_machine_nodes", lambda *_: [])
        saved = file_service.update_file_analysis(
            session=db, teaching_class_id=class_id, file_id=file.id, analysis=analysis,
        )
        analysis = TeacherJudgeRubricAnalysis.model_validate(saved.analysis_json)
    assert get_script_generation_blockers(analysis, [], require_typed_plan=True) == []
    analysis.items[0].target_node_key = "web"
    script, policy, _, plan = compile_check_plan(analysis, target_node_key="web")
    assert policy["approved"] is True
    assert '/srv/student/.env' in script
    assert plan["items"][0]["check_steps"][0]["collector"]["cwd"] == "/srv/student"


def test_separately_valid_commands_do_not_trigger_cross_step_deny_pattern():
    items = []
    for index, argv in enumerate([
        ["find", "/srv/student", "-name", "*.py"],
        ["cat", "/srv/student/to-delete.txt"],
    ]):
        item = _candidate(_typed({"type": "command", "argv": argv}))
        item.update(id=f"item-{index}", target_node_key="web")
        item["check_steps"][0]["id"] = f"step-{index}"
        analysis = TeacherJudgeRubricAnalysis(items=[item])
        assert get_script_generation_blockers(analysis, [], require_typed_plan=True) == []
        items.append(item)
    script, policy, _, _ = compile_check_plan(
        TeacherJudgeRubricAnalysis(items=items), target_node_key="web",
    )
    assert policy["approved"] is True
    assert "to-delete.txt" in script


def test_module_package_query_does_not_ask_for_student_directory() -> None:
    # Authorization of module execution is a separate command policy concern.
    assert (
        missing_execution_location(
            {
                "type": "command",
                "argv": ["python3", "-m", "pip", "show", "torch"],
            }
        )
        == []
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["jq", "--arg", "name", "student", ".name == $name", "/tmp/result.json"],
        ["jq", "--from-file", "/tmp/filter.jq", "/tmp/result.json"],
        ["ls", "--color", "/srv/student"],
        ["grep", "--regexp=passed", "/tmp/result.txt"],
    ],
)
def test_option_values_are_not_mistaken_for_relative_files(argv) -> None:
    assert missing_execution_location({"type": "command", "argv": argv}) == []


@pytest.mark.parametrize("reply", ["已通過檢查。", "stdout: 42\nstderr: none", ""])
async def test_missing_path_rejects_proposal_and_asks_even_when_model_does_not(
    monkeypatch,
    reply,
) -> None:
    command = {"argv": ["python3", "main.py"], "timeout_seconds": 30}
    step = _typed({"type": "command", **command})
    candidate = _candidate(step)
    candidate.pop("id")
    calls, fake = scripted_vllm(
        [
            tool_call_message("create_checklist_item", candidate),
            reply_message(reply, "ready" if reply.startswith("已") else "none"),
        ]
    )
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    patch_teacher_judge_vllm_settings(monkeypatch)
    result = await service.chat_with_rubric(
        messages=[
            SimpleNamespace(role="user", content="執行 main.py，把輸出交給我檢查")
        ],
        rubric_context='{"items": []}',
        template_key="linux",
        template_commands=None,
        rubric_available=True,
    )
    assert len(calls) == 2
    assert result.proposal is None
    assert result.proposal_status == "needs_information"
    assert "請補上完整路徑，或工作目錄與相對路徑" in result.reply
    assert "stdout" not in result.reply
    reason = result.tool_calls[-1]["reason"]
    assert "不需要老師補充" not in reason
    assert "不要猜測" in reason
    assert result.conversation_focus["requirements"][0]["missing_information"]


async def test_followup_with_directory_clears_gap_and_stages_update(
    monkeypatch,
) -> None:
    raw = _candidate(_typed({"type": "command", "argv": ["python3", "main.py"]}))
    raw["detectable"] = "partial"
    raw["missing_information"] = ["main.py 所在的工作目錄"]
    calls, fake = scripted_vllm(
        [
            tool_call_message("get_checklist_item", {"id": "program"}),
            tool_call_message(
                "edit_checklist_item",
                {
                    "id": "program",
                    "detectable": "auto",
                    "check_steps": [
                        _typed(
                            {
                                "type": "command",
                                "argv": ["python3", "main.py"],
                                "cwd": "/srv/student",
                            }
                        )
                    ],
                },
            ),
            reply_message("已補上工作目錄，請確認提案。", "ready"),
        ]
    )
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    patch_teacher_judge_vllm_settings(monkeypatch)
    result = await service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content="程式在 /srv/student")],
        rubric_context=json.dumps({"items": [raw]}),
        template_key="linux",
        rubric_available=True,
    )
    assert len(calls) == 3
    assert result.proposal[0]["detectable"] == "auto"
    assert result.proposal[0]["missing_information"] == []
    assert result.proposal[0]["check_steps"][0]["collector"]["cwd"] == "/srv/student"


@pytest.mark.parametrize("refine", [False, True])
async def test_file_directory_followup_clears_old_gap_in_chat_and_finalizer(monkeypatch, refine):
    from app.ai.teacher_judge.session_service import (
        apply_proposal_operations_to_analysis,
    )

    raw = _candidate(_typed({"type": "file_text", "path": ".env"}))
    raw.update(detectable="partial", missing_information=[".env 所在的工作目錄"])
    calls, fake = scripted_vllm([
        tool_call_message("get_checklist_item", {"id": "program"}),
        tool_call_message("edit_checklist_item", {
            "id": "program", "detectable": "auto", "check_steps": [
                _typed({"type": "file_text", "path": ".env", "cwd": "/srv/student"}),
            ],
        }),
        reply_message("已補上工作目錄。", "ready"),
    ])
    monkeypatch.setattr(service, "_call_vllm_message", fake)
    patch_teacher_judge_vllm_settings(monkeypatch)
    result = await service.chat_with_rubric(
        messages=[SimpleNamespace(role="user", content=".env 在 /srv/student，收集內容供我檢查")],
        rubric_context=json.dumps({"items": [raw]}), template_key="linux",
        rubric_available=True, is_refine=refine,
    )
    assert len(calls) == 3
    assert result.proposal[0]["missing_information"] == []
    candidate = apply_proposal_operations_to_analysis(
        TeacherJudgeRubricAnalysis(items=[raw]), result.proposal,
    )
    assert get_script_generation_blockers(candidate, [], require_typed_plan=True) == []
    candidate.items[0].target_node_key = "web"
    _, policy, _, _ = compile_check_plan(candidate, target_node_key="web")
    assert policy["approved"] is True


@pytest.mark.parametrize("stage", ["refine", "create"])
async def test_routes_persist_visible_path_question_and_never_create_script(
    monkeypatch, stage
) -> None:
    with make_session() as db:
        class_id = uuid.uuid4()
        file = make_teacher_judge_file(db, class_id)
        file.analysis_json = {
            "items": [
                _candidate(
                    _typed(
                        {
                            "type": "command",
                            "argv": ["python3", "main.py"],
                        }
                    )
                )
            ]
        }
        item = TeacherJudgeSession(
            teaching_class_id=class_id,
            title="程式檢查",
            selected_file_id=file.id,
        )
        db.add_all([file, item])
        db.commit()
        original = file.analysis_json
        monkeypatch.setattr(routes, "_access", lambda *_: None)
        monkeypatch.setattr(
            routes, "get_enabled_template_commands", lambda *a, **kw: []
        )

        async def fake_chat(*args, **kwargs):
            return "所有檢查項目已通過，狀態良好。", [], {}

        def unexpected_compile(**kwargs):
            pytest.fail("A missing location must stop before creating artifacts")

        monkeypatch.setattr(routes, "chat_with_rubric", fake_chat)
        monkeypatch.setattr(routes, "create_artifact_set", unexpected_compile)
        user = SimpleNamespace(id=uuid.uuid4())
        if stage == "refine":
            result = await routes.create_message(
                class_id,
                item.id,
                TeacherJudgeSessionMessageCreateRequest(
                    content="儲存並製作腳本", is_refine=True
                ),
                db,
                user,
            )
            assert result.assistant_message.metadata_json["script_ready"] is False
        else:
            with pytest.raises(HTTPException) as exc:
                await routes.create_session_script_set(class_id, item.id, db, user)
            assert exc.value.status_code == 422
            assert exc.value.detail["items"][0]["status"] == "missing_info"
        messages = db.exec(select(TeacherJudgeSessionMessage)).all()
        assistant = next(
            message for message in messages if message.role.value == "assistant"
        )
        assert "請提供該程式／檔案的完整路徑" in assistant.content
        assert "狀態良好" not in assistant.content
        assert assistant.metadata_json["status"] == "needs_information"
        assert assistant.metadata_json["conversation_focus"]["requirements"][0][
            "missing_information"
        ]
        db.refresh(file)
        assert file.analysis_json == original


def test_compile_contract_error_keeps_specific_reason_in_chat(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(
        routes, "_save_workflow_message", lambda *a, **kw: saved.append(kw)
    )
    routes._save_script_set_failure(
        None,
        SimpleNamespace(),
        detail={
            "code": "teacher_judge_check_plan_invalid",
            "issues": [
                {
                    "item_id": "program",
                    "step_id": "output",
                    "message": "缺少完整工作目錄",
                }
            ],
        },
        stage="script_generation",
        status_code=422,
        source_file_id=uuid.uuid4(),
        analysis_revision=1,
        created_by=None,
    )
    assert "缺少完整工作目錄" in saved[0]["content"]
    assert saved[0]["metadata"]["status"] == "analysis_error"
    assert saved[0]["metadata"]["stage"] == "script_generation"
    assert saved[0]["metadata"]["reason_code"] == "teacher_judge_check_plan_invalid"
