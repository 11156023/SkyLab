from __future__ import annotations

import json
import os
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


load_dotenv(".env", override=True)

NavigationAction = Literal["navigate", "suggest", "clarify"]
GuideMode = Literal["guide", "shortcut"]


class PageCapability(BaseModel):
    key: str
    title: str
    summary: str
    keywords: list[str]
    roles: list[str] = Field(default_factory=lambda: ["student", "teacher", "admin"])
    group: str
    stage: str
    page_type: str
    actions: list[str] = Field(default_factory=list)


class WorkflowStep(BaseModel):
    step: int
    page_key: str
    title: str
    instruction: str
    expected_result: str


class NavigationResolveRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=1000)
    role: Literal["student", "teacher", "admin"] = "student"
    mode: GuideMode = "guide"


class NavigationTarget(BaseModel):
    page_key: str
    title: str
    reason: str
    group: str
    stage: str


class NavigationResolveResponse(BaseModel):
    intent: str
    confidence: float = Field(ge=0.0, le=1.0)
    action: NavigationAction
    primary: NavigationTarget | None = None
    suggestions: list[NavigationTarget] = Field(default_factory=list)
    workflow: list[WorkflowStep] = Field(default_factory=list)
    clarification_question: str | None = None
    source: Literal["vllm", "fallback"]
    explanation: str


