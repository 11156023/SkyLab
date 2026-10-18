"""Validator coverage for app.ai.teacher_judge.schemas."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.ai.teacher_judge.schemas import (
    TeacherJudgeCommandCollector,
    TeacherJudgeFileTextCollector,
    TeacherJudgeLocalhostHttpCollector,
    TeacherJudgeRubricCheckStep,
    TeacherJudgeScriptRunCreateRequest,
    TeacherJudgeScriptUpdateRequest,
    TeacherJudgeSessionCreateRequest,
    TeacherJudgeSessionForkRequest,
    TeacherJudgeSessionUpdateRequest,
    TeacherJudgeTargetReviewUpdate,
    TeacherJudgeTypedCheckStep,
    sanitize_rubric_missing_information,
)

WEEK = uuid.uuid4()


def test_sanitize_missing_information_passes_non_lists_through() -> None:
    assert sanitize_rubric_missing_information("text") == "text"
    assert sanitize_rubric_missing_information(["成功條件 X", "路徑", 3]) == [
        "路徑",
        3,
    ]


def test_command_collector_rejects_blank_argv() -> None:
    with pytest.raises(ValidationError):
        TeacherJudgeCommandCollector(type="command", argv=["ls", " "])


def test_file_text_collector_line_rules() -> None:
    with pytest.raises(ValidationError, match="head/tail"):
        TeacherJudgeFileTextCollector(type="file_text", path="a", read_mode="head")
    with pytest.raises(ValidationError, match="full"):
        TeacherJudgeFileTextCollector(type="file_text", path="a", lines=3)
    ok = TeacherJudgeFileTextCollector(
        type="file_text", path="a", read_mode="tail", lines=3, cwd=" "
    )
    assert ok.cwd is None


def test_localhost_http_collector_rejects_external_url() -> None:
    with pytest.raises(ValidationError):
        TeacherJudgeLocalhostHttpCollector(
            type="localhost_http", url="http://example.com"
        )
    assert (
        TeacherJudgeLocalhostHttpCollector(
            type="localhost_http", url="http://localhost:8080/x"
        ).url
        == "http://localhost:8080/x"
    )


def test_typed_check_step_rejects_blank_identity() -> None:
    with pytest.raises(ValidationError):
        TeacherJudgeTypedCheckStep(id=" ", title="t", collector={"type": "peer_ping"})


def test_rubric_check_step_rejects_non_dict_input() -> None:
    with pytest.raises(ValidationError):
        TeacherJudgeRubricCheckStep.model_validate("not-a-step")


def test_rubric_check_step_typed_contract_errors() -> None:
    with pytest.raises(ValidationError, match="unknown fields"):
        TeacherJudgeRubricCheckStep.model_validate(
            {"id": "a", "title": "t", "collector": {"type": "peer_ping"}, "x": 1}
        )
    with pytest.raises(ValidationError, match="requires id"):
        TeacherJudgeRubricCheckStep.model_validate(
            {"title": "t", "collector": {"type": "peer_ping"}}
        )
    with pytest.raises(ValidationError, match="requires title"):
        TeacherJudgeRubricCheckStep.model_validate(
            {"id": "a", "collector": {"type": "peer_ping"}}
        )
    with pytest.raises(ValidationError, match="legacy command fields"):
        TeacherJudgeRubricCheckStep.model_validate(
            {
                "id": "a",
                "title": "t",
                "collector": {"type": "peer_ping"},
                "argv": ["ls"],
            }
        )


def test_rubric_check_step_flat_contract() -> None:
    with pytest.raises(ValidationError, match="requires argv"):
        TeacherJudgeRubricCheckStep.model_validate({"id": "a"})
    with pytest.raises(ValidationError, match="非空字串"):
        TeacherJudgeRubricCheckStep.model_validate({"argv": ["ls", " "]})
    step = TeacherJudgeRubricCheckStep.model_validate(
        {"argv": ["ls"], "cwd": "/srv", "timeout_seconds": 5}
    )
    assert step.model_dump() == {"argv": ["ls"], "cwd": "/srv", "timeout_seconds": 5}


def test_session_create_request_validators() -> None:
    # 一週一個對話：沒有週次就不能建立
    with pytest.raises(ValidationError):
        TeacherJudgeSessionCreateRequest(title="x")
    with pytest.raises(ValidationError):
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="  ")
    assert (
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="x", rubric_name=None).rubric_name
        is None
    )
    with pytest.raises(ValidationError):
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="x", rubric_name="   ")
    assert (
        TeacherJudgeSessionCreateRequest(
            teaching_class_week_id=WEEK, title="x", environment_keys=None
        ).environment_keys
        is None
    )
    assert TeacherJudgeSessionCreateRequest(
        teaching_class_week_id=WEEK, title="x", environment_keys=[" Linux ", "linux", " "]
    ).environment_keys == ["linux"]
    with pytest.raises(ValidationError):
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="x", environment_keys=["windows"])


def test_session_create_request_creation_mode_rules() -> None:
    file_id = uuid.uuid4()
    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(
            teaching_class_week_id=WEEK, title="x",
            creation_mode="blank",
            selected_file_id=file_id,
            rubric_name="r",
        )
    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="x", creation_mode="blank")
    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(teaching_class_week_id=WEEK, title="x", creation_mode="existing")
    with pytest.raises(ValueError):
        TeacherJudgeSessionCreateRequest(
            teaching_class_week_id=WEEK, title="x",
            creation_mode="existing",
            selected_file_id=file_id,
            rubric_name="r",
        )
    ok = TeacherJudgeSessionCreateRequest(
        teaching_class_week_id=WEEK, title="x", creation_mode="existing", selected_file_id=file_id
    )
    assert ok.selected_file_id == file_id


def test_session_update_request_title() -> None:
    assert TeacherJudgeSessionUpdateRequest(title=None).title is None
    assert TeacherJudgeSessionUpdateRequest(title=" t ").title == "t"
    with pytest.raises(ValidationError):
        TeacherJudgeSessionUpdateRequest(title="   ")


def test_script_update_request_name() -> None:
    assert TeacherJudgeScriptUpdateRequest(name=" n ").name == "n"
    with pytest.raises(ValidationError):
        TeacherJudgeScriptUpdateRequest(name="   ")


def test_fork_request_title() -> None:
    assert TeacherJudgeSessionForkRequest(title=None, teaching_class_week_id=WEEK).title is None
    assert TeacherJudgeSessionForkRequest(title=" f ", teaching_class_week_id=WEEK).title == "f"
    with pytest.raises(ValidationError):
        TeacherJudgeSessionForkRequest(title="   ", teaching_class_week_id=WEEK)
    # 複製一定要指定目標週次
    with pytest.raises(ValidationError):
        TeacherJudgeSessionForkRequest(title="f")


def test_script_run_create_request_selectors() -> None:
    manual = TeacherJudgeScriptRunCreateRequest(target_vmids=[101, 101, 102])
    assert manual.target_vmids == [101, 102]
    with pytest.raises(ValidationError):
        TeacherJudgeScriptRunCreateRequest()

    node = TeacherJudgeScriptRunCreateRequest(
        target_scope="all_students_on_node", target_node_key=" web "
    )
    assert node.target_node_key == "web"
    with pytest.raises(ValidationError):
        TeacherJudgeScriptRunCreateRequest(
            target_scope="all_students_on_node", target_node_key="  "
        )
    with pytest.raises(ValidationError):
        TeacherJudgeScriptRunCreateRequest(
            target_scope="all_students_on_node",
            target_node_key="web",
            target_vmids=[1],
        )

    legacy = TeacherJudgeScriptRunCreateRequest(
        target_scope="all_with_vm", target_node_key=None, target_vmids=[5]
    )
    assert legacy.target_node_key is None
    with pytest.raises(ValidationError):
        TeacherJudgeScriptRunCreateRequest(target_scope="running_only")
    with pytest.raises(ValidationError):
        TeacherJudgeScriptRunCreateRequest(
            target_scope="running_only", target_node_key="web", target_vmids=[5]
        )


def test_target_review_update_decisions() -> None:
    review = TeacherJudgeTargetReviewUpdate(
        feedback="  ok  ", decisions={" c1 ": "pass"}
    )
    assert review.feedback == "ok"
    assert review.decisions == {"c1": "pass"}
    with pytest.raises(ValidationError, match="more than 100"):
        TeacherJudgeTargetReviewUpdate(
            decisions={f"c{index}": "pass" for index in range(101)}
        )
    with pytest.raises(ValidationError, match="1 to 255"):
        TeacherJudgeTargetReviewUpdate(decisions={"  ": "fail"})
