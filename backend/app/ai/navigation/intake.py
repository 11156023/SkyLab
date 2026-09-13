"""配置模式：先把需求問清楚，再產生配置。

一句「我要一台機器」不足以規劃出對的東西，但直接丟一份猜出來的配置給使用者，
他也無從判斷對不對。所以規劃前先走幾個問題，每題都給可以直接點的選項。

判斷「還缺什麼」完全在本地做（關鍵字 + 選項字面比對），不打模型：問問題這件事
不該花一次推論，也不該因為模型慢而卡住對話。真正的規劃還是交給 /recommend。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.ai.navigation.flows import (
    INTAKE_FLOW_ID,
    all_flows,
    find_flow_by_id,
    public_steps,
)
from app.ai.navigation.schemas import (
    IntakeFacts,
    IntakeKey,
    IntakeQuestion,
    IntakeState,
    NavigationMessage,
)
from app.ai.template_recommendation.recommendation_service import (
    GPU_KEYWORDS,
    WINDOWS_KEYWORDS,
)

# 圖形介面與 Windows 一樣會把選擇推向 VM，放在同一題問。
_DISPLAY_KEYWORDS = (
    "gui", "圖形介面", "桌面", "遠端桌面", "rdp", "vnc", "視窗",
)
_DURATION_KEYWORDS = (
    "天", "週", "周", "個月", "學期", "小時", "整學期", "長期", "短期",
    "day", "week", "month", "semester",
)


@dataclass(frozen=True)
class IntakeSlot:
    key: str
    question: str
    options: tuple[str, ...]
    keywords: tuple[str, ...] = ()


SLOTS: tuple[IntakeSlot, ...] = (
    IntakeSlot(
        key="purpose",
        question="這台機器主要要拿來做什麼？",
        options=("課程作業", "專題開發", "架設網站", "AI 訓練", "資料庫"),
    ),
    IntakeSlot(
        key="gpu",
        question="需要 GPU 嗎？",
        options=("需要 GPU", "不需要 GPU", "不確定，你幫我判斷"),
        keywords=GPU_KEYWORDS,
    ),
    IntakeSlot(
        key="display",
        question="需要 Windows 或圖形桌面嗎？",
        options=("需要圖形桌面", "需要 Windows", "Linux 指令列就好"),
        keywords=WINDOWS_KEYWORDS + _DISPLAY_KEYWORDS,
    ),
    IntakeSlot(
        key="duration",
        question="大概要用多久？",
        options=("幾天", "幾週", "整個學期", "還不確定"),
        keywords=_DURATION_KEYWORDS,
    ),
)

_ALL_OPTIONS = {option for slot in SLOTS for option in slot.options}
# 使用者可能只是點了上一題的選項，那不算描述用途。
_PURPOSE_KEYWORDS = (
    "網站", "架站", "網頁", "開發", "專題", "作業", "課程", "教學", "研究",
    "資料庫", "訓練", "推論", "node", "flask", "django", "web", "database",
    "mysql", "postgres", "redis", "docker", "python", "java", "minecraft",
    "pytorch", "tensorflow", "深度學習", "機器學習", "deep learning", "research",
)
_ACK = re.compile(r"^(好|好的|好啊|可以|沒問題|ok|yes|繼續|下一步)[。！!\s]*$", re.I)
_NO = re.compile(r"^(不用|不需要|不要|沒有|no)[。！!\s]*$", re.I)


def _is_answer(text: str) -> bool:
    return bool(text.strip()) and not (
        _ACK.fullmatch(text.strip())
        or re.search(r"為什麼|什麼意思|是什麼|怎麼|如何|取消|先不要", text)
        or text.strip().rstrip("。！!？?") in {"幫我填", "我沒有機器", "沒有機器"}
    )


def _explicit_answer(slot: IntakeSlot, text: str) -> str | None:
    """Only user statements contribute facts; retain the answer, not a boolean."""
    if not _is_answer(text):
        return None
    if slot.key == "purpose":
        if text in _ALL_OPTIONS and text not in slot.options:
            return None
        return text if _mentions(text, _PURPOSE_KEYWORDS) else None
    if any(option in text for option in slot.options) or _mentions(text, slot.keywords):
        return text
    return None


def _collect_facts(
    history: list[NavigationMessage] | None,
    facts: IntakeFacts | None,
    pending_key: IntakeKey | None,
) -> IntakeFacts:
    result = (facts or IntakeFacts()).model_copy(deep=True)

    def record(key: str, value: str) -> None:
        # Keep the original detailed purpose when a later category button is clicked.
        if key == "purpose" and result.purpose and value in SLOTS[0].options:
            return
        setattr(result, key, value)
        result.inferred = [item for item in result.inferred if item != key]

    pending = None
    # Once facts exist, earlier messages have already been processed. Replaying
    # them could undo a later correction whose original message was trimmed.
    has_memory = facts is not None and any(getattr(facts, slot.key) for slot in SLOTS)
    messages = (history or [])[-1:] if has_memory else (history or [])
    for message in messages:
        text = message.content.strip()
        if message.role == "assistant":
            pending = next((slot.key for slot in SLOTS if slot.question in text), None)
            continue
        if not _is_answer(text):
            pending = None
            continue
        for slot in SLOTS:
            value = _explicit_answer(slot, text)
            if value:
                record(slot.key, value)
        if pending:
            slot = next(slot for slot in SLOTS if slot.key == pending)
            if slot.key in {"gpu", "display"} and _NO.fullmatch(text):
                record(slot.key, "不需要 GPU" if slot.key == "gpu" else "Linux 指令列就好")
            elif slot.key == "purpose" and text not in _ALL_OPTIONS and not any(
                _explicit_answer(other, text) for other in SLOTS[1:]
            ):
                record(slot.key, text)
        pending = None

    # The client identifies the question being answered even after old turns expire.
    last = history[-1] if history else None
    if pending_key and last and last.role == "user" and _is_answer(last.content):
        text = last.content.strip()
        if pending_key in {"gpu", "display"} and _NO.fullmatch(text):
            record(pending_key, "不需要 GPU" if pending_key == "gpu" else "Linux 指令列就好")
        elif text in {"不確定", "你幫我判斷", "都可以"} and pending_key != "purpose":
            record(pending_key, "還不確定" if pending_key == "duration" else "不確定，你幫我判斷")
        elif pending_key == "purpose" and text not in _ALL_OPTIONS and not any(
            _explicit_answer(other, text) for other in SLOTS[1:]
        ):
            record(pending_key, text)

    # Ordinary web services have useful defaults; explicit requirements win.
    purpose = result.purpose or ""
    ordinary_web = _mentions(purpose, ("網站", "網頁", "架站", "node.js", "nodejs", "flask", "django", "website"))
    if ordinary_web and not _mentions(purpose, GPU_KEYWORDS + WINDOWS_KEYWORDS + _DISPLAY_KEYWORDS):
        for key, value in (("gpu", "不需要 GPU"), ("display", "Linux 指令列就好")):
            if not getattr(result, key):
                setattr(result, key, value)
                result.inferred.append(key)
    return result


def _mentions(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(keyword.casefold() in lowered for keyword in keywords)


def read_intake(
    history: list[NavigationMessage] | None,
    *,
    facts: IntakeFacts | None = None,
    pending_key: IntakeKey | None = None,
) -> IntakeState:
    """看看還缺哪一格，回傳下一個要問的問題（都齊了就是 ready）。

    答案獨立保存；只有實際回答才填入，單純問過或同意繼續不算回答。
    """
    facts = _collect_facts(history, facts, pending_key)
    answered = [slot.key for slot in SLOTS if getattr(facts, slot.key)]
    missing = [slot for slot in SLOTS if slot.key not in answered]

    # 配置產生後要接回「申請一台機器」的後續步驟，所以每一輪都把流程帶著，
    # 不管使用者是從流程進來的還是直接問「推薦規格」。
    flow = find_flow_by_id(INTAKE_FLOW_ID, all_flows())
    recommend_index = next(
        (index for index, step in enumerate(flow.steps) if step.action == "recommend"),
        0,
    ) if flow else 0
    flow_fields = {
        "facts": facts,
        "assumptions": [getattr(facts, key) for key in facts.inferred if getattr(facts, key)],
        "flow_id": flow.flow_id,
        "flow_title": flow.title,
        # 進度停在規劃那一步：填完還要自己檢查、輸入密碼、按送出，
        # 都在同一張表單上，還沒到「等待審核」。
        "steps": public_steps(flow, recommend_index),
    } if flow else {}

    if not missing:
        return IntakeState(
            ready=True,
            answered=len(SLOTS),
            total=len(SLOTS),
            known=answered,
            question=None,
            hint="需求問齊了，我來規劃配置。",
            **flow_fields,
        )

    nxt = missing[0]
    return IntakeState(
        ready=False,
        answered=len(answered),
        total=len(SLOTS),
        known=answered,
        question=IntakeQuestion(
            key=nxt.key,
            text=nxt.question,
            options=list(nxt.options),
        ),
        hint="我先問幾個問題，再依你的答案產生配置。",
        **flow_fields,
    )