PAGES: list[PageCapability] = [
    PageCapability(
        key="dashboard",
        title="總覽儀表板",
        summary="查看目前資源、申請、告警與近期任務狀態。",
        keywords=["首頁", "總覽", "dashboard", "狀態", "告警", "任務"],
        group="個人",
        stage="overview",
        page_type="summary",
        actions=["查看資源狀態", "查看待處理事項", "進入常用功能"],
    ),
    PageCapability(
        key="my-resources",
        title="我的資源",
        summary="查看自己已取得的 VM、LXC、GPU 或課程環境。",
        keywords=["我的資源", "我的 vm", "vm", "lxc", "資源", "環境", "機器"],
        group="個人",
        stage="inspect",
        page_type="list",
        actions=["啟動或停止資源", "打開 console", "查看連線資訊"],
    ),
    PageCapability(
        key="my-requests",
        title="我的申請",
        summary="查看資源申請紀錄、申請狀態與退件原因。",
        keywords=["我的申請", "申請紀錄", "進度", "狀態", "退件", "等待"],
        group="個人",
        stage="tracking",
        page_type="list",
        actions=["查看審核狀態", "補充申請資訊", "重新送出"],
    ),
    PageCapability(
        key="request-form",
        title="建立資源申請",
        summary="申請新的 VM、LXC、GPU、課程實驗環境或服務。",
        keywords=["申請", "建立", "新的", "開 vm", "要一台", "gpu", "課程環境", "request", "create vm", "gpu vm"],
        group="個人",
        stage="request",
        page_type="form",
        actions=["選擇 VM 或 LXC", "填寫 CPU/記憶體/磁碟", "送出申請"],
    ),
    PageCapability(
        key="request-review",
        title="審核申請",
        summary="教師或管理員審核學生提交的資源申請。",
        keywords=["審核", "資源申請", "學生申請", "批准", "拒絕", "review", "approval", "approve", "student request", "待審"],
        roles=["teacher", "admin"],
        group="資源",
        stage="approval",
        page_type="review",
        actions=["檢查申請內容", "批准或退回", "查看資源可用性"],
    ),
    PageCapability(
        key="resource-mgmt",
        title="資源管理",
        summary="管理平台內所有 VM、LXC 與資源生命週期。",
        keywords=["資源管理", "全部資源", "刪除 vm", "管理 vm", "管理 lxc"],
        roles=["teacher", "admin"],
        group="資源",
        stage="operate",
        page_type="table",
        actions=["搜尋資源", "批次操作", "刪除或延長資源"],
    ),
    PageCapability(
        key="gpu-mgmt",
        title="GPU 管理",
        summary="管理 GPU mapping、可用張數、節點綁定與分配狀態。",
        keywords=["gpu", "cuda", "顯卡", "gpu 管理", "mapping", "vram"],
        roles=["teacher", "admin"],
        group="資源",
        stage="resource",
        page_type="table",
        actions=["查看 GPU 可用量", "建立 mapping", "檢查節點綁定"],
    ),
    PageCapability(
        key="batch-review",
        title="批量審核",
        summary="審核大量課程或群組資源派發工作。",
        keywords=["批量", "批次", "群組申請", "大量", "batch"],
        roles=["teacher", "admin"],
        group="資源",
        stage="approval",
        page_type="review",
        actions=["查看批次內容", "確認名單", "批准派發"],
    ),
    PageCapability(
        key="firewall",
        title="防火牆規則",
        summary="設定資源對外連線、連入限制與 NAT 規則。",
        keywords=["防火牆", "firewall", "port", "連線", "nat", "開 port"],
        group="網路",
        stage="network",
        page_type="graph",
        actions=["選擇 VM", "新增 port 規則", "確認連線方向"],
    ),
    PageCapability(
        key="reverse-proxy",
        title="反向代理",
        summary="設定網域、HTTPS 與服務路由到指定資源。",
        keywords=["反向代理", "reverse proxy", "網域", "domain", "https", "traefik", "website"],
        group="網路",
        stage="network",
        page_type="form",
        actions=["選擇網域", "指定目標服務", "啟用 HTTPS"],
    ),
    PageCapability(
        key="domain",
        title="網域管理",
        summary="管理 Cloudflare/DNS 網域、記錄與驗證狀態。",
        keywords=["網域", "dns", "domain", "cloudflare", "record"],
        roles=["teacher", "admin"],
        group="網路",
        stage="network",
        page_type="table",
        actions=["新增 DNS record", "檢查解析", "綁定服務"],
    ),
    PageCapability(
        key="ip-management",
        title="IP 管理",
        summary="管理 IP pool、分配紀錄與 subnet 狀態。",
        keywords=["ip", "subnet", "ip pool", "位址", "ip 管理"],
        roles=["teacher", "admin"],
        group="網路",
        stage="network",
        page_type="table",
        actions=["查看 IP pool", "檢查分配", "調整 subnet"],
    ),
    PageCapability(
        key="gateway",
        title="閘道 VM",
        summary="查看 Gateway VM 狀態、服務版本與同步任務。",
        keywords=["gateway", "閘道", "traefik", "gateway vm", "同步"],
        roles=["teacher", "admin"],
        group="網路",
        stage="network",
        page_type="status",
        actions=["檢查服務狀態", "同步設定", "查看 logs"],
    ),
    PageCapability(
        key="ai-api",
        title="AI API 申請",
        summary="申請 AI API 使用權限、模型額度與使用目的。",
        keywords=["ai api 申請", "申請 ai", "模型權限", "額度申請"],
        group="AI 服務",
        stage="ai-access",
        page_type="form",
        actions=["填寫用途", "選擇額度", "送出審核"],
    ),
    PageCapability(
        key="ai-api-review",
        title="AI API 審核",
        summary="審核使用者提交的 AI API 權限與額度申請。",
        keywords=["ai api 審核", "審核 ai", "批准 token", "模型申請審核"],
        roles=["teacher", "admin"],
        group="AI 服務",
        stage="ai-access",
        page_type="review",
        actions=["查看申請理由", "設定額度", "批准或退回"],
    ),
    PageCapability(
        key="ai-api-keys",
        title="AI API Key",
        summary="建立、輪替或查看 AI API token 與用量限制。",
        keywords=["ai api", "api key", "token", "key", "金鑰", "用量", "額度"],
        group="AI 服務",
        stage="ai-access",
        page_type="keys",
        actions=["建立 token", "輪替金鑰", "查看用量"],
    ),
    PageCapability(
        key="ai-monitoring",
        title="AI 監控",
        summary="查看 AI API 呼叫量、使用者用量與錯誤率。",
        keywords=["ai 監控", "用量", "usage", "monitoring", "錯誤率"],
        roles=["teacher", "admin"],
        group="AI 服務",
        stage="ai-ops",
        page_type="metrics",
        actions=["查看用量圖表", "檢查錯誤", "追蹤使用者"],
    ),
    PageCapability(
        key="ai-management",
        title="AI 管理",
        summary="管理 AI 模型、系統提示、API 政策與審核規則。",
        keywords=["ai 管理", "模型管理", "policy", "prompt", "系統提示"],
        roles=["admin"],
        group="AI 服務",
        stage="ai-ops",
        page_type="admin",
        actions=["調整模型", "設定政策", "查看系統狀態"],
    ),
    PageCapability(
        key="groups",
        title="群組",
        summary="管理課程群組、成員與批次派發對象。",
        keywords=["群組", "group", "課程", "學生名單", "成員"],
        roles=["teacher", "admin"],
        group="系統管理",
        stage="admin",
        page_type="table",
        actions=["建立群組", "匯入成員", "查看群組資源"],
    ),
    PageCapability(
        key="admin",
        title="使用者管理",
        summary="管理使用者、角色、啟用狀態與帳號權限。",
        keywords=["admin", "使用者", "帳號", "角色", "權限"],
        roles=["admin"],
        group="系統管理",
        stage="admin",
        page_type="admin",
        actions=["搜尋使用者", "調整角色", "停用帳號"],
    ),
    PageCapability(
        key="settings",
        title="系統設定",
        summary="調整平台層級設定、資源政策與系統參數。",
        keywords=["系統設定", "設定", "參數", "policy", "config"],
        roles=["admin"],
        group="系統管理",
        stage="admin",
        page_type="settings",
        actions=["調整政策", "保存設定", "檢查環境"],
    ),
    PageCapability(
        key="migration",
        title="Migration Jobs",
        summary="查看資源遷移工作、狀態與失敗原因。",
        keywords=["migration", "遷移", "搬移", "移轉", "job"],
        roles=["teacher", "admin"],
        group="系統管理",
        stage="ops",
        page_type="jobs",
        actions=["查看遷移工作", "重試失敗任務", "檢查節點"],
    ),
    PageCapability(
        key="jobs",
        title="背景任務",
        summary="查看建立、刪除、同步與部署等背景任務。",
        keywords=["背景任務", "job", "task", "部署", "同步", "失敗"],
        roles=["teacher", "admin"],
        group="系統管理",
        stage="ops",
        page_type="jobs",
        actions=["查看任務狀態", "篩選失敗", "檢查 logs"],
    ),
    PageCapability(
        key="audit",
        title="Audit Logs",
        summary="查看使用者操作紀錄、系統事件與安全稽核。",
        keywords=["audit", "logs", "稽核", "操作紀錄", "安全"],
        roles=["admin"],
        group="系統管理",
        stage="ops",
        page_type="logs",
        actions=["搜尋事件", "匯出紀錄", "檢查操作來源"],
    ),
]


