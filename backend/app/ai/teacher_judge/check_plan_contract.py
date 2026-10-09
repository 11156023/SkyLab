"""Shared typed write validation for proposals, persistence, and finalization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from app.ai.teacher_judge.execution_paths import (
    absolute_execution_path,
    is_generated_location_gap,
    optional_cwd,
)
from app.ai.teacher_judge.schemas import (
    TeacherJudgeRubricAnalysis,
    TeacherJudgeRubricItem,
    TeacherJudgeTypedCheckStep,
)

TYPED_STEP_REPAIR_HINT = (
    "請保留原檢查目標，重新送出該項目的完整 check_steps 陣列，不能省略失敗步驟。"
    "collector 與 assertion 的類型欄位都是 type，預期值欄位是 expected；"
    "不要使用 collector_type、assertion_type、expected_value 或 flat argv。"
    '例如 {"id":"python.version","title":"Python 版本",'
    '"collector":{"type":"command","argv":["python3","--version"]},'
    '"assertion":{"type":"text_contains","expected":"3.12"}}。'
    "範例值不能取代老師的條件。teacher 模式省略 assertion；"
    "file_text 的 head/tail 必須提供 lines。缺少真實資訊時列出缺口，不得猜值。"
)


def typed_step_tool_schema() -> dict[str, Any]:
    """Inline Pydantic definitions for providers without local $ref support."""
    schema = TeacherJudgeTypedCheckStep.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(entry) for entry in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            name = value["$ref"].rsplit("/", 1)[-1]
            return inline(
                {
                    **deepcopy(definitions[name]),
                    **{k: v for k, v in value.items() if k != "$ref"},
                }
            )
        # The branches retain their literal `type` fields; provider schemas do
        # not need Pydantic's discriminator mapping with local references.
        return {
            key: inline(entry) for key, entry in value.items() if key != "discriminator"
        }

    return inline(schema)


def typed_step_issues(
    raw_steps: Any, *, item_id: str | None = None
) -> list[dict[str, Any]]:
    """Reject the complete write if any step is malformed; never drop steps."""
    if not isinstance(raw_steps, list):
        return [
            {
                "item_id": item_id,
                "field": "check_steps",
                "message": "check_steps 必須是陣列",
            }
        ]
    issues: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_steps):
        try:
            TeacherJudgeTypedCheckStep.model_validate(raw)
        except ValidationError as exc:
            for error in exc.errors(include_input=False, include_url=False):
                field = ".".join(str(part) for part in error["loc"])
                issues.append(
                    {
                        "item_id": item_id,
                        "step_id": str(raw.get("id") or "")
                        if isinstance(raw, dict)
                        else "",
                        "step_index": index,
                        "field": f"check_steps[{index}]"
                        + (f".{field}" if field else ""),
                        "message": str(error["msg"]),
                    }
                )
    return issues


def typed_item_issues(
    item: TeacherJudgeRubricItem,
    *,
    other_items: list[dict[str, Any]] | None = None,
    allow_legacy: bool = False,
) -> list[dict[str, Any]]:
    """Apply the compiler's semantic rules as soon as an auto item is written.

    ``allow_legacy`` is limited to the chat proposal compatibility boundary:
    older model adapters can still emit the read-compatible
    ``template_key/command_key/parameters`` shape, which is normalized by the
    chat service before staging. Persistence and finalization keep the default
    strict typed contract.
    """
    steps_for_validation = [
        step.model_dump(mode="json")
        for step in item.check_steps
        if not allow_legacy or step.collector is not None
    ]
    issues = typed_step_issues(steps_for_validation, item_id=item.id)
    occupied = {
        str(step.get("id") or "").strip()
        for other in (other_items or [])
        if other.get("id") != item.id
        and str(other.get("target_node_key") or "").strip() == str(item.target_node_key or "").strip()
        for step in (other.get("check_steps") or [])
        if isinstance(step, dict) and step.get("collector")
    }
    issues.extend(
        {
            "item_id": item.id,
            "step_id": step.id,
            "message": "同一 node 內 check step id 重複",
        }
        for step in item.check_steps
        if step.id and step.id.strip() in occupied
    )
    if issues or item.detectable != "auto":
        return issues
    if allow_legacy and any(step.collector is None for step in item.check_steps):
        # Legacy chat candidates are normalized against the command catalog and
        # remain read-compatible until the caller replaces them with typed steps.
        return issues
    from app.ai.teacher_judge.deterministic_compiler import (
        CheckPlanContractError,
        canonicalize_check_plan,
    )

    try:
        canonicalize_check_plan(
            TeacherJudgeRubricAnalysis(items=[item]), require_target_node=False
        )
    except CheckPlanContractError as exc:
        return exc.issues
    return []


def reconcile_finalizer_locations(
    current: dict[str, Any], candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep known locations when reviewing the same item/node/step/input.

    This is only for Save/Create review, never an ordinary teacher edit. A
    missing optional field in a regenerated step must not erase known execution
    information. Explicit new absolute locations remain intentional changes.
    """
    from app.ai.teacher_judge.deterministic_compiler import missing_execution_location

    try:
        previous = TeacherJudgeRubricItem.model_validate(current)
    except ValidationError:
        return candidate, []
    if previous.detectable != "auto" or typed_item_issues(previous):
        return candidate, []
    if any(current.get(key) != candidate.get(key) for key in ("id", "target_node_key", "peer_node_key")):
        return candidate, []

    result = deepcopy(candidate)
    prior_steps = {step.id: step for step in previous.check_steps}
    issues: list[dict[str, Any]] = []
    for index, step in enumerate(result.get("check_steps") or []):
        old_step = prior_steps.get(step.get("id"))
        collector = step.get("collector")
        if old_step is None or old_step.collector is None or not isinstance(collector, dict):
            continue
        old = old_step.collector.model_dump(mode="json")
        kind = collector.get("type")
        input_key = "argv" if kind == "command" else "path"
        if kind not in {"command", "file_text", "file_stat"} or (
            kind != old.get("type") or collector.get(input_key) != old.get(input_key)
        ):
            continue
        if optional_cwd(collector.get("cwd")) is None:
            collector["cwd"] = old.get("cwd")
        elif not absolute_execution_path(collector["cwd"]):
            issues.append({
                "item_id": previous.id, "step_id": step.get("id"), "step_index": index,
                "field": f"check_steps[{index}].collector.cwd",
                "message": "核對修改把既有執行位置改成無效值；請保留目前項目的 collector.cwd，不需要老師補資料。",
            })

    steps = result.get("check_steps") or []
    if steps and all(
        isinstance(step.get("collector"), dict)
        and not missing_execution_location(step["collector"])
        for step in steps
    ):
        claimed = result.get("missing_information") or []
        if not isinstance(claimed, list):
            return result, issues
        remaining = [gap for gap in claimed if not isinstance(gap, str) or not is_generated_location_gap(gap)]
        if remaining != claimed:
            result["missing_information"] = remaining
            if result.get("detectable") == "partial" and not remaining:
                result["detectable"] = "auto"
    return result, issues


