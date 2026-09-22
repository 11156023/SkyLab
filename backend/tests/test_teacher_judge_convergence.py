from __future__ import annotations

import pytest

from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.automation_support import (
    ensure_script_generation_supported,
    get_script_generation_blockers,
)
from app.ai.teacher_judge.machine_context import (
    canonicalize_machine_node_key,
    format_machine_context,
    rubric_item_machine_issues,
    target_node_keys_from_snapshot,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeScriptRunCreateRequest,
)
from app.ai.teacher_judge.script_run_service import (
    _public_target,
    _public_target_snapshot,
)
from app.ai.teacher_judge.service import _normalize_rubric_items
from app.ai.teacher_judge.template_command_service import validate_check_steps


def test_check_step_schema_keeps_flat_read_compatibility_and_exposes_typed_contract() -> None:
    flat = TeacherJudgeRubricCheckStep(
        argv=["curl", "--fail", "http://127.0.0.1:8080/health"],
        cwd="/workspace",
        timeout_seconds=20,
    )
    assert flat.model_dump(mode="json") == {
        "argv": ["curl", "--fail", "http://127.0.0.1:8080/health"],
        "cwd": "/workspace",
        "timeout_seconds": 20,
    }
    schema = TeacherJudgeRubricCheckStep.model_json_schema()
    assert {"argv", "cwd", "timeout_seconds"}.issubset(schema["properties"])
    assert {"id", "title", "collector", "assertion"}.issubset(schema["properties"])
    assert {"required": ["argv"]} in schema["anyOf"]
    assert {"required": ["collector", "id", "title"]} in schema["anyOf"]

    legacy = TeacherJudgeRubricCheckStep(
        template_key="linux",
        command_key="system.run_command",
        parameters={"argv": ["uname", "-a"], "timeout_seconds": 30},
    )
    assert legacy.model_dump(mode="json")["command_key"] == "system.run_command"


def test_flat_steps_normalize_without_a_template_command_catalog() -> None:
    validated = validate_check_steps(
        "linux",
        [{"check_steps": [{"argv": ["uname", "-a"], "timeout_seconds": "5"}]}],
        [],
    )
    assert validated == [
        {
            "check_steps": [{"argv": ["uname", "-a"], "timeout_seconds": 5}]
        }
    ]

    items = _normalize_rubric_items(
        [
            {
                "id": "web-health",
                "title": "Web health",
                "target_node_key": "web",
                "detectable": "auto",
                "detection_method": "HTTP response",
                "check_steps": [
                    {
                        "argv": ["curl", "--fail", "http://127.0.0.1:8080/health"],
                        "cwd": "/workspace",
                        "timeout_seconds": 20,
                    }
                ],
            }
        ],
        template_key="linux",
        template_commands=[],
    )
    analysis = TeacherJudgeRubricAnalysis(items=items)
    ensure_script_generation_supported(analysis, [])
    assert analysis.model_dump(mode="json")["items"][0]["check_steps"] == [
        {
            "argv": ["curl", "--fail", "http://127.0.0.1:8080/health"],
            "cwd": "/workspace",
            "timeout_seconds": 20,
        }
    ]


def test_machine_context_is_logical_and_target_collection_is_class_scoped() -> None:
    context = format_machine_context(
        [
            {
                "display_label": "P1",
                "node_key": "web",
                "name": "Web Server",
                "role": "frontend",
                "resource_type": "qemu",
            }
        ]
    )
    assert "P1" in context
    assert "node_key=web" in context
    assert "VMID" not in context
    assert "SSH" not in context
    assert "192.168." not in context

    assert target_node_keys_from_snapshot(
        {
            "target_node_key": "web",
            "items": [{"target_node_key": "web"}],
        }
    ) == {"web"}


def test_machine_aliases_canonicalize_only_keys_and_derived_p_labels() -> None:
    entries = [
        {"display_label": "P1", "node_key": "web", "name": "Web Server"},
        {"display_label": "P2", "node_key": "db", "name": "Database"},
    ]

    assert canonicalize_machine_node_key("web", entries) == "web"
    assert canonicalize_machine_node_key("p2", entries) == "db"
    assert canonicalize_machine_node_key(None, entries) is None
    with pytest.raises(ValueError, match="node_key"):
        canonicalize_machine_node_key("Database", entries)


def test_proposal_tools_are_request_scoped_to_current_class_nodes() -> None:
    tools = teacher_judge_service._build_proposal_tools(
        [
            {"display_label": "P1", "node_key": "web"},
            {"display_label": "P2", "node_key": "db"},
        ]
    )

    for tool in tools:
        if tool["function"]["name"] not in {
            "create_checklist_item",
            "edit_checklist_item",
        }:
            continue
        properties = tool["function"]["parameters"]["properties"]
        assert properties["target_node_key"]["enum"] == ["web", "db", None]
        assert properties["peer_node_key"]["enum"] == ["web", "db", None]
        assert "P1=web" in properties["target_node_key"]["description"]