app = FastAPI(title="AI Navigation Demo")
app.mount("/static", StaticFiles(directory="static"), name="static")


def allowed_pages(role: str) -> list[PageCapability]:
    return [page for page in PAGES if role in page.roles]


def make_target(page: PageCapability, reason: str) -> NavigationTarget:
    return NavigationTarget(
        page_key=page.key,
        title=page.title,
        reason=reason or page.summary,
        group=page.group,
        stage=page.stage,
    )


def clamp_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def build_prompt(pages: list[PageCapability], mode: GuideMode) -> str:
    catalog = "\n".join(
        (
            f'- page_key="{page.key}" title="{page.title}" group="{page.group}" '
            f'stage="{page.stage}" type="{page.page_type}" summary="{page.summary}" '
            f'actions="{", ".join(page.actions)}" keywords="{", ".join(page.keywords)}"'
        )
        for page in pages
    )
    mode_rules = (
        "Mode: guide. The user may be new. Return workflow steps with short reasons. "
        "Use 1 step for a single-page task, 2-3 steps for common cross-page tasks, and at most 5 steps for complex tasks.\n"
        if mode == "guide"
        else "Mode: shortcut. The user is experienced. Return the shortest useful button path. "
        "Prefer 1 step, use 2-3 steps only when multiple pages are truly required. Keep instruction text very short.\n"
    )
    return (
        "You are an AI navigation planner for a campus cloud frontend.\n"
        "Map the user's intent to one or more allowed frontend pages from the catalog.\n"
        "Never invent page keys. Use only page_key values listed in the catalog.\n"
        f"{mode_rules}"
        "Return strict JSON only.\n\n"
        "Rules:\n"
        "- action=navigate if one page is clearly correct and confidence >= 0.85.\n"
        "- action=suggest if multiple pages may fit, or a workflow needs multiple pages.\n"
        "- action=clarify if the user intent is unclear.\n\n"
        "JSON schema:\n"
        "{"
        '"intent":"string",'
        '"confidence":0.0,'
        '"action":"navigate|suggest|clarify",'
        '"primary_key":"string or empty",'
        '"suggested_keys":["string"],'
        '"workflow":[{"page_key":"string","instruction":"string","expected_result":"string"}],'
        '"reason":"short reason",'
        '"clarification_question":"string or empty"'
        "}\n\n"
        f"Allowed catalog:\n{catalog}"
    )


