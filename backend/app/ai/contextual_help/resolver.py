"""把前端送來的畫面狀態併進伺服器端定義，並依 intent 只留下必要的部分。

兩件事在這裡發生：

**合併。** 靜態的一半（label、help、constraints）永遠以伺服器端為準，前端送來的
同名欄位一律忽略——那些描述會左右模型怎麼回答，不能讓呼叫端決定。動態的一半
（value、error、disabled）只有瀏覽器知道，只能由前端送，但只接受這個 surface
已經宣告過的 element id，其餘丟棄。

**減量。** 情境不是愈多愈好：多餘的欄位會稀釋重點，也讓模型更容易講到不相干的
東西。所以每種 intent 只撈自己需要的層級。
"""

from __future__ import annotations

from typing import Any

from app.ai.contextual_help.schemas import (
    ElementState,
    HelpIntent,
    SurfaceSpec,
)
from app.ai.contextual_help.surfaces import find_element

# 情境層級，對應「撈到多遠」：0 只有目標本身，1 加上相關的錯誤，2 整個 surface 概觀。
LEVEL_TARGET = 0
LEVEL_RELATED = 1
LEVEL_SURFACE = 2


def _element_context(
    surface: SurfaceSpec, element_id: str, state: ElementState | None
) -> dict[str, Any] | None:
    spec = find_element(surface, element_id)
    if spec is None:
        return None

    context: dict[str, Any] = {
        "id": spec.id,
        "role": spec.role,
        "label": spec.label,
    }
    if spec.section:
        context["section"] = spec.section
    if spec.help:
        context["help"] = spec.help
    if spec.constraints:
        context["constraints"] = list(spec.constraints)

    if state is not None:
        # 敏感欄位只說有沒有填，不把內容送進 prompt。
        if state.value is not None:
            if spec.sensitive:
                context["value_present"] = bool(state.value)
            else:
                context["value"] = state.value
        if state.error:
            context["error"] = state.error
        if state.disabled is not None:
            context["disabled"] = state.disabled
        if state.disabled_reason:
            context["disabled_reason"] = state.disabled_reason
    return context


def sanitize_state(
    surface: SurfaceSpec, state: dict[str, ElementState]
) -> dict[str, ElementState]:
    """只留下這個 surface 宣告過的 element；未宣告的一律丟棄。"""
    return {
        element_id: element_state
        for element_id, element_state in state.items()
        if find_element(surface, element_id) is not None
    }


def blocked_elements(
    surface: SurfaceSpec, state: dict[str, ElementState]
) -> list[str]:
    """目前有驗證錯誤或停用原因的 element，順序照 surface 的宣告順序。"""
    blocked = []
    for spec in surface.elements:
        element_state = state.get(spec.id)
        if element_state is None:
            continue
        if element_state.error or element_state.disabled_reason:
            blocked.append(spec.id)
    return blocked


def resolve_context(
    surface: SurfaceSpec,
    intent: HelpIntent,
    *,
    active_target: str | None,
    state: dict[str, ElementState],
) -> tuple[dict[str, Any], list[str], int]:
    """回傳 (要送進 prompt 的情境, grounded_in, context_level)。

    ``grounded_in`` 列出這次答案可以引用的情境欄位，讓回答能被追溯，也讓回歸
    測試有東西可以比對。
    """
    surface_context: dict[str, Any] = {
        "id": surface.id,
        "title": surface.title,
        "purpose": surface.purpose,
    }
    if surface.sections:
        surface_context["sections"] = list(surface.sections)

    grounded: list[str] = []

    if intent == "field_help" and active_target:
        element = _element_context(surface, active_target, state.get(active_target))
        if element is not None:
            grounded.extend(f"{active_target}.{key}" for key in element if key != "id")
            return (
                {"surface": surface_context, "target": element},
                grounded,
                LEVEL_TARGET,
            )
        # 目標不在宣告裡（前端傳了未知 id）：不要猜，退回頁面概觀。
        intent = "page_overview"

    if intent == "validation_help":
        blocked = blocked_elements(surface, state)
        elements = []
        for element_id in blocked:
            element = _element_context(surface, element_id, state[element_id])
            if element is None:
                continue
            elements.append(element)
            grounded.extend(
                f"{element_id}.{key}"
                for key in element
                if key in {"error", "disabled", "disabled_reason"}
            )
        # 使用者選中的元素本身沒被擋，但問的是它——一起帶上，答案才知道要回應誰。
        if active_target and active_target not in blocked:
            element = _element_context(surface, active_target, state.get(active_target))
            if element is not None:
                elements.append(element)
        return (
            {"surface": surface_context, "blocked": elements},
            grounded,
            LEVEL_RELATED,
        )

    # page_overview：只講這一頁是什麼，不帶任何欄位當下的值。
    labels = [
        {
            "id": spec.id,
            "label": spec.label,
            "role": spec.role,
            **({"section": spec.section} if spec.section else {}),
        }
        for spec in surface.elements
    ]
    if labels:
        surface_context["elements"] = labels
    grounded.append(f"{surface.id}.purpose")
    if surface.sections:
        grounded.append(f"{surface.id}.sections")
    return ({"surface": surface_context}, grounded, LEVEL_SURFACE)
