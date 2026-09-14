"""問題分類。純規則，不打模型。

分類是為了決定「要撈哪些情境」，撈錯了模型再強也答不對，而分類本身沒有難到
需要一次推論。規則看不出來時退回 ``page_overview``——講這一頁在做什麼，永遠
是安全的答案，不會誤導。
"""

from __future__ import annotations

from app.ai.contextual_help.schemas import HelpIntent

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
    "可以做什麼", "能做什麼", "用來做什麼", "在做什麼", "page",
)


def _mentions(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def classify(question: str, *, has_active_target: bool, has_blocked: bool) -> HelpIntent:
    """依問題文字與畫面現況決定 intent。

    ``has_blocked`` 是「畫面上真的有驗證錯誤或停用原因」。有錯誤在眼前時，
    一句沒頭沒尾的「為什麼」問的幾乎一定是那個錯誤，而不是頁面簡介。
    """
    text = question.strip()
    if not text:
        return "page_overview"

    if _mentions(text, _VALIDATION_KEYWORDS):
        return "validation_help"
    if _mentions(text, _PAGE_KEYWORDS):
        return "page_overview"
    if _mentions(text, _FIELD_KEYWORDS):
        return "field_help" if has_active_target else "page_overview"

    # 指代詞（「這個」「它」）沒有明講要問什麼，靠畫面現況決定：
    # 有東西被擋住就先解釋被擋的原因，否則解釋選中的元素。
    if has_blocked and text.startswith(("為什麼", "怎麼會", "why")):
        return "validation_help"
    if has_active_target:
        return "field_help"
    return "page_overview"