async def call_vllm(query: str, pages: list[PageCapability], mode: GuideMode) -> dict[str, Any] | None:
    base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1").rstrip("/")
    api_key = os.getenv("VLLM_API_KEY", "EMPTY")
    model = os.getenv("VLLM_MODEL", "").strip()
    if not model:
        return None

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_prompt(pages, mode)},
            {"role": "user", "content": query},
        ],
        "temperature": 0.1,
        "top_p": 0.9,
        "max_tokens": 450,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    content = str(data["choices"][0]["message"]["content"])
    return extract_json_object(content)


def response_from_payload(
    payload: dict[str, Any],
    pages: list[PageCapability],
    query: str,
    source: Literal["vllm", "fallback"],
    mode: GuideMode,
) -> NavigationResolveResponse:
    by_key = {page.key: page for page in pages}
    confidence = clamp_confidence(payload.get("confidence"))
    action = str(payload.get("action", "")).lower()
    if action not in {"navigate", "suggest", "clarify"}:
        action = "suggest"

    reason = str(payload.get("reason") or "")
    primary_key = str(payload.get("primary_key") or payload.get("primary_path") or "")
    primary_page = by_key.get(primary_key)
    primary = make_target(primary_page, reason) if primary_page else None

    suggestions: list[NavigationTarget] = []
    seen = {primary.page_key} if primary else set()
    for key in payload.get("suggested_keys") or payload.get("suggested_paths") or []:
        page = by_key.get(str(key))
        if page and page.key not in seen:
            suggestions.append(make_target(page, page.summary))
            seen.add(page.key)

    workflow = normalize_workflow(payload.get("workflow"), by_key, primary, query, mode)

    if action == "navigate" and (confidence < 0.85 or primary is None):
        action = "suggest"
    if action in {"navigate", "suggest"} and primary is None and suggestions:
        primary = suggestions.pop(0)
    if action in {"navigate", "suggest"} and primary is None:
        action = "clarify"

    clarification = str(payload.get("clarification_question") or "").strip()
    if action == "clarify" and not clarification:
        clarification = "你想處理資源、網路、AI API，還是管理設定？"

    return NavigationResolveResponse(
        intent=str(payload.get("intent") or query),
        confidence=confidence,
        action=action,  # type: ignore[arg-type]
        primary=primary,
        suggestions=suggestions[:4],
        workflow=workflow,
        clarification_question=clarification or None,
        source=source,
        explanation=(
            "後端先用使用者角色過濾可用頁面，再要求模型只從 catalog 選路徑。"
            if source == "vllm"
            else "目前沒有可用 vLLM 結果，因此後端用關鍵字命中數做 fallback。"
        ),
    )


def keyword_fallback(query: str, pages: list[PageCapability], mode: GuideMode = "guide") -> NavigationResolveResponse:
    text = query.lower()
    scored: list[tuple[int, PageCapability]] = []
    for page in pages:
        score = sum(1 for keyword in page.keywords if keyword.lower() in text)
        if score:
            scored.append((score, page))
    scored.sort(key=lambda item: item[0], reverse=True)

    if not scored:
        return NavigationResolveResponse(
            intent=query,
            confidence=0.25,
            action="clarify",
            suggestions=[],
            clarification_question="請再描述你要完成的任務，例如申請 VM、開 port、建立 API key。",
            source="fallback",
            explanation="沒有命中明確頁面，所以回到追問，避免亂跳頁。",
        )

    primary_score, primary_page = scored[0]
    suggestions = [make_target(page, page.summary) for _, page in scored[1:5]]
    return NavigationResolveResponse(
        intent=query,
        confidence=0.86 if primary_score >= 2 else 0.68,
        action="navigate" if primary_score >= 2 else "suggest",
        primary=make_target(primary_page, primary_page.summary),
        suggestions=suggestions,
        workflow=build_fallback_workflow(query, pages, primary_page, mode),
        clarification_question=None if primary_score >= 2 else "這是最接近的頁面，也可以改選下面候選項。",
        source="fallback",
        explanation="根據 catalog keywords 計算命中數，選出最接近的頁面。",
    )


