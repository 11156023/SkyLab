"""整理：從 create_message 路由抽出來的 Teacher Judge 對話步驟。"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai.teacher_judge import session_chat_service as chat


def test_legacy_command_context_detects_template_steps() -> None:
    assert chat.uses_legacy_command_context(None) is False
    assert chat.uses_legacy_command_context({"items": []}) is False
    assert (
        chat.uses_legacy_command_context(
            {"items": [{"check_steps": [{"kind": "command"}]}, "junk"]}
        )
        is False
    )
    assert (
        chat.uses_legacy_command_context(
            {"items": [{"check_steps": [{"command_key": "ping"}]}]}
        )
        is True
    )


def test_prompt_template_scope_only_uses_file_scope_for_legacy_context() -> None:
    file = SimpleNamespace(template_key="windows", environment_keys=["lab"])
    assert chat.prompt_template_scope(None, True) == ("linux", None)
    assert chat.prompt_template_scope(file, False) == ("linux", None)
    assert chat.prompt_template_scope(file, True) == ("windows", ["lab"])


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "resolved"),
        (["resolved", "resolved"], "resolved"),
        (["resolved", "unsupported"], "unsupported"),
        (["unsupported", "needs_information"], "needs_information"),
        (["needs_information", "analysis_error", "unsupported"], "analysis_error"),
    ],
)
def test_attachment_analysis_status_priority(statuses, expected) -> None:
    rows = [{"status": status} for status in statuses]
    assert chat.attachment_analysis_status(rows) == expected


def _nodes(monkeypatch, *keys: str) -> None:
    monkeypatch.setattr(
        chat,
        "load_class_machine_nodes",
        lambda *_args: [SimpleNamespace(node_key=key) for key in keys],
    )


def test_proposal_nodes_must_belong_to_class(monkeypatch) -> None:
    _nodes(monkeypatch, "web")
    monkeypatch.setattr(chat, "rubric_item_machine_issues", lambda _item: [])
    with pytest.raises(HTTPException) as exc:
        chat.validate_proposal_machine_nodes(
            None,  # type: ignore[arg-type]
            uuid.uuid4(),
            [{"item": {"id": "a", "target_node_key": "db", "peer_node_key": "x"}}],
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "teacher_judge_target_node_not_in_class"
    assert exc.value.detail["target_node_keys"] == ["db", "x"]
    assert exc.value.detail["message"] == "提案中的 target_node_key 不屬於目前班級。"


def test_auto_items_need_a_target_node(monkeypatch) -> None:
    _nodes(monkeypatch, "web")
    monkeypatch.setattr(chat, "rubric_item_machine_issues", lambda _item: [])
    with pytest.raises(HTTPException) as exc:
        chat.validate_proposal_machine_nodes(
            None,  # type: ignore[arg-type]
            uuid.uuid4(),
            [{"title": "開機", "detectable": "AUTO"}, {"id": "b", "detectable": "auto"}],
        )
    assert exc.value.detail["code"] == "teacher_judge_target_node_required"
    assert exc.value.detail["item_ids"] == ["開機", "b"]
    assert exc.value.detail["message"] == "可執行的提案項目必須指定 target_node_key。"


def test_valid_proposal_passes(monkeypatch) -> None:
    _nodes(monkeypatch, "web")
    monkeypatch.setattr(chat, "rubric_item_machine_issues", lambda _item: [])
    chat.validate_proposal_machine_nodes(
        None,  # type: ignore[arg-type]
        uuid.uuid4(),
        [{"id": "a", "detectable": "auto", "target_node_key": "web"}, "junk"],
    )


def test_metadata_for_itemwise_failure_replaces_results() -> None:
    file_id = uuid.uuid4()
    metadata = chat.build_assistant_metadata(
        metrics={"latency": 1},
        workflow=None,
        item_results=[{"item_id": "a", "status": "resolved"}],
        itemwise_error="boom",
        conversation_focus=None,
        tool_calls=None,
        source_file_id=file_id,
        analysis_revision=3,
    )
    assert metadata["metrics"] == {"latency": 1}
    assert metadata["status"] == "analysis_error"
    assert metadata["stage"] == "attachment_analysis"
    assert metadata["source_file_id"] == str(file_id)
    assert metadata["analysis_revision"] == 3
    assert [row["item_id"] for row in metadata["item_results"]] == [
        "attachment-analysis"
    ]
    assert "tool_calls" not in metadata


def test_metadata_for_plain_chat_keeps_focus_and_tool_calls() -> None:
    metadata = chat.build_assistant_metadata(
        metrics={},
        workflow=None,
        item_results=None,
        itemwise_error=None,
        conversation_focus={"item_id": "a"},
        tool_calls=[{"name": "lookup"}],
        source_file_id=None,
        analysis_revision=None,
    )
    assert metadata == {
        "metrics": {},
        "conversation_focus": {
            "item_id": "a",
            "source_file_id": None,
            "analysis_revision": None,
        },
        "tool_calls": [{"name": "lookup"}],
    }


def test_metadata_prefers_workflow_result() -> None:
    workflow = {"content": "ok", "metadata": {"status": "ready", "stage": "refine"}}
    metadata = chat.build_assistant_metadata(
        metrics={},
        workflow=workflow,  # type: ignore[arg-type]
        item_results=[{"status": "analysis_error"}],
        itemwise_error=None,
        conversation_focus={"item_id": "a"},
        tool_calls=None,
        source_file_id=None,
        analysis_revision=None,
    )
    assert metadata == {"metrics": {}, "status": "ready", "stage": "refine"}
