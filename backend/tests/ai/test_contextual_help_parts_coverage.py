"""Coverage tests for app.ai.contextual_help resolver, surfaces, intent and guide."""

from app.ai.contextual_help import surfaces
from app.ai.contextual_help.guide import render_dialog_index
from app.ai.contextual_help.intent import classify
from app.ai.contextual_help.resolver import resolve_context
from app.ai.contextual_help.schemas import (
    DialogSpec,
    ElementSpec,
    ElementState,
    SurfaceSpec,
)

_SURFACE = SurfaceSpec(
    id="demo",
    path="/demo",
    title="示範頁",
    purpose="示範用途。",
    elements=(
        ElementSpec(id="a", role="text", label="名稱", section="基本"),
        ElementSpec(id="ab", role="text", label="連線名稱"),
        # 前後有空白的 id：blocked_elements 認得，find_element 會 strip 後找不到
        ElementSpec(id=" pad ", role="text", label="怪欄位"),
    ),
)


def test_field_help_with_section_and_disabled_reason() -> None:
    context, grounded, level = resolve_context(
        _SURFACE,
        "field_help",
        active_target="a",
        state={"a": ElementState(disabled=True, disabled_reason="尚未選擇")},
    )
    assert level == 0
    assert context["target"]["section"] == "基本"
    assert context["target"]["disabled_reason"] == "尚未選擇"
    assert "a.disabled_reason" in grounded


def test_field_help_with_unknown_target_falls_back_to_overview() -> None:
    context, _grounded, level = resolve_context(
        _SURFACE, "field_help", active_target="missing", state={}
    )
    assert level == 2
    assert "target" not in context


def test_validation_help_skips_unresolvable_blocked_element() -> None:
    context, _grounded, _level = resolve_context(
        _SURFACE,
        "validation_help",
        active_target=None,
        state={
            " pad ": ElementState(error="壞了"),
            "a": ElementState(error="必填"),
        },
    )
    assert [item["id"] for item in context["blocked"]] == ["a"]


def test_with_guide_keeps_surface_without_guide() -> None:
    surface = SurfaceSpec(id="no-guide-x", path="/x", title="X", purpose="p")
    assert surfaces._with_guide(surface) is surface


def test_find_helpers_handle_blank_input() -> None:
    assert surfaces.find_surface("  ", surfaces.all_surfaces()) is None
    assert surfaces.find_element(_SURFACE, " ") is None
    assert surfaces.match_element_by_label(_SURFACE, "") is None
    assert surfaces.match_element_by_label(_SURFACE, "無關") is None


def test_classify_edge_cases() -> None:
    assert classify("   ", has_active_target=True, has_blocked=True) == "page_overview"
    assert (
        classify("嗯嗯", has_active_target=False, has_blocked=False) == "page_overview"
    )


def test_render_dialog_index_with_single_dialog() -> None:
    dialog = DialogSpec(id="d", title="新增", opened_by="新增", purpose="建立東西")
    text = render_dialog_index(_SURFACE, (dialog,))
    assert text.startswith("**新增**（按「新增」開啟）")