def analysis_write_issues(
    analysis: TeacherJudgeRubricAnalysis,
    previous: Any,
) -> list[dict[str, Any]]:
    """Allow unchanged legacy plans, while validating every new/changed plan."""
    previous_items = previous.get("items", []) if isinstance(previous, dict) else []
    previous_items = previous_items if isinstance(previous_items, list) else []
    previous_by_id = {}
    for raw in previous_items:
        try:
            old_item = TeacherJudgeRubricItem.model_validate(raw)
        except ValidationError:
            continue
        previous_by_id[old_item.id] = old_item
    issues: list[dict[str, Any]] = []
    seen_items: set[str] = set()
    seen_steps: set[tuple[str | None, str]] = set()
    plan_fields = (
        "check_steps",
        "detectable",
        "judgement_mode",
        "target_node_key",
        "peer_node_key",
    )
    for item in analysis.items:
        if item.id in seen_items:
            issues.append({"item_id": item.id, "message": "檢查項目 id 重複"})
        seen_items.add(item.id)
        old = previous_by_id.get(item.id)
        legacy = any(step.collector is None for step in item.check_steps)
        if (
            legacy
            and old
            and all(
                getattr(item, field) == getattr(old, field) for field in plan_fields
            )
        ):
            continue
        issues.extend(typed_item_issues(item))
        for step in item.check_steps:
            if step.collector is None or not step.id:
                continue
            key = (str(item.target_node_key or "").strip(), step.id.strip())
            if key in seen_steps:
                issues.append(
                    {
                        "item_id": item.id,
                        "step_id": step.id,
                        "message": "同一 node 內 check step id 重複",
                    }
                )
            seen_steps.add(key)
    return issues