def normalize_workflow(
    raw_workflow: Any,
    by_key: dict[str, PageCapability],
    primary: NavigationTarget | None,
    query: str,
    mode: GuideMode,
) -> list[WorkflowStep]:
    steps: list[WorkflowStep] = []
    max_steps = 3 if mode == "shortcut" else 5
    if isinstance(raw_workflow, list):
        for item in raw_workflow:
            if not isinstance(item, dict):
                continue
            page = by_key.get(str(item.get("page_key") or item.get("path") or ""))
            if not page:
                continue
            steps.append(
                WorkflowStep(
                    step=len(steps) + 1,
                    page_key=page.key,
                    title=page.title,
                    instruction=str(item.get("instruction") or page.actions[0] if page.actions else page.summary),
                    expected_result=str(item.get("expected_result") or page.summary),
                )
            )
            if len(steps) >= max_steps:
                break
    if steps:
        return steps
    if primary:
        page = by_key.get(primary.page_key)
        if page:
            return [
                WorkflowStep(
                    step=1,
                    page_key=page.key,
                    title=page.title,
                    instruction=page.actions[0] if page.actions else f"進入「{page.title}」處理：{query}",
                    expected_result=page.summary,
                )
            ]
    return []


def build_fallback_workflow(
    query: str,
    pages: list[PageCapability],
    primary_page: PageCapability,
    mode: GuideMode = "guide",
) -> list[WorkflowStep]:
    by_key = {page.key: page for page in pages}
    text = query.lower()
    keys: list[str] = []

    if any(token in text for token in ["申請", "開 vm", "要一台", "gpu vm", "課程環境"]):
        keys = ["request-form", "my-requests", "my-resources"]
    elif any(token in text for token in ["https", "網域", "domain", "反向代理", "網站"]):
        keys = ["domain", "reverse-proxy", "firewall"]
    elif any(token in text for token in ["api key", "token", "金鑰", "ai api"]):
        keys = ["ai-api", "ai-api-review", "ai-api-keys"]
    elif any(token in text for token in ["審核", "批准", "待審"]):
        keys = ["request-review", "jobs"]
    elif any(token in text for token in ["port", "防火牆", "連線", "nat"]):
        keys = ["my-resources", "firewall"]
    else:
        keys = [primary_page.key]

    visible_pages = [by_key[key] for key in keys if key in by_key]
    if not visible_pages:
        visible_pages = [primary_page]

    max_steps = 3 if mode == "shortcut" else 5
    if mode == "shortcut" and len(visible_pages) > 1:
        visible_pages = visible_pages[:3]

    return [
        WorkflowStep(
            step=index + 1,
            page_key=page.key,
            title=page.title,
            instruction=page.actions[0] if page.actions else f"進入「{page.title}」",
            expected_result=page.summary,
        )
        for index, page in enumerate(visible_pages[:max_steps])
    ]


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


@app.get("/api/capabilities")
async def list_capabilities(role: Literal["student", "teacher", "admin"] = "student") -> list[PageCapability]:
    return allowed_pages(role)


@app.post("/api/navigation/resolve")
async def resolve_navigation(request: NavigationResolveRequest) -> NavigationResolveResponse:
    pages = allowed_pages(request.role)
    try:
        payload = await call_vllm(request.query, pages, request.mode)
    except Exception:
        payload = None
    if payload:
        model_response = response_from_payload(payload, pages, request.query, "vllm", request.mode)
        if model_response.action == "clarify":
            fallback_response = keyword_fallback(request.query, pages, request.mode)
            if fallback_response.primary and fallback_response.confidence >= 0.68:
                fallback_response.explanation = (
                    "模型回覆不夠明確；後端改用 capability keywords 補上快速導引。"
                )
                return fallback_response
        return model_response
    return keyword_fallback(request.query, pages, request.mode)
