"""Rubric coverage validation for Teacher Judge managed script generation.

The generation model must return a ``coverage`` payload mapping every
``record_check`` id in the produced script to the rubric item ids it collects
evidence for. Validation here is purely mechanical: reference existence and
per-item completeness. Semantic relevance is re-checked later by the AI
reviewer and the result analysis stage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.ai.teacher_judge._types import CoverageMapping, CoverageResult
from app.ai.teacher_judge.script_quality_validator import collect_record_check_ids

if TYPE_CHECKING:
    from app.ai.teacher_judge._types import FixHint


def parse_coverage_payload(raw: Any) -> list[CoverageMapping] | None:
    """Return normalized coverage mappings, or None when the payload is invalid.

    An empty list is treated as invalid as well: a managed script for a
    non-empty rubric must map at least one check to at least one item.
    """
    if not isinstance(raw, list) or not raw:
        return None
    mappings: list[CoverageMapping] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        check_id = entry.get("check_id")
        item_ids = entry.get("rubric_item_ids")
        if (
            not isinstance(check_id, str)
            or not check_id.strip()
            or not isinstance(item_ids, list)
            or not item_ids
            or any(not isinstance(value, str) or not value.strip() for value in item_ids)
        ):
            return None
        mappings.append(
            {
                "check_id": check_id.strip(),
                "rubric_item_ids": list(
                    dict.fromkeys(value.strip() for value in item_ids)
                ),
            }
        )
    return mappings


def _rubric_index(
    rubric_items: list[dict[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    rubric_ids: set[str] = set()
    titles: dict[str, str] = {}
    for item in rubric_items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip()
        if not item_id:
            continue
        rubric_ids.add(item_id)
        titles[item_id] = str(item.get("title") or "").strip()
    return rubric_ids, titles


def _merge_mappings(mappings: list[CoverageMapping]) -> list[CoverageMapping]:
    merged: dict[str, CoverageMapping] = {}
    for mapping in mappings:
        existing = merged.get(mapping["check_id"])
        if existing is None:
            merged[mapping["check_id"]] = {
                "check_id": mapping["check_id"],
                "rubric_item_ids": list(mapping["rubric_item_ids"]),
            }
            continue
        existing["rubric_item_ids"] = list(
            dict.fromkeys(existing["rubric_item_ids"] + mapping["rubric_item_ids"])
        )
    return list(merged.values())


def validate_coverage(
    *,
    coverage: list[CoverageMapping],
    script_content: str,
    rubric_items: list[dict[str, Any]],
) -> CoverageResult:
    """Validate reference existence and per-item coverage completeness."""
    issues: list[str] = []
    fix_hints: list[FixHint] = []

    script_check_ids = collect_record_check_ids(script_content)
    rubric_ids, titles = _rubric_index(rubric_items)

    mappings = _merge_mappings(coverage)
    covered: set[str] = set()
    unknown_check_ids: set[str] = set()
    unknown_item_ids: set[str] = set()
    for mapping in mappings:
        if mapping["check_id"] not in script_check_ids:
            unknown_check_ids.add(mapping["check_id"])
            continue
        covered.update(
            item_id
            for item_id in mapping["rubric_item_ids"]
            if item_id in rubric_ids
        )
        unknown_item_ids.update(
            item_id
            for item_id in mapping["rubric_item_ids"]
            if item_id not in rubric_ids
        )

    if unknown_check_ids:
        issue = "coverage 引用不存在的 check id：" + ", ".join(sorted(unknown_check_ids))
        issues.append(issue)
        fix_hints.append({"type": "fix_coverage_refs", "description": issue})
    if unknown_item_ids:
        issue = "coverage 引用不存在的 rubric item id：" + ", ".join(
            sorted(unknown_item_ids)
        )
        issues.append(issue)
        fix_hints.append({"type": "fix_coverage_refs", "description": issue})

    uncovered = sorted(rubric_ids - covered)
    if uncovered:
        described = "、".join(
            f"{item_id}（{titles.get(item_id) or '未命名項目'}）"
            for item_id in uncovered
        )
        issue = f"以下檢查項目沒有任何 check 證據覆蓋：{described}"
        issues.append(issue)
        fix_hints.append({"type": "cover_rubric_items", "description": issue})

    return {
        "approved": not issues,
        "issues": issues,
        "fix_hints": fix_hints,
        "mappings": mappings,
        "uncovered_items": [
            {"id": item_id, "title": titles.get(item_id) or ""}
            for item_id in uncovered
        ],
    }


def realign_coverage_to_script(
    coverage: list[CoverageMapping],
    script_content: str,
) -> list[CoverageMapping]:
    """Drop mappings whose check_id no longer exists after a patch.

    Patches fix policy/quality/AI-review findings and may rename or remove
    collection blocks. Dropping stale mappings keeps valid ones; a rubric item
    that loses its last mapping is then reported as uncovered by
    :func:`validate_coverage`.
    """
    script_check_ids = collect_record_check_ids(script_content)
    return [
        {
            "check_id": mapping["check_id"],
            "rubric_item_ids": list(mapping["rubric_item_ids"]),
        }
        for mapping in coverage
        if mapping["check_id"] in script_check_ids
    ]


__all__ = [
    "parse_coverage_payload",
    "realign_coverage_to_script",
    "validate_coverage",
]
