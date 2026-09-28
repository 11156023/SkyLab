"""Behaviour pins for the Teacher Judge scripts cleanup (shared helpers, removed dead code)."""

from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import deterministic_compiler, machine_context
from app.ai.teacher_judge import script_executor_service as executor
from app.ai.teacher_judge import script_run_service as run_service
from app.ai.teacher_judge.script_artifact_service import latest_set_children
from app.ai.teacher_judge.script_policy import (
    PEER_IP_TOKEN,
    validate_managed_script_output,
)
from app.ai.teacher_judge.script_quality_validator import (
    _except_name,
    _record_check_has_raw_parameter,
)
from app.models.teacher_judge_script_artifact import TeacherJudgeScriptStatus


def _result_payload(status: str) -> str:
    return json.dumps(
        {
            "schema_version": "teacher_judge_result.v1",
            "metadata": {"timestamp": "2026-09-27T00:00:00Z", "platform": "linux"},
            "checks": [{"id": "c1", "title": "check", "status": status}],
            "errors": [],
        }
    )


def test_result_status_must_already_be_canonical() -> None:
    assert validate_managed_script_output(_result_payload("pass"))["valid"] is True
    # The Literal type rejects non-canonical spellings; nothing normalizes them.
    assert validate_managed_script_output(_result_payload("PASS"))["valid"] is False
    assert validate_managed_script_output(_result_payload(" pass"))["valid"] is False


def test_peer_ip_token_has_one_definition() -> None:
    assert PEER_IP_TOKEN == "{{peer.ip}}"
    assert deterministic_compiler.PEER_IP_TOKEN is PEER_IP_TOKEN
    assert machine_context.PEER_IP_TOKEN == PEER_IP_TOKEN


def _handler(source: str) -> ast.ExceptHandler:
    tree = ast.parse(f"try:\n    pass\n{source}\n    pass\n")
    handler = tree.body[0].handlers[0]  # type: ignore[attr-defined]
    assert isinstance(handler, ast.ExceptHandler)
    return handler


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("except:", None),
        ("except Exception:", "Exception"),
        ("except sp.TimeoutExpired:", "subprocess.TimeoutExpired"),
        ("except (OSError, ValueError):", None),
        ("except make_exc():", None),
    ],
)
def test_except_name_only_resolves_plain_and_dotted_names(
    source: str, expected: str | None
) -> None:
    assert _except_name(_handler(source), {"sp": "subprocess"}) == expected


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("def record_check(check_id, title, status, evidence, raw): pass", True),
        ("def record_check(check_id, *, raw=''): pass", True),
        ("def record_check(check_id, title, status, evidence): pass", False),
    ],
)
def test_record_check_has_raw_parameter(signature: str, expected: bool) -> None:
    function_def = ast.parse(signature).body[0]
    assert isinstance(function_def, ast.FunctionDef)
    assert _record_check_has_raw_parameter(function_def) is expected


def test_preflight_progress_defaults_status_to_failed() -> None:
    rows = executor.preflight_progress(
        [{"vmid": 7, "name": "7", "reason_code": "missing_vmid"}]
    )
    assert rows == [
        {
            "vmid": 7,
            "name": "7",
            "student_id": None,
            "node_key": None,
            "node_name": None,
            "display_label": None,
            "proxmox_node": None,
            "resource_type": None,
            "user": None,
            "status": "failed",
            "reason_code": "missing_vmid",
        }
    ]


def test_live_resources_delegate_to_proxmox_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = {101: {"vmid": 101, "status": "running", "type": "qemu"}}
    monkeypatch.setattr(
        executor.proxmox_ops, "list_all_resources_by_vmid", lambda: resources
    )
    assert executor._live_running_by_vmid() == resources
    assert run_service._running_resources_by_vmid() == resources


def test_running_resources_lookup_failure_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> dict[int, dict[str, Any]]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(
        run_service.proxmox_ops, "list_all_resources_by_vmid", unavailable
    )
    with pytest.raises(HTTPException) as exc_info:
        run_service._running_resources_by_vmid()
    assert exc_info.value.status_code == 503


def test_ensure_targets_on_node_reports_mismatched_nodes() -> None:
    targets = [{"node_key": "web"}, {"node_key": "db"}, {"node_key": None}]
    run_service._ensure_targets_on_node(targets, None, "msg")
    run_service._ensure_targets_on_node([{"node_key": "web"}], "web", "msg")
    with pytest.raises(HTTPException) as exc_info:
        run_service._ensure_targets_on_node(targets, "web", "手動執行目標不符")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {
        "code": "teacher_judge_target_node_mismatch",
        "message": "手動執行目標不符",
        "artifact_target_node_key": "web",
        "mismatched_node_keys": ["", "db"],
    }


def test_latest_set_children_skips_archived_and_orders_by_node() -> None:
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)

    def row(node_key: str, version: int, status: TeacherJudgeScriptStatus, minutes: int = 0) -> Any:
        return SimpleNamespace(
            target_node_key=node_key,
            version=version,
            status=status,
            created_at=base + timedelta(minutes=minutes),
        )

    approved = TeacherJudgeScriptStatus.approved
    web_old = row("web", 1, approved)
    web_new = row("web", 2, approved)
    web_archived = row("web", 3, TeacherJudgeScriptStatus.archived)
    db_first = row("db", 1, approved, minutes=0)
    db_later = row("db", 1, approved, minutes=5)
    children = latest_set_children(
        [web_old, web_new, web_archived, db_first, db_later],
        node_order={"web": 0, "db": 1},
    )
    assert children == [web_new, db_later]


def test_resolve_node_targets_reuses_node_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class_id = uuid.uuid4()
    node = SimpleNamespace(node_key="web", name="Web", role=None)
    members = [
        {"student_id": "s1", "vmid": 101, "user_id": "u1", "node_key": "web"},
        {"student_id": "s2", "vmid": None, "user_id": "u2", "node_key": "web"},
    ]
    monkeypatch.setattr(
        run_service, "resolve_class_machine_node", lambda *args: node
    )
    monkeypatch.setattr(
        run_service,
        "_class_member_by_node_key",
        lambda **kwargs: {"web": members},
    )

    def roster_reload(**kwargs: Any) -> dict[int, dict[str, Any]]:
        raise AssertionError("node runs must not reload the roster by VMID")

    monkeypatch.setattr(run_service, "_class_member_by_vmid", roster_reload)
    monkeypatch.setattr(run_service, "_running_resources_by_vmid", lambda: {})
    seen: list[dict[int, dict[str, Any]]] = []

    def fake_resolve_running_targets(**kwargs: Any) -> list[dict[str, Any]]:
        seen.append(kwargs["member_by_vmid"])
        return [{"vmid": kwargs["target_vmids"][0]}]

    monkeypatch.setattr(
        run_service, "_resolve_running_targets", fake_resolve_running_targets
    )

    targets, preflight = run_service._resolve_node_targets(
        session=None,  # type: ignore[arg-type]
        teaching_class_id=class_id,
        target_node_key="web",
    )
    assert targets == [{"vmid": 101}]
    assert seen == [{101: members[0]}]
    assert [result["reason_code"] for result in preflight] == ["missing_vmid"]
