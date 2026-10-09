"""Shared utility functions for AI modules — LLM response processing & safe type coercion."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.core.i18n import t
from app.exceptions import BadRequestError

# 單次對話送進模型的上限。schema 沒有限制則數與長度時，一個請求就能塞進
# 幾 MB 的 prompt，把 GPU 佔滿並產生大量 token 費用。
MAX_CONVERSATION_MESSAGES = 50
MAX_CONVERSATION_CHARS = 32 * 1024


def ensure_conversation_within_limits(
    messages: Sequence[Any],
    *,
    max_messages: int = MAX_CONVERSATION_MESSAGES,
    max_chars: int = MAX_CONVERSATION_CHARS,
) -> None:
    """擋掉過長的對話；超過上限一律 400，不要送進模型。"""
    if len(messages) > max_messages:
        raise BadRequestError(t("ai_guard.too_many_messages", limit=max_messages))
    total_chars = sum(len(str(getattr(message, "content", "") or "")) for message in messages)
    if total_chars > max_chars:
        raise BadRequestError(t("ai_guard.messages_too_long", limit=max_chars))


# 表單快照（form_context）序列化後的上限。它不算在對話字數裡，卻會原樣塞進
# prompt；schema 已逐欄截斷，這裡再擋總量，避免繞過上面的對話上限。
MAX_FORM_CONTEXT_CHARS = 64 * 1024


def ensure_form_context_within_limits(
    form_context_json: str, *, max_chars: int = MAX_FORM_CONTEXT_CHARS
) -> None:
    """表單快照過大一律 400，必須在呼叫模型之前檢查。"""
    if len(form_context_json) > max_chars:
        raise BadRequestError(t("ai_guard.form_context_too_long", limit=max_chars))


# ── Prompt injection 的基本清理 ─────────────────────────────────────────
# 擋的是「改變 prompt 結構」的東西，不是擋某些句子：
#   - 聊天範本的控制 token（<|im_start|>、</think>、[INST]…）：vLLM 套範本時會把
#     它們當成真的角色邊界，使用者就能在自己的訊息裡偽造一段 system 訊息。
#   - 看不見的字元（零寬、雙向覆寫）：可以把指令藏在畫面上看起來正常的字裡。
#   - 控制字元：沒有正當用途，只會讓模型或日誌出現怪東西。
# 「忽略前面的指示」這類句子不在這裡擋：關鍵字黑名單一換說法就繞過，又會誤傷
# 正常問題。那一層交給 prompt 裡「使用者內容是資料」的規則，以及輸出端的白名單。
_TEMPLATE_TOKEN_RE = re.compile(
    r"<\|[^|<>\n]{0,40}\|>"  # <|im_start|>、<|endoftext|>、<|system|>…
    r"|</?think>"
    r"|\[/?INST\]"
    r"|<</?SYS>>",
    re.IGNORECASE,
)
# 要刪掉的字元直接列碼位、用 str.translate 刪除，不寫成正規表示式的字元範圍：
# 範圍兩端是看不見的控制字元，CodeQL（py/overly-large-range）會當成可疑範圍，
# 人也很難一眼看出範圍到底涵蓋了什麼。
_STRIPPED_CODEPOINTS = (
    # 控制字元：保留 \t（0x09）、\n（0x0A）、\r（0x0D）
    *range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F,
    # 零寬字元與左右標記：U+200B–U+200F
    *range(0x200B, 0x2010),
    # 雙向嵌入與覆寫：U+202A–U+202E
    *range(0x202A, 0x202F),
    # 不可見運算子：U+2060–U+2064
    *range(0x2060, 0x2065),
    # 雙向隔離：U+2066–U+2069
    *range(0x2066, 0x206A),
    # BOM / 零寬不換行空格
    0xFEFF,
)
_STRIP_TABLE = dict.fromkeys(_STRIPPED_CODEPOINTS)


def clean_prompt_text(text: str | None) -> str:
    """使用者可控的文字送進 prompt 之前一律先過這裡。"""
    if not text:
        return text or ""
    # 先刪隱形字元再找控制 token：<|im_​start|> 這種拆開寫的也抓得到
    text = text.translate(_STRIP_TABLE)
    return _TEMPLATE_TOKEN_RE.sub("", text)


def strip_think_tags(text: str) -> str:
    """Keep only content after </think> marker; return text as-is if tag absent."""
    marker = "</think>"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker) :].strip()
    return text.strip()


def mentions(text: str, keywords: tuple[str, ...]) -> bool:
    """*text* 是否包含任一關鍵字（不分大小寫，子字串比對）。"""
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def apply_thinking_control(payload: dict[str, Any], enable_thinking: bool) -> dict[str, Any]:
    """Inject *enable_thinking* into the vLLM chat_template_kwargs payload."""
    payload["chat_template_kwargs"] = {
        **dict(payload.get("chat_template_kwargs") or {}),
        "enable_thinking": enable_thinking,
    }
    return payload


def safe_int(
    value: Any,
    default: int = 0,
    *,
    minimum: int | None = None,
    extract_digits: bool = False,
) -> int:
    """Safely coerce *value* to int with configurable fallback, floor, and digit extraction.

    Args:
        value: Raw input (``None``, ``str``, ``int``, ``float``, etc.).
        default: Returned when coercion fails or *value* is ``None``.
        minimum: When not ``None``, clamp result to ``>= minimum``.
        extract_digits: When ``True``, strip non-digit characters from strings
            before parsing (e.g. ``"2 vCPU"`` → ``2``).
    """
    if value is None:
        return default
    try:
        if extract_digits and isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit())
            parsed = int(digits) if digits else int(value)
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return minimum
    return parsed


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce *value* to float; return *default* on failure or ``None``."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    """Safely coerce *value* to bool with fallback.

    Returns ``True`` for truthy values (e.g., ``True``, ``1``, ``"true"``, ``"yes"``, ``"on"``, ``"checked"``)
    and ``False`` for falsy values (e.g., ``False``, ``0``, ``"false"``, ``"no"``, ``"off"``, ``"unchecked"``).
    Otherwise returns *default*.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on", "checked", "done"}:
            return True
        if text in {"false", "0", "no", "n", "off", "unchecked", "todo"}:
            return False
    return default

