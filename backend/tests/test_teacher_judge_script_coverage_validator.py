from __future__ import annotations

from app.ai.teacher_judge.script_coverage_validator import (
    parse_coverage_payload,
    realign_coverage_to_script,
    validate_coverage,
)

COLLECTION_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

checks = [
    record_check("runtime.python_version", "收集 Python 版本", "unknown", "n/a"),
    record_check("service.port", "收集服務連接埠", "unknown", "n/a"),
]
"""

VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
checks = [
    record_check(check_id=check_id, title="Python 版本", status="unknown", evidence="n/a"),
]
"""

SEQUENTIAL_VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
checks = [record_check(check_id, "Python 版本", "unknown", "n/a")]
check_id = "python.pip_list"
checks.append(record_check(check_id, "必要套件", "unknown", "n/a"))
check_id = "python.run_entrypoint"
checks.append(record_check(check_id, "執行入口", "unknown", "n/a"))
"""

AMBIGUOUS_VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
check_id = get_check_id()
checks = [record_check(check_id, "Python 版本", "unknown", "n/a")]
"""

BRANCHED_VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
if should_use_other_id:
    check_id = "python.pip_list"
checks = [record_check(check_id, "檢查", "unknown", "n/a")]
"""

LOOP_VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
for check_id in requested_ids:
    checks = [record_check(check_id, "檢查", "unknown", "n/a")]
"""

COMPREHENSION_VARIABLE_ID_SCRIPT = """
def record_check(check_id, title, status, evidence, raw=""):
    return {"id": check_id}

check_id = "python.version"
checks = [
    record_check(check_id, "檢查", "unknown", "n/a")
    for check_id in requested_ids
]
"""

RUBRIC_ITEMS = [
    {"id": "item-1", "title": "程式正常結束"},
    {"id": "item-2", "title": "輸出整數 20"},
]


def test_validate_coverage_approves_complete_mapping() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]},
            {"check_id": "service.port", "rubric_item_ids": ["item-2"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=RUBRIC_ITEMS,
    )

    assert result["approved"] is True
    assert result["issues"] == []
    assert result["uncovered_items"] == []
    assert result["available_check_ids"] == ["runtime.python_version", "service.port"]


def test_validate_coverage_accepts_literal_constant_ids_and_keyword_arguments() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "python.version", "rubric_item_ids": ["item-1"]},
        ],
        script_content=VARIABLE_ID_SCRIPT,
        rubric_items=[{"id": "item-1", "title": "Python 版本"}],
    )

    assert result["approved"] is True
    assert result["available_check_ids"] == ["python.version"]


def test_validate_coverage_resolves_sequential_constant_ids() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "python.version", "rubric_item_ids": ["item-1"]},
            {"check_id": "python.pip_list", "rubric_item_ids": ["item-2"]},
            {"check_id": "python.run_entrypoint", "rubric_item_ids": ["item-3"]},
        ],
        script_content=SEQUENTIAL_VARIABLE_ID_SCRIPT,
        rubric_items=[
            {"id": "item-1", "title": "Python 版本"},
            {"id": "item-2", "title": "必要套件"},
            {"id": "item-3", "title": "執行入口"},
        ],
    )

    assert result["approved"] is True
    assert result["available_check_ids"] == [
        "python.pip_list",
        "python.run_entrypoint",
        "python.version",
    ]


def test_validate_coverage_does_not_resolve_dynamic_or_ambiguous_ids() -> None:
    for script_content in (
        AMBIGUOUS_VARIABLE_ID_SCRIPT,
        BRANCHED_VARIABLE_ID_SCRIPT,
        LOOP_VARIABLE_ID_SCRIPT,
        COMPREHENSION_VARIABLE_ID_SCRIPT,
    ):
        result = validate_coverage(
            coverage=[
                {"check_id": "python.version", "rubric_item_ids": ["item-1"]},
            ],
            script_content=script_content,
            rubric_items=[{"id": "item-1", "title": "Python 版本"}],
        )

        assert result["approved"] is False
        assert "python.version" not in result["available_check_ids"]
        assert any("python.version" in issue for issue in result["issues"])


def test_validate_coverage_merges_duplicate_check_entries() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]},
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-2"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=RUBRIC_ITEMS,
    )

    assert result["approved"] is True
    assert result["mappings"] == [
        {
            "check_id": "runtime.python_version",
            "rubric_item_ids": ["item-1", "item-2"],
        }
    ]


def test_validate_coverage_reports_uncovered_items_with_title() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=RUBRIC_ITEMS,
    )

    assert result["approved"] is False
    assert any("item-2" in issue for issue in result["issues"])
    assert any("輸出整數 20" in issue for issue in result["issues"])
    assert result["uncovered_items"] == [{"id": "item-2", "title": "輸出整數 20"}]


def test_validate_coverage_rejects_unknown_check_refs() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "ghost.check", "rubric_item_ids": ["item-1"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=RUBRIC_ITEMS,
    )

    assert result["approved"] is False
    assert any("ghost.check" in issue for issue in result["issues"])
    assert result["uncovered_items"] == [
        {"id": "item-1", "title": "程式正常結束"},
        {"id": "item-2", "title": "輸出整數 20"},
    ]


def test_validate_coverage_rejects_unknown_rubric_refs() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-99"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=RUBRIC_ITEMS,
    )

    assert result["approved"] is False
    assert any("item-99" in issue for issue in result["issues"])


def test_validate_coverage_ignores_unmapped_extra_script_checks() -> None:
    result = validate_coverage(
        coverage=[
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]},
        ],
        script_content=COLLECTION_SCRIPT,
        rubric_items=[{"id": "item-1", "title": "程式正常結束"}],
    )

    assert result["approved"] is True
    assert result["issues"] == []


def test_parse_coverage_payload_normalizes_valid_entries() -> None:
    parsed = parse_coverage_payload(
        [
            {"check_id": " runtime.python_version ", "rubric_item_ids": ["item-1", "item-1", "item-2"]},
        ]
    )

    assert parsed == [
        {
            "check_id": "runtime.python_version",
            "rubric_item_ids": ["item-1", "item-2"],
        }
    ]


def test_parse_coverage_payload_rejects_invalid_shapes() -> None:
    assert parse_coverage_payload(None) is None
    assert parse_coverage_payload([]) is None
    assert parse_coverage_payload("not-a-list") is None
    assert parse_coverage_payload([{"rubric_item_ids": ["item-1"]}]) is None
    assert parse_coverage_payload([{"check_id": "", "rubric_item_ids": ["item-1"]}]) is None
    assert parse_coverage_payload([{"check_id": "a", "rubric_item_ids": []}]) is None
    assert parse_coverage_payload([{"check_id": "a", "rubric_item_ids": [""]}]) is None
    assert parse_coverage_payload(["not-a-dict"]) is None


def test_realign_coverage_to_script_drops_stale_check_ids() -> None:
    realigned = realign_coverage_to_script(
        [
            {"check_id": "runtime.python_version", "rubric_item_ids": ["item-1"]},
            {"check_id": "removed.check", "rubric_item_ids": ["item-2"]},
        ],
        COLLECTION_SCRIPT,
    )

    assert realigned == [
        {
            "check_id": "runtime.python_version",
            "rubric_item_ids": ["item-1"],
        }
    ]
