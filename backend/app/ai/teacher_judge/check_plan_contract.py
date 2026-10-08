"""Shared typed write validation for proposals, persistence, and finalization."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

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
) -> list[dict[str, Any]]:
    """Apply the compiler's semantic rules as soon as an auto item is written."""
    issues = typed_step_issues(
        [step.model_dump(mode="json") for step in item.check_steps],
        item_id=item.id,
    )
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
