"""Contextual help: 靜態畫面定義與 API 形狀。

刻意不記錄任何版面位置——沒有 bounds、沒有 selector、沒有「右上角」這種描述。
版面還會調整，記了就是等著過期，而且過期的位置比沒有位置更糟：使用者會照著
一個不存在的地方找。element 只用穩定的邏輯 ``id`` 指認，section 是邏輯分組
而不是視覺排列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.navigation.catalog import RouteAccess

# 第一版只有三種：都靠文字情境就能回答。需要截圖或座標的視覺說明不在範圍內。
HelpIntent = Literal["field_help", "validation_help", "page_overview"]

# element 的語意角色。用途是讓模型知道「這是可以填的還是只能看的」，
# 不描述它長什麼樣子或在哪裡。
ElementRole = Literal[
    "text", "number", "select", "textarea", "toggle", "date",
    "button", "table", "chart", "list", "readonly",
]

MAX_STATE_ELEMENTS = 60


@dataclass(frozen=True)
class ElementSpec:
    """一個欄位或控制項的靜態定義。這一半永遠以伺服器端為準。"""

    id: str
    role: ElementRole
    label: str
    help: str = ""
    constraints: tuple[str, ...] = ()
    # 屬於畫面的哪一組（對應 SurfaceSpec.sections）。是邏輯分組，不是版面位置：
    # 「排程開機與時段」講的是這個設定管什麼，不是它排在畫面第幾塊。
    section: str = ""
    # 敏感欄位連 value 都不收進情境（密碼、金鑰、憑證）
    sensitive: bool = False


@dataclass(frozen=True)
class SurfaceSpec:
    """一個可以被解釋的畫面。

    ``id`` 而不是 ``path`` 當主鍵：同一個路徑上可能有多個畫面（``/my-requests``
    同時是申請列表與申請表單），路徑分不出來。
    """

    id: str
    path: str
    title: str
    purpose: str
    sections: tuple[str, ...] = ()
    elements: tuple[ElementSpec, ...] = field(default=())
    access: RouteAccess = "all"


# ---------------------------------------------------------------- API


class ElementState(BaseModel):
    """前端送上來的動態狀態。只有瀏覽器知道這一半。"""

    value: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=300)
    disabled: bool | None = None
    disabled_reason: str | None = Field(default=None, max_length=300)


class ExplainRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    surface_id: str = Field(..., min_length=1, max_length=100)
    active_target: str | None = Field(default=None, max_length=100)
    # 前端每次畫面狀態改變就遞增；後端不保存，只原樣回傳，
    # 讓前端能丟棄過期的回應（使用者在等待期間又改了欄位）。
    context_version: int = Field(default=0, ge=0)
    state: dict[str, ElementState] = Field(
        default_factory=dict, max_length=MAX_STATE_ELEMENTS
    )


class ExplainResponse(BaseModel):
    intent: HelpIntent
    answer: str
    target: str | None = None
    # 這個答案用到了哪些情境欄位，讓回答可以被追溯與回歸測試
    grounded_in: list[str] = Field(default_factory=list)
    context_level: int = 0
    context_version: int = 0
    # 是否真的呼叫了模型。結構化資料足以直接作答時不呼叫。
    used_model: bool = False


class SurfacePublic(BaseModel):
    """給前端做「目前路徑對應哪個畫面」的對照表。不含欄位定義：
    那些只在回答時進 prompt，沒有必要整包送到瀏覽器。"""

    id: str
    path: str
    title: str
    # 一句話說明這個畫面在做什麼，功能索引直接用它，不必再問模型
    purpose: str = ""
    # 這個畫面有沒有宣告欄位；沒有的話前端不必註冊動態狀態
    has_fields: bool = False
