"""Teacher Judge service cleanup: shared helpers that replaced duplicated inline logic."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.teacher_judge import machine_context, session_service, target_ip_resolver
from app.ai.teacher_judge import service as teacher_judge_service
from app.ai.teacher_judge.script_policy import PEER_IP_TOKEN
from app.models.teacher_judge_file import TeacherJudgeFileStatus
from app.models.teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeMessageType,
)

BACKEND_DIR = Path(__file__).resolve().parents[3]


def test_package_init_does_not_eagerly_load_service() -> None:
    code = (
        "import sys\n"
        "import app.ai.teacher_judge.config\n"
        "assert 'app.ai.teacher_judge.service' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_peer_ip_token_has_one_definition() -> None:
    assert machine_context.PEER_IP_TOKEN is PEER_IP_TOKEN
    assert teacher_judge_service.PEER_IP_TOKEN is PEER_IP_TOKEN


def test_proposal_status_claims_ready_reads_only_structured_status() -> None:
    claims = teacher_judge_service._proposal_status_claims_ready
    assert claims("ready") is True
    assert claims(" READY ") is True
    assert claims("needs_information") is False
    assert claims(None) is False


def test_raw_items_by_id_matches_normalizer_fallback_ids() -> None:
    raw = [
        {"id": "a", "detectable": "Auto "},
        "junk",
        {"detectable": "manual"},
    ]
    by_id = teacher_judge_service._raw_items_by_id(raw)
    assert set(by_id) == {"a", "item-3"}
    assert teacher_judge_service._raw_detectable(by_id["a"]) == "auto"
    assert teacher_judge_service._raw_detectable(by_id.get("missing")) == ""
    assert teacher_judge_service._raw_items_by_id("not-a-list") == {}


@pytest.mark.parametrize(
    ("raw", "expected_operation"),
    [
        ({"item": {"id": "x"}, "operation": "Delete"}, "delete"),
        ({"item": {"id": "x"}, "action": "UPDATE"}, "update"),
        ({"id": "x", "op": "create"}, "create"),
        ({"id": "x"}, ""),
    ],
)
def test_proposal_candidate_unwraps_item_and_operation_aliases(
    raw: dict[str, Any], expected_operation: str
) -> None:
    candidate, operation = session_service._proposal_candidate(raw)
    assert candidate["id"] == "x"
    assert operation == expected_operation
    candidate["id"] = "mutated"
    nested = raw.get("item")
    assert (nested or raw)["id"] == "x"


def test_is_active_class_file() -> None:
    class_id = uuid.uuid4()
    active = SimpleNamespace(
        teaching_class_id=class_id, status=TeacherJudgeFileStatus.active
    )
    check = session_service._is_active_class_file
    assert check(active, class_id) is True  # type: ignore[arg-type]
    assert check(None, class_id) is False
    assert check(active, uuid.uuid4()) is False  # type: ignore[arg-type]
    archived = SimpleNamespace(
        teaching_class_id=class_id,
        status=next(
            status
            for status in TeacherJudgeFileStatus
            if status != TeacherJudgeFileStatus.active
        ),
    )
    assert check(archived, class_id) is False  # type: ignore[arg-type]


def test_is_assistant_boundary() -> None:
    session_id = uuid.uuid4()

    def message(**overrides: Any) -> Any:
        values: dict[str, Any] = {
            "session_id": session_id,
            "role": TeacherJudgeMessageRole.assistant,
            "message_type": next(
                kind
                for kind in TeacherJudgeMessageType
                if kind != TeacherJudgeMessageType.system_notice
            ),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    check = session_service._is_assistant_boundary
    assert check(message(), session_id) is True
    assert check(None, session_id) is False
    assert check(message(session_id=uuid.uuid4()), session_id) is False
    assert check(message(role=TeacherJudgeMessageRole.user), session_id) is False
    assert (
        check(
            message(message_type=TeacherJudgeMessageType.system_notice), session_id
        )
        is False
    )


def test_message_context_appends_attachments_only_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_service, "attachment_context", lambda rows: "FULL")
    monkeypatch.setattr(
        session_service, "attachment_compact_context", lambda rows: "COMPACT"
    )
    row: Any = SimpleNamespace(content="hello")
    attachment: Any = object()
    build = session_service._message_context
    assert build(row, attachments=[]) == "hello"
    assert build(row, attachments=[attachment], include_attachments=False) == "hello"
    assert build(row, attachments=[attachment]) == "hello\n\nFULL"
    assert (
        build(row, attachments=[attachment], compact_attachments=True)
        == "hello\n\nCOMPACT"
    )


def test_target_ip_resolver_writes_live_ip_back_through_sync_ip_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[int, str]] = []
    rollbacks: list[bool] = []

    monkeypatch.setattr(
        target_ip_resolver.resource_repo,
        "get_cached_ip_address",
        lambda **_: None,
    )

    def failing_update(*, session: Any, vmid: int, ip_address: str) -> None:
        writes.append((vmid, ip_address))
        raise RuntimeError("db down")

    monkeypatch.setattr(
        target_ip_resolver.resource_repo, "update_ip_address", failing_update
    )
    monkeypatch.setattr(
        target_ip_resolver.proxmox_ops,
        "get_ip_address",
        lambda node, vmid, resource_type: " 10.0.0.5 ",
    )
    fake_session: Any = SimpleNamespace(rollback=lambda: rollbacks.append(True))

    ip = target_ip_resolver.resolve_target_ip_address(
        session=fake_session,
        vmid=101,
        live_resource={"node": "pve1", "type": "qemu"},
    )

    assert ip == "10.0.0.5"
    assert writes == [(101, "10.0.0.5")]
    assert rollbacks == [True]
