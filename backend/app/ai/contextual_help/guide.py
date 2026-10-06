"""完整導覽與彈出視窗說明：直接由畫面定義組出答案，不呼叫模型。

導覽要講的每一件事——用途、時機、功能用法、視窗每一欄怎麼填、該改去哪一頁——
都已經寫在 ``surface_guides.py`` 裡。讓模型改寫只會多一次等待，還多一個講錯欄位
名稱的機會；照定義排版，答案跟畫面上的字永遠一致。

輸出是 markdown（助手面板會渲染），只用粗體與清單，不用標題：面板很窄。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.ai.contextual_help.schemas import (
    DialogSpec,
    RelatedTarget,
    SurfaceSpec,
)
from app.ai.navigation.catalog import NavigationRoute, can_access, find_route_by_path
from app.models.user import UserRole

# 只講到開啟按鈕時，要同時有這些字才算在問那個視窗
_FILL_WORDS = ("填", "視窗", "彈窗", "對話框", "表單", "欄位", "跳出")


def visible_dialogs(surface: SurfaceSpec, role: UserRole) -> tuple[DialogSpec, ...]:
    """這個身分在這頁看得到的視窗。看不到的連提都不提，免得照著找卻找不到。"""
    return tuple(
        dialog for dialog in surface.dialogs if can_access(dialog.access, role)
    )


def related_targets(
    surface: SurfaceSpec, routes: Iterable[NavigationRoute]
) -> list[RelatedTarget]:
    """這頁的相關頁面，只留使用者有權限去的。

    ``routes`` 是已依身分過濾的導覽目錄；不在裡面的頁面連提都不提，免得學生
    看到管理頁的按鈕、點了又被擋回來。
    """
    allowed = list(routes)
    targets: list[RelatedTarget] = []
    for item in surface.related:
        route = find_route_by_path(item.path, allowed)
        if route is None:
            continue
        targets.append(
            RelatedTarget(title=route.title, path=route.path, reason=item.when)
        )
    return targets


def _dialog_summary(dialog: DialogSpec) -> str:
    required = [field.label for field in dialog.fields if field.required]
    line = f"- **{dialog.title}**（按「{dialog.opened_by}」開啟）：{dialog.purpose}"
    if required:
        line += f"必填：{'、'.join(required)}。"
    return line


def render_page_guide(
    surface: SurfaceSpec,
    dialogs: tuple[DialogSpec, ...],
    related: list[RelatedTarget],
) -> str:
    parts = [f"**{surface.title}**：{surface.purpose}"]
    if surface.when_to_use:
        parts.append(f"**什麼時候用**：{surface.when_to_use}")
    if surface.features:
        parts.append(
            "**怎麼用**\n" + "\n".join(f"- {item}" for item in surface.features)
        )
    if dialogs:
        parts.append(
            "**會跳出的視窗**\n"
            + "\n".join(_dialog_summary(dialog) for dialog in dialogs)
        )
        parts.append(f"想看某個視窗每一欄怎麼填，可以問「{dialogs[0].title}怎麼填」。")
    if related:
        parts.append("要做的事不在這頁的話，可以直接點相關頁面前往。")
    return "\n\n".join(parts)


def render_dialog(dialog: DialogSpec) -> str:
    parts = [f"**{dialog.title}**（按「{dialog.opened_by}」開啟）\n{dialog.purpose}"]
    if dialog.fields:
        lines = []
        for field in dialog.fields:
            tag = "（必填）" if field.required else ""
            detail = f"：{field.help}" if field.help else ""
            lines.append(f"- **{field.label}**{tag}{detail}")
        parts.append("**怎麼填**\n" + "\n".join(lines))
    if dialog.notes:
        parts.append("**注意**\n" + "\n".join(f"- {note}" for note in dialog.notes))
    return "\n\n".join(parts)


def render_dialog_index(surface: SurfaceSpec, dialogs: tuple[DialogSpec, ...]) -> str:
    """問「這頁的視窗怎麼填」卻沒點名哪一個：只有一個就直接講，否則先列出來請他挑。"""
    if len(dialogs) == 1:
        return render_dialog(dialogs[0])
    lines = "\n".join(_dialog_summary(dialog) for dialog in dialogs)
    return (
        f"「{surface.title}」有這些會跳出的視窗：\n\n{lines}\n\n"
        f"想看其中一個每一欄怎麼填，可以問「{dialogs[0].title}怎麼填」。"
    )


def match_dialog(dialogs: tuple[DialogSpec, ...], question: str) -> DialogSpec | None:
    """問題裡點名了哪個視窗。

    先比標題（最長的命中優先，避免「新增」搶走「新增 DNS Record」）。只講到
    開啟它的按鈕時，要同時提到填寫或視窗才算——「新增連線是什麼」問的可能
    只是按鈕。
    """
    text = (question or "").casefold()
    if not text or not dialogs:
        return None
    by_title = [d for d in dialogs if d.title.casefold() in text]
    if by_title:
        return max(by_title, key=lambda d: len(d.title))
    if not any(word in text for word in _FILL_WORDS):
        return None
    by_opener = [d for d in dialogs if d.opened_by.casefold() in text]
    if by_opener:
        return max(by_opener, key=lambda d: len(d.opened_by))
    return None