def test_finalizer_tools_expose_only_typed_check_steps() -> None:
    tools = teacher_judge_service._build_proposal_tools(finalizer=True)

    for tool in tools:
        if tool["function"]["name"] not in {
            "create_checklist_item",
            "edit_checklist_item",
        }:
            continue
        step_schema = tool["function"]["parameters"]["properties"]["check_steps"]["items"]
        assert set(step_schema["properties"]) == {"id", "title", "collector", "assertion"}
        assert "argv" not in step_schema["properties"]
        assert step_schema["required"] == ["id", "title", "collector"]


def test_finalizer_rejects_a_malformed_step_without_dropping_it() -> None:
    error = teacher_judge_service._finalizer_check_step_error(
        [
            {
                "id": "log.tail",
                "title": "讀取日誌尾端",
                "collector": {
                    "type": "file_text",
                    "path": "/var/log/app.log",
                    "read_mode": "tail",
                },
            },
        ]
    )

    assert error is not None
    assert "typed contract 無效" in error


def test_typed_plan_semantic_errors_block_readiness_before_script_creation() -> None:
    analysis = TeacherJudgeRubricAnalysis(
        items=[
            {
                "id": "service",
                "title": "服務狀態",
                "detectable": "auto",
                "detection_method": "檢查服務狀態",
                "check_steps": [
                    {
                        "id": "service.stat",
                        "title": "取得服務狀態",
                        "collector": {"type": "file_stat", "path": "/tmp/service"},
                    }
                ],
            }
        ]
    )

    blockers = get_script_generation_blockers(
        analysis,
        [],
        require_typed_plan=True,
    )

    assert blockers[0]["reason_code"] == "check_plan_contract_invalid"
    assert blockers[0]["status"] == "analysis_error"
    assert "assertion" in blockers[0]["detail"]


def test_typed_readiness_does_not_silently_compile_legacy_flat_steps() -> None:
    analysis = TeacherJudgeRubricAnalysis(
        items=[
            {
                "id": "legacy-service",
                "title": "舊服務檢查",
                "detectable": "auto",
                "detection_method": "執行唯讀命令",
                "target_node_key": "web",
                "check_steps": [
                    {
                        "argv": ["systemctl", "is-active", "n8n"],
                        "timeout_seconds": 30,
                    }
                ],
            }
        ]
    )

    blockers = get_script_generation_blockers(
        analysis,
        [],
        require_typed_plan=True,
    )

    assert blockers[0]["status"] == "analysis_error"
    assert blockers[0]["reason_code"] == "check_plan_contract_invalid"
    assert "flat legacy" in blockers[0]["detail"]


def test_peer_contract_requires_distinct_node_and_whole_argv_token() -> None:
    valid = {
        "detectable": "auto",
        "target_node_key": "db",
        "peer_node_key": "web",
        "check_steps": [{"argv": ["ping", "-c", "4", "{{peer.ip}}"]}],
    }
    assert rubric_item_machine_issues(valid) == []
    assert rubric_item_machine_issues(
        {**valid, "target_node_key": "web"}
    ) == ["peer_node_key 不得與 target_node_key 相同"]
    assert rubric_item_machine_issues(
        {**valid, "check_steps": [{"argv": ["ping", "host={{peer.ip}}"]}]}
    ) == ["{{peer.ip}} 只能作為完整 argv element", "指定 peer_node_key 的 auto 項目必須在 argv 使用 {{peer.ip}}"]


def test_node_scope_is_canonical_but_legacy_vmid_scope_remains_readable() -> None:
    node_request = TeacherJudgeScriptRunCreateRequest(
        target_scope="all_students_on_node",
        target_node_key="web",
    )
    assert node_request.target_node_key == "web"

    legacy_request = TeacherJudgeScriptRunCreateRequest(
        target_scope="all_with_vm",
        target_vmids=[101, 101],
    )
    assert legacy_request.target_vmids == [101]


def test_public_run_projection_does_not_expose_provider_connection_fields() -> None:
    target = _public_target(
        {
            "student_id": "student-1",
            "node_key": "web",
            "display_label": "P1",
            "vmid": 101,
            "proxmox_node": "pve-1",
            "ip_address": "192.0.2.10",
            "ssh_user": "root",
            "private_key_pem": "secret",
            "os_info": "Ubuntu 24.04",
            "environment_type": "linux",
        }
    )
    assert target == {
        "student_id": "student-1",
        "node_key": "web",
        "display_label": "P1",
    }
    snapshot = _public_target_snapshot(
        {
            "script": {"id": "artifact-1", "template_key": "linux"},
            "targets": [{"vmid": 101, "node_key": "web"}],
        }
    )
    assert snapshot == {
        "script": {"id": "artifact-1"},
        "targets": [{"node_key": "web"}],
    }
