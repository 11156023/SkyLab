from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# navigate: 直接帶去某頁；suggest: 給候選；clarify: 反問；
# guide: 這是一段多步驟流程，回傳 steps 讓前端逐步帶著走。
NavigationAction = Literal["navigate", "suggest", "clarify", "guide"]

StepStatus = Literal["done", "current", "todo"]

# 一次對話最多帶幾則歷史進 prompt；再多對導覽沒有幫助，只會拉高 token。
MAX_HISTORY_MESSAGES = 12


class NavigationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=2000)


class NavigationResolveRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000)
    # 同一次對話的前文，由前端保存並回傳（導覽沒有伺服器端會話表）。
    history: list[NavigationMessage] = Field(
        default_factory=list, max_length=MAX_HISTORY_MESSAGES
    )
    # 使用者目前所在的頁面路徑，提供脈絡但不代表工作已完成。
    current_path: str | None = Field(default=None, max_length=200)


class NavigationStepPublic(BaseModel):
    index: int
    title: str
    path: str
    detail: str = ""
    status: StepStatus = "todo"
    state: dict[str, Any] | None = None
    # "recommend" 代表這一步由助手就地完成（規劃配置），而不是導到某一頁
    action: str | None = None


IntakeKey = Literal["purpose", "gpu", "display", "duration"]


class IntakeFacts(BaseModel):
    """Confirmed answers survive history trimming; defaults remain distinguishable."""

    purpose: str | None = Field(default=None, max_length=2000)
    gpu: str | None = Field(default=None, max_length=2000)
    display: str | None = Field(default=None, max_length=2000)
    duration: str | None = Field(default=None, max_length=2000)
    inferred: list[IntakeKey] = Field(default_factory=list, max_length=4)


class IntakeRequest(BaseModel):
    """配置模式的每一輪：把到目前為止的對話送回來，問下一個問題。"""

    history: list[NavigationMessage] = Field(
        default_factory=list, max_length=MAX_HISTORY_MESSAGES
    )
    # 相容舊客戶端；問過不等於已回答，不能用來推算完成度。
    asked: list[str] = Field(default_factory=list, max_length=20)
    facts: IntakeFacts = Field(default_factory=IntakeFacts)
    pending_key: IntakeKey | None = None
    goal: str | None = Field(default=None, max_length=2000)


class IntakeQuestion(BaseModel):
    key: str
    # 與下方選項配對的實際問句，前端直接顯示以避免問答主題不一致。
    text: str
    # 可以直接點的答案，讓使用者不用打字
    options: list[str] = Field(default_factory=list)


class IntakeState(BaseModel):
    ready: bool
    answered: int
    total: int
    known: list[str] = Field(default_factory=list)
    facts: IntakeFacts = Field(default_factory=IntakeFacts)
    assumptions: list[str] = Field(default_factory=list)
    question: IntakeQuestion | None = None
    hint: str = ""
    # 配置只是「申請一台機器」的其中一步，附上整條流程，配置產生後才接得回去。
    # 這是固定的策展流程，不需要模型判斷。
    flow_id: str | None = None
    flow_title: str | None = None
    steps: list[NavigationStepPublic] = Field(default_factory=list)


class NavigationTarget(BaseModel):
    title: str
    path: str
    reason: str = ""
    # 交給 react-router 的 location state，例如 {"create": true} 會讓
    # /my-requests 直接開啟申請表單，而不是只停在列表。
    state: dict[str, Any] | None = None


class NavigationResolveResponse(BaseModel):
    intent: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    action: NavigationAction
    primary: NavigationTarget | None = None
    suggestions: list[NavigationTarget] = Field(default_factory=list)
    clarification_question: str | None = None
    # action == "guide" 時才有值
    flow_id: str | None = None
    flow_title: str | None = None
    steps: list[NavigationStepPublic] = Field(default_factory=list)
    active_step: int | None = None
