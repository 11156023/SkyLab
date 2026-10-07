"""問題分類。純規則，不打模型。

分類是為了決定「要撈哪些情境」，撈錯了模型再強也答不對，而分類本身沒有難到
需要一次推論。規則看不出來時退回 ``page_overview``——講這一頁在做什麼，永遠
是安全的答案，不會誤導。
"""

from __future__ import annotations

from app.ai.contextual_help.schemas import HelpIntent
from app.ai.utils import mentions

# 「為什麼不能送」這類問題。放在最前面比對：它同時會命中欄位關鍵字
# （「這個欄位為什麼是紅的」），但使用者要的是被擋的原因，不是欄位定義。
_VALIDATION_KEYWORDS = (
    "不能送", "不能按", "送不出", "送不了", "無法送出", "沒反應", "按不了",
    "為什麼不行", "為什麼失敗", "錯誤", "紅字", "紅色", "驗證", "擋",
    "必填", "灰的", "反灰", "停用", "disabled", "invalid", "error",
)

_FIELD_KEYWORDS = (
    "這格", "這欄", "這個欄位", "欄位", "這個是什麼", "這是什麼", "怎麼填",
    "要填什麼", "填什麼", "怎麼選", "要選什麼", "選什麼", "什麼意思",
    "限制", "格式", "可以填", "field",
)

_PAGE_KEYWORDS = (
    "這頁", "這一頁", "本頁", "整頁", "這個頁面", "頁面", "這裡是",
    "用來做什麼", "在做什麼", "什麼時候用", "何時用", "什麼時候需要",
    "什麼情況", "page",
)

# 要整頁的導覽，不是一句話的簡介：用法、功能、視窗都要講。
_GUIDE_KEYWORDS = (
    "介紹", "導覽", "教學", "使用說明", "說明一下", "怎麼用", "如何使用",
    "怎麼操作", "如何操作", "教我", "新手", "第一次用", "完整",
    "有哪些功能", "有什麼功能", "功能有哪些", "可以做什麼", "能做什麼",
    # 助手的「頁面導覽」按鈕會用介面語言送出問句，英文與日文也要認得
    "guide", "tour", "how to use", "how do i use", "walk me through",
    "使い方", "案内", "紹介",
)

# 問的是跳出來的視窗。沒有點名哪一個時，列出這頁所有視窗。
_DIALOG_KEYWORDS = (
    "視窗", "彈窗", "對話框", "跳出", "表單", "popup", "dialog", "modal",
    "ダイアログ", "ウィンドウ",
)

# 明確指著某個元素問。選中元素時，這些說法問的是那個元素，不是整頁導覽。
_POINTER_KEYWORDS = ("這格", "這欄", "這個欄位", "這顆", "這個按鈕", "這個選項")

# 講明了要整頁。問題裡剛好提到某個按鈕名稱時，靠這些字分辨「這頁怎麼用」
# 與「『測試』怎麼用」。
_WHOLE_PAGE_KEYWORDS = (
    "這頁", "這一頁", "本頁", "整頁", "這個頁面", "頁面", "介紹", "導覽",
    "使用說明", "新手", "完整", "有哪些功能", "有什麼功能", "功能有哪些",
)


def classify(
    question: str,
    *,
    has_active_target: bool,
    has_blocked: bool,
    named_element: bool = False,
    named_dialog: bool = False,
    has_dialogs: bool = False,
) -> HelpIntent:
    """依問題文字與畫面現況決定 intent。

    ``has_blocked`` 是「畫面上真的有驗證錯誤或停用原因」。有錯誤在眼前時，
    一句沒頭沒尾的「為什麼」問的幾乎一定是那個錯誤，而不是頁面簡介。
    ``named_element`` 是問題裡講出了某個元素的名稱；``named_dialog`` 是點名了
    這頁的某個彈出視窗；``has_dialogs`` 是這頁有沒有彈出視窗可講。
    """
    text = question.strip()
    if not text:
        return "page_overview"

    if mentions(text, _VALIDATION_KEYWORDS):
        return "validation_help"
    if has_active_target and mentions(text, _POINTER_KEYWORDS):
        return "field_help"
    if named_dialog:
        return "dialog_help"
    if mentions(text, _DIALOG_KEYWORDS) and not has_active_target:
        # 這頁本身就是表單（例如申請表單）時，整頁導覽裡就有每一欄的填法
        return "dialog_help" if has_dialogs else "page_guide"
    if mentions(text, _GUIDE_KEYWORDS) and (
        not named_element or mentions(text, _WHOLE_PAGE_KEYWORDS)
    ):
        return "page_guide"
    if mentions(text, _PAGE_KEYWORDS):
        return "page_overview"
    if mentions(text, _FIELD_KEYWORDS):
        return "field_help" if has_active_target else "page_overview"

    # 指代詞（「這個」「它」）沒有明講要問什麼，靠畫面現況決定：
    # 有東西被擋住就先解釋被擋的原因，否則解釋選中的元素。
    if has_blocked and text.startswith(("為什麼", "怎麼會", "why")):
        return "validation_help"
    if has_active_target:
        return "field_help"
    return "page_overview"
