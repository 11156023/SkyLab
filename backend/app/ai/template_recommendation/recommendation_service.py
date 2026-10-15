from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import httpx

from app.ai.monitoring import usage_metrics
from app.ai.role_contracts import OutputMode, RoleContract
from app.ai.template_recommendation.config import settings
from app.ai.template_recommendation.node_service import summarize_device_nodes
from app.ai.template_recommendation.prompt import (
    build_fast_ai_plan_prompt,
)
from app.ai.template_recommendation.schemas import (
    PRESET_RESOURCE_BASELINES,
    ChatMessage,
    ChatRequest,
    DeviceNode,
    ExtractedIntent,
    IntakeFactsContext,
    RecommendationRequest,
)
from app.ai.utils import (
    apply_thinking_control,
    ensure_form_context_within_limits,
    keyword_spans,
    mentions_term,
    safe_bool,
    safe_int,
)
from app.core.i18n import t
from app.exceptions import AppError, UpstreamServiceError
from app.infrastructure.ai import VLLMRequestProfile
from app.infrastructure.ai.template_recommendation import client

logger = logging.getLogger(__name__)

TEMPLATE_CHAT_CONTRACT = RoleContract(
    role_id="template_recommendation",
    output_mode=OutputMode.MODEL_FREE_TEXT,
    contract_version="template-recommendation-v1",
    fallback_key="template_recommendation.scope_clarification",
)
TEMPLATE_RECOMMENDATION_CONTRACT = RoleContract(
    role_id="template_recommendation",
    output_mode=OutputMode.MODEL_ACTION,
    contract_version="template-recommendation-v1",
    fallback_key="template_recommendation.scope_clarification",
)
TEMPLATE_ADHERENCE_FALLBACK = (
    "我只能協助規劃 SkyLab 的 VM、LXC 與硬體資源；請描述工作負載、作業系統與資源需求。"
)

MIN_VM_DISK_GB = 20
MIN_LXC_DISK_GB = 8
# 規劃逾時就改用規則配置；上限要比前端等待的時間短，使用者才拿得到備援結果
PLANNER_TIMEOUT_SECONDS = 60.0

# 只放真的指名 Windows 的字。GUI、遠端桌面、RDP 也可能是 Linux 桌面（xrdp、VNC），
# 放這裡會讓要 Linux 桌面的人被配到 Windows 映像；那些字在 DESKTOP_KEYWORDS。
WINDOWS_KEYWORDS = (
    "windows",
    "window",
    "win11",
    "win10",
)

GPU_KEYWORDS = (
    "gpu",
    "cuda",
    "nvidia",
    "pytorch",
    "tensorflow",
    "llm",
    "stable diffusion",
    "comfyui",
    "yolo",
    "ai training",
    "inference",
    "模型訓練",
    "模型推論",
    "深度學習",
    "機器學習",
    "顯卡",
    "加速卡",
    "訓練模型",
    "跑模型",
)

DATABASE_KEYWORDS = (
    "database",
    "db",
    "mysql",
    "postgres",
    "postgresql",
    "mariadb",
    "mongodb",
    "redis",
    "sql",
    "資料庫",
    "關聯式資料庫",
    "資料庫伺服器",
    "儲存資料",
    "存資料",
)

PUBLIC_WEB_KEYWORDS = (
    "public",
    "internet",
    "external",
    "domain",
    "webhook",
    "public ip",
    "public-ip",
    "外網",
    "公開網路",
    "網際網路",
    "網域",
    "域名",
    "公開存取",
    "對外服務",
    "外部連線",
    "公網",
)


# 只用在推薦的需求判斷，不併進 GPU_KEYWORDS：intake 用那份表判斷「GPU 這題答了沒」，
# 選「AI 訓練」當用途不該順便把 GPU 題當成已回答。
_GPU_WORKLOAD_HINTS = ("ai 訓練", "ai訓練", "顯示卡")
# 圖形桌面會把選擇推向 VM，但不代表要 Windows（Linux 桌面也是桌面）。
DESKTOP_KEYWORDS = (
    "圖形桌面",
    "桌面環境",
    "遠端桌面",
    "圖形介面",
    "視窗",
    "gui",
    "vnc",
    "rdp",
    "remote desktop",
)
_NEGATIONS = ("不需要", "不要", "不用", "不必", "無需", "取消", "no", "without")
_UNSURE = ("不確定", "你幫我判斷", "都可以")


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return mentions_term(text, keywords)


def _extract_user_signal_flags(messages: list[ChatMessage]) -> dict[str, bool]:
    user_text = "\n".join(
        str(message.content)
        for message in messages
        if str(message.role).strip().lower() == "user"
    )

    normalized_text = _normalize_user_text_for_intent(user_text)

    return {
        "needs_windows": _contains_any(normalized_text, WINDOWS_KEYWORDS),
        "needs_desktop": _contains_any(normalized_text, DESKTOP_KEYWORDS),
        "requires_gpu": _contains_any(
            normalized_text, GPU_KEYWORDS + _GPU_WORKLOAD_HINTS
        ),
        "needs_database": _contains_any(normalized_text, DATABASE_KEYWORDS),
        "needs_public_web": _contains_any(normalized_text, PUBLIC_WEB_KEYWORDS),
    }


def _last_mention_is_negated(text: str, keywords: tuple[str, ...]) -> bool:
    """這句話最後一次提到這類需求時是不是在否定它。

    要看「最後一次」：前端的記憶區塊依序是原始目標、已回答的事實、本輪要求，
    原始目標說要 GPU、後來改口不需要時，只看第一次出現會永遠關不掉。
    """
    spans = [
        found[-1] for keyword in keywords if (found := keyword_spans(text, keyword))
    ]
    if not spans:
        return False
    # 關鍵字會互相包含（mysql 裡有 sql）：從蓋住最後那個位置的最長字開始往前看，
    # 否則「不要mysql」會只看到「不要my」
    last_start = max(start for start, _ in spans)
    mention_start = min(start for start, end in spans if start <= last_start < end)
    prefix = text[max(0, mention_start - 12) : mention_start].strip()
    return any(prefix.endswith(word) for word in _NEGATIONS)


def _apply_intake_facts(flags: dict[str, bool], facts: IntakeFactsContext) -> None:
    """配置模式問到的答案優先於關鍵字猜測。"""
    purpose = _normalize_user_text_for_intent(facts.purpose or "")
    gpu = _normalize_user_text_for_intent(facts.gpu or "")
    if gpu:
        if _contains_any(gpu, _UNSURE):
            # 交給系統判斷：看用途是不是 GPU 類的工作
            flags["requires_gpu"] = _contains_any(
                purpose, GPU_KEYWORDS + _GPU_WORKLOAD_HINTS
            )
        elif _contains_any(gpu, _NEGATIONS):
            flags["requires_gpu"] = False
        else:
            flags["requires_gpu"] = True
    display = _normalize_user_text_for_intent(facts.display or "")
    if display:
        flags["needs_windows"] = "windows" in display or "win1" in display
        flags["needs_desktop"] = flags["needs_windows"] or _contains_any(
            display, DESKTOP_KEYWORDS + ("桌面",)
        )


# 申請表單的時段最多只能排到 90 天後
MAX_SCHEDULE_DAYS = 90
_CJK_AMOUNTS = {
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5,
}
# 量詞全部設上限、「個」前後的空白只有一種切法：使用者輸入整串 0 或空白時
# 回溯不會變成多項式時間（CodeQL py/polynomial-redos）
_DURATION_PATTERN = re.compile(
    r"(?<!\d)(\d{1,3}(?:\.\d{1,2})?|[一二兩三四五六七八九十半])"
    r"(?:\s{0,3}個)?\s{0,3}"
    r"(月|週|周|星期|禮拜|天|日|months?|weeks?|days?)",
    re.IGNORECASE,
)


def parse_duration_days(text: str | None) -> int | None:
    """把「3天」「2周」「1個月」這類使用時間換成天數；講不出長度就回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if "學期" in text or "semester" in text.lower():
        return MAX_SCHEDULE_DAYS
    match = _DURATION_PATTERN.search(text)
    if not match:
        return None
    amount, unit = match.groups()
    value = float(amount) if amount[0].isdigit() else _CJK_AMOUNTS[amount]
    unit = unit.lower()
    per_unit = 30 if unit[0] in "月m" else 7 if unit[0] in "週周星禮w" else 1
    return min(max(round(value * per_unit), 1), MAX_SCHEDULE_DAYS)


def _schedule_days(option: Any) -> int:
    seconds = (option.end_at - option.start_at).total_seconds()
    return max(round(seconds / 86400), 1)


def _schedule_for_duration(options: list[Any], days: int | None) -> Any | None:
    """挑長度剛好的時段；排不滿就挑最長的較短時段，不替使用者多申請。"""
    if not days or not options:
        return None
    exact = next((option for option in options if _schedule_days(option) == days), None)
    if exact:
        return exact
    shorter = [option for option in options if _schedule_days(option) < days]
    if shorter:
        return max(shorter, key=_schedule_days)
    return min(options, key=_schedule_days)


def infer_intent_from_chat(request: ChatRequest) -> ExtractedIntent:
    """Build a fast intent seed locally; the planner still interprets full chat context."""
    recent = request.messages[-12:]
    user_texts = [
        str(message.content).strip()
        for message in recent
        if str(message.role).strip().lower() == "user" and str(message.content).strip()
    ]
    default_goal = "請依目前表單內容提供完整配置建議"
    goal_summary = "\n".join(user_texts)[-4000:]
    # RecommendationRequest.goal 要求至少 3 字；「hi」「好的」這種短句要補上預設目標，
    # 否則在 route 建 RecommendationRequest 時會丟 ValidationError 變成 500
    if len(goal_summary.strip()) < 3:
        goal_summary = (
            f"{default_goal}：{goal_summary}" if goal_summary else default_goal
        )
    flags = _extract_user_signal_flags(recent)
    normalized = _normalize_user_text_for_intent(goal_summary)
    latest_user_text = _normalize_user_text_for_intent(
        user_texts[-1] if user_texts else ""
    )

    for flag, keywords in (
        ("requires_gpu", GPU_KEYWORDS + _GPU_WORKLOAD_HINTS),
        ("needs_windows", WINDOWS_KEYWORDS),
        ("needs_desktop", DESKTOP_KEYWORDS),
        ("needs_database", DATABASE_KEYWORDS),
        ("needs_public_web", PUBLIC_WEB_KEYWORDS),
    ):
        if _last_mention_is_negated(latest_user_text, keywords):
            flags[flag] = False
    if request.intake_facts is not None:
        _apply_intake_facts(flags, request.intake_facts)
    role = (
        "teacher"
        if any(word in normalized for word in ("teacher", "教授", "老師", "教學"))
        else "student"
    )
    course_context = (
        "teaching"
        if role == "teacher"
        else (
            "research"
            if any(word in normalized for word in ("research", "研究", "實驗"))
            else "coursework"
        )
    )
    budget_mode = (
        "performance"
        if any(word in normalized for word in ("performance", "效能", "速度優先"))
        else (
            "resource-saving"
            if any(word in normalized for word in ("省資源", "低成本", "節省"))
            else "balanced"
        )
    )
    return ExtractedIntent(
        goal_summary=goal_summary,
        role=role,
        course_context=course_context,
        budget_mode=budget_mode,
        **flags,
    )


def _normalize_user_text_for_intent(text: str) -> str:
    return str(text or "").casefold().replace("　", " ")


def _minimum_disk_gb(resource_type: str) -> int:
    return MIN_VM_DISK_GB if resource_type == "vm" else MIN_LXC_DISK_GB


def _gpu_option_label(option: dict[str, Any]) -> str:
    description = str(option.get("description") or "").strip()
    mapping_id = str(option.get("mapping_id") or "").strip()
    node = str(option.get("node") or "").strip()
    if description:
        return description
    if mapping_id and node:
        return f"{mapping_id} ({node})"
    return mapping_id or node or "GPU"


def _best_gpu_option(options: list[dict[str, Any]]) -> dict[str, Any] | None:
    available = [
        option for option in options if int(option.get("available_count") or 0) > 0
    ]
    if not available:
        return None
    return sorted(
        available,
        key=lambda option: (
            -int(option.get("available_count") or 0),
            -int(option.get("total_vram_mb") or 0),
            str(option.get("mapping_id") or ""),
        ),
    )[0]


def _build_submission_reason(
    *,
    request: RecommendationRequest,
    resource_type: str,
    service_name: str,
    cores: int,
    memory_mb: int,
    disk_gb: int,
) -> str:
    usage_label = {
        "coursework": t("recommendation.usageLabel.coursework"),
        "teaching": t("recommendation.usageLabel.teaching"),
        "research": t("recommendation.usageLabel.research"),
    }.get(request.course_context, t("recommendation.usageLabel.general"))
    scope_label = (
        t("recommendation.scopeLabel.personal")
        if request.sharing_scope == "personal"
        else t("recommendation.scopeLabel.shared")
    )
    env_label = "LXC" if resource_type == "lxc" else "VM"
    return t(
        "recommendation.submissionReason",
        env=env_label,
        service=service_name,
        scope=scope_label,
        usage=usage_label,
        cores=cores,
        memory=memory_mb,
        disk=disk_gb,
    )


# 前端帶來的選項清單不放進 prompt；模型用的是後端即時查到的 resource_options
_PROMPT_FORM_CONTEXT_EXCLUDE = frozenset(
    {
        "gpu_options",
        "lxc_os_options",
        "vm_os_options",
        "resource_options_from_client",
    }
)


def prompt_form_context(request: RecommendationRequest) -> dict[str, Any] | None:
    """回傳會塞進 prompt 的表單快照（已排除選項清單）。"""
    if not request.form_context:
        return None
    return request.form_context.model_dump(
        mode="json", exclude=set(_PROMPT_FORM_CONTEXT_EXCLUDE)
    )


def ensure_recommendation_form_context_within_limits(
    request: RecommendationRequest,
) -> None:
    """表單快照不算在對話字數上限裡，呼叫模型之前另外擋總量（過大一律 400）。

    路由要在記錄 AI 用量的 try 之前呼叫，客戶端錯誤才不會被記成上游失敗。
    """
    ensure_form_context_within_limits(
        json.dumps(prompt_form_context(request) or {}, ensure_ascii=False, default=str)
    )


async def generate_ai_plan(
    request: RecommendationRequest,
    chat_history: list[ChatMessage],
    *,
    resource_options: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_name = settings.VLLM_MODEL_NAME
    if not model_name:
        raise AppError(t("templateRec.modelBindingMissing"), 503)

    user_context = {
        "goal": request.goal,
        "role": request.role,
        "preset": request.preset,
        "course_context": request.course_context,
        "sharing_scope": request.sharing_scope,
        "expected_users": request.expected_users,
        "budget_mode": request.budget_mode,
        "resource_baseline": request.resource_baseline,
        "needs_public_web": request.needs_public_web,
        "needs_database": request.needs_database,
        "requires_gpu": request.requires_gpu,
        "needs_windows": request.needs_windows,
        "needs_desktop": request.needs_desktop,
        "form_context": prompt_form_context(request),
    }
    # 第二道防線：路由應已在記錄用量的 try 之前呼叫
    # ensure_recommendation_form_context_within_limits
    ensure_form_context_within_limits(
        json.dumps(user_context["form_context"] or {}, ensure_ascii=False, default=str)
    )
    resource_options = resource_options or {
        "lxc_os_images": [],
        "vm_operating_systems": [],
        "gpu_options": [],
    }
    gpu_options = list(resource_options.get("gpu_options") or [])
    # 不跟模型要說明文字：說明由伺服器依驗證過的欄位產生（render_plan_summary），
    # 模型的自由文字會寫出清單裡沒有的 GPU 或機型。
    plan_schema = {
        "application_target": {
            "service_name": "short service name, e.g. Node.js 網站",
        },
        "form_prefill": {
            "resource_type": "lxc|vm",
            "mode": "immediate|scheduled",
            "hostname": "string",
            "lxc_os_image": "real-lxc-os-image-or-empty",
            "lxc_template_id": "integer-or-0",
            "vm_template_id": "integer-or-0",
            "gpu_mapping_id": "gpu-mapping-id-or-empty",
            "start_at": "ISO-8601-datetime-or-empty",
            "end_at": "ISO-8601-datetime-or-empty",
            "immediate_no_end": "boolean",
            "cores": "integer",
            "memory_mb": "integer",
            "disk_gb": "integer",
        },
    }
    payload = apply_thinking_control(
        {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": build_fast_ai_plan_prompt(
                        user_context=user_context,
                        resource_options={
                            **resource_options,
                            "gpu_options": gpu_options,
                        },
                        plan_schema=plan_schema,
                        conversation_history=[
                            {"role": str(item.role), "content": str(item.content)}
                            for item in chat_history[-12:]
                            if str(item.role).strip().lower() in {"user", "assistant"}
                        ],
                    ),
                }
            ],
            "max_tokens": settings.VLLM_MAX_TOKENS,
            "temperature": settings.VLLM_TEMPERATURE,
            "top_p": settings.VLLM_TOP_P,
            "top_k": settings.VLLM_TOP_K,
            "min_p": settings.VLLM_MIN_P,
            "presence_penalty": settings.VLLM_PRESENCE_PENALTY,
            "repetition_penalty": settings.VLLM_REPETITION_PENALTY,
            "response_format": {"type": "json_object"},
        },
        settings.VLLM_ENABLE_THINKING,
    )

    try:
        started_at = perf_counter()
        started_at_utc = datetime.now(timezone.utc)
        data = await client.create_chat_completion(
            payload,
            profile=VLLMRequestProfile.STRUCTURED_OBJECT,
            timeout=min(float(settings.VLLM_TIMEOUT), PLANNER_TIMEOUT_SECONDS),
            request_id=request_id,
        )
        metrics = usage_metrics(
            data,
            perf_counter() - started_at,
            request_id=request_id,
            started_at=started_at_utc,
        )
        elapsed_seconds = metrics["elapsed_seconds"]
        metrics["tokens_per_second"] = (
            round(metrics["completion_tokens"] / elapsed_seconds, 2)
            if elapsed_seconds > 0
            else 0.0
        )
        parsed = json.loads(data["choices"][0]["message"]["content"])
    except httpx.HTTPError:
        # 交給 route 對應成通用的 upstreamError，不把上游網址等細節回給前端
        raise
    except Exception as exc:
        logger.warning("AI plan generation failed", exc_info=True)
        raise UpstreamServiceError(t("aiTemplateRecommendation.upstreamError")) from exc
    # 模型可能回合法 JSON 但不是物件（例如 []），normalize_ai_result 只吃 dict
    if not isinstance(parsed, dict):
        raise UpstreamServiceError(t("aiTemplateRecommendation.upstreamError"))
    return parsed, metrics


def _rule_based_spec(
    request: RecommendationRequest, resource_type: str
) -> dict[str, int]:
    """模型沒給規格時的預設：從身分預設的基準出發，再依需求往上調。"""
    baseline = dict(
        request.resource_baseline.get(resource_type)
        or PRESET_RESOURCE_BASELINES["student_individual"][resource_type]
    )
    cores = int(baseline.get("cpu") or 1)
    memory_mb = int(baseline.get("memory_mb") or 1024)
    disk_gb = int(baseline.get("disk_gb") or _minimum_disk_gb(resource_type))
    if resource_type == "vm" and request.requires_gpu:
        cores, memory_mb, disk_gb = (
            max(cores, 4),
            max(memory_mb, 8192),
            max(disk_gb, 50),
        )
    elif resource_type == "vm" and request.needs_windows:
        cores, memory_mb, disk_gb = (
            max(cores, 2),
            max(memory_mb, 4096),
            max(disk_gb, 64),
        )
    elif resource_type == "vm" and request.needs_desktop:
        cores, memory_mb, disk_gb = (
            max(cores, 2),
            max(memory_mb, 4096),
            max(disk_gb, 32),
        )
    elif request.needs_database:
        memory_mb, disk_gb = max(memory_mb, 2048), max(disk_gb, 20)
    return {
        "cores": cores,
        "memory_mb": memory_mb,
        "disk_gb": max(disk_gb, _minimum_disk_gb(resource_type)),
    }


def _is_windows_label(label: Any) -> bool:
    return "windows" in str(label or "").casefold()


def _default_vm_candidate(
    candidates: list[dict[str, Any]], *, wants_windows: bool
) -> dict[str, Any]:
    """沒有指定 VM 來源時，挑一個符合 Windows／Linux 需求的，不要盲選第一個。"""
    return next(
        (
            item
            for item in candidates
            if _is_windows_label(item.get("label")) == wants_windows
        ),
        candidates[0],
    )


def rule_based_ai_result(
    request: RecommendationRequest,
    *,
    resource_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """不經模型、只靠需求與候選清單產生的規劃輸入。

    模型逾時、上游錯誤或檢查不放行時用它：使用者已經回答的需求足以規劃出一份
    合理配置，不該只留下一句「已保留你的需求」。輸出仍交給 normalize_ai_result
    逐欄驗證，跟模型的輸出走同一條路。
    """
    goal = _normalize_user_text_for_intent(request.goal)
    for item in (resource_options or {}).get("application_templates") or []:
        name = str(item.get("name") or "").strip()
        if len(name) < 2 or name.casefold() not in goal:
            continue
        resource_type = "lxc" if str(item.get("resource_type")) == "lxc" else "vm"
        template_id = int(item.get("template_id") or 0)
        return {
            "application_target": {"service_name": name},
            "form_prefill": {
                "resource_type": resource_type,
                "lxc_template_id": template_id if resource_type == "lxc" else 0,
                "vm_template_id": template_id if resource_type == "vm" else 0,
                "cores": item.get("cores"),
                "memory_mb": item.get("memory_mb"),
                "disk_gb": item.get("disk_gb"),
            },
        }
    return {}


def _format_memory_gb(memory_mb: int) -> str:
    return f"{round(memory_mb / 1024, 1):g}"


def render_plan_summary(
    *,
    resource_type: str,
    source_label: str,
    from_template: bool,
    cores: int,
    memory_mb: int,
    disk_gb: int,
    gpu_label: str,
    gpu_missing: bool,
    fallback: bool,
) -> str:
    """配置說明一律由伺服器依驗證過的欄位產生。

    提到的作業系統、範本、GPU 都來自候選清單，不會出現平台沒有的型號。
    """
    if not source_label:
        source = t("recommendation.summary.sourceMissing")
    elif from_template:
        source = t("recommendation.summary.sourceTemplate", name=source_label)
    else:
        source = t("recommendation.summary.sourceImage", name=source_label)
    parts = [
        t(
            "recommendation.summary.base",
            env="VM" if resource_type == "vm" else "LXC",
            source=source,
            cores=cores,
            memory=_format_memory_gb(memory_mb),
            disk=disk_gb,
        )
    ]
    if gpu_label:
        parts.append(t("recommendation.summary.gpuSelected", gpu=gpu_label))
    elif gpu_missing:
        parts.append(t("recommendation.summary.gpuUnavailable"))
    if fallback:
        parts.append(t("recommendation.summary.fallback"))
    return "".join(parts)


def _environment_reason(
    request: RecommendationRequest, resource_type: str, *, gpu_selected: bool
) -> str:
    if resource_type != "vm":
        return t("recommendation.environmentReason.lxc")
    if gpu_selected:
        return t("recommendation.environmentReason.gpu")
    if request.needs_windows:
        return t("recommendation.environmentReason.windows")
    if request.needs_desktop:
        return t("recommendation.environmentReason.desktop")
    return t("recommendation.environmentReason.vm")


def normalize_ai_result(
    ai_result: dict[str, Any],
    request: RecommendationRequest,
    nodes: list[DeviceNode],
    *,
    resource_options: dict[str, Any] | None = None,
    fallback: bool = False,
) -> dict[str, Any]:
    resource_options = resource_options or {
        "lxc_os_images": [],
        "vm_operating_systems": [],
        "gpu_options": [],
    }
    lxc_os_images = list(resource_options.get("lxc_os_images") or [])
    vm_operating_systems = list(resource_options.get("vm_operating_systems") or [])
    gpu_options = list(resource_options.get("gpu_options") or [])
    form_context = request.form_context
    # 模型輸出沒有型別保證：null 或型別錯的欄位一律當成沒給
    prefill_value = ai_result.get("form_prefill")
    raw_prefill: dict[str, Any] = (
        dict(prefill_value) if isinstance(prefill_value, dict) else {}
    )
    target_value = ai_result.get("application_target")
    application_target: dict[str, Any] = (
        target_value if isinstance(target_value, dict) else {}
    )

    # GPU 只能掛在 VM 上：需要 GPU 且真的有空的卡，才把整台推向 VM
    gpu_available = _best_gpu_option(gpu_options) is not None
    gpu_forces_vm = request.requires_gpu and gpu_available
    default_resource_type = (
        "vm"
        if request.needs_windows or request.needs_desktop or gpu_forces_vm
        else "lxc"
    )
    resource_type = str(
        raw_prefill.get("resource_type") or default_resource_type
    ).lower()
    if resource_type not in {"lxc", "vm"}:
        resource_type = default_resource_type
    if request.needs_windows or gpu_forces_vm:
        resource_type = "vm"

    hostname_seed = str(
        raw_prefill.get("hostname")
        or (form_context.hostname if form_context else "")
        or "ai-generated-host"
    ).lower()
    hostname = "".join(
        char if (char.isalnum() or char == "-") else "-"
        for char in hostname_seed.replace("_", "-")
    ).strip("-")[:63]
    if not hostname:
        hostname = "ai-generated-host"

    application_templates = list(resource_options.get("application_templates") or [])
    catalog_by_id = {
        int(item.get("template_id") or 0): item for item in application_templates
    }

    # 應用範本優先：選到範本就走克隆路徑，基礎映像欄位必須清空
    selected_lxc_template_id = 0
    if resource_type == "lxc":
        requested_lxc_template_id = safe_int(
            raw_prefill.get("lxc_template_id"), 0, minimum=0, extract_digits=True
        )
        candidate = catalog_by_id.get(requested_lxc_template_id)
        if candidate is not None and str(candidate.get("resource_type")) == "lxc":
            selected_lxc_template_id = requested_lxc_template_id

    selected_lxc_image = ""
    selected_lxc_label = ""
    if resource_type == "lxc" and not selected_lxc_template_id and lxc_os_images:
        requested_image = str(
            raw_prefill.get("lxc_os_image")
            or (form_context.lxc_os_image if form_context else "")
            or ""
        ).strip()
        selected_image = next(
            (item for item in lxc_os_images if item["value"] == requested_image),
            lxc_os_images[0],
        )
        selected_lxc_image = selected_image["value"]
        selected_lxc_label = str(selected_image.get("label") or "").strip()

    # VM 的候選 = 基礎映像 + 應用範本（後者在伺服器端不會出現在基礎映像清單裡）
    vm_candidates = list(vm_operating_systems) + [
        {
            "template_id": int(item.get("template_id") or 0),
            "label": item.get("name") or "",
        }
        for item in application_templates
        if str(item.get("resource_type")) != "lxc"
        and int(item.get("template_id") or 0)
        not in {int(row.get("template_id") or 0) for row in vm_operating_systems}
    ]

    selected_vm_template_id = 0
    selected_vm_os = ""
    if resource_type == "vm" and vm_candidates:
        requested_vm_template_id = safe_int(
            raw_prefill.get("vm_template_id")
            or (form_context.vm_template_id if form_context else 0),
            0,
            minimum=0,
            extract_digits=True,
        )
        selected_vm = next(
            (
                item
                for item in vm_candidates
                if int(item.get("template_id") or 0) == requested_vm_template_id
            ),
            None,
        ) or _default_vm_candidate(vm_candidates, wants_windows=request.needs_windows)
        selected_vm_template_id = int(selected_vm.get("template_id") or 0)
        selected_vm_os = str(selected_vm.get("label") or "").strip()

    # 模型挑的 GPU 只在需要 GPU 時採用；表單上已選的照舊保留
    form_gpu_mapping_id = str(
        (form_context.selected_gpu_mapping_id if form_context else "") or ""
    ).strip()
    model_gpu_mapping_id = (
        str(raw_prefill.get("gpu_mapping_id") or "").strip()
        if request.requires_gpu
        else ""
    )
    requested_gpu_mapping_id = model_gpu_mapping_id or form_gpu_mapping_id
    selected_gpu: dict[str, Any] | None = None
    if resource_type == "vm" and gpu_options:
        if requested_gpu_mapping_id:
            selected_gpu = next(
                (
                    option
                    for option in gpu_options
                    if str(option.get("mapping_id") or "").strip()
                    == requested_gpu_mapping_id
                    and int(option.get("available_count") or 0) > 0
                ),
                None,
            )
        if not selected_gpu and (request.requires_gpu or form_gpu_mapping_id):
            selected_gpu = _best_gpu_option(gpu_options)

    gpu_reason = ""
    gpu_candidates: list[dict[str, Any]] = []
    if resource_type == "vm" and gpu_options:
        for option in gpu_options[:3]:
            if int(option.get("available_count") or 0) <= 0:
                continue
            gpu_candidates.append(
                {
                    "mapping_id": str(option.get("mapping_id") or "").strip(),
                    "label": _gpu_option_label(option),
                    "reason": t("recommendation.gpuCandidateReason"),
                }
            )
    if selected_gpu:
        gpu_reason = t("recommendation.gpuReasonSelected")
    elif request.requires_gpu:
        gpu_reason = t("recommendation.gpuReasonInsufficient")

    selected_gpu_mapping_id = ""
    selected_gpu_label = ""
    if selected_gpu:
        selected_gpu_mapping_id = str(selected_gpu.get("mapping_id") or "").strip()
        selected_gpu_label = _gpu_option_label(selected_gpu)

    selected_template = catalog_by_id.get(
        selected_lxc_template_id if resource_type == "lxc" else selected_vm_template_id
    )
    spec = _rule_based_spec(request, resource_type)
    cores = safe_int(
        raw_prefill.get("cores") or (form_context.cores if form_context else None),
        spec["cores"],
        minimum=1,
        extract_digits=True,
    )
    memory_mb = safe_int(
        raw_prefill.get("memory_mb")
        or (form_context.memory_mb if form_context else None),
        spec["memory_mb"],
        minimum=512,
        extract_digits=True,
    )
    # 克隆出來的硬碟不能比範本本身小
    minimum_disk_gb = max(
        _minimum_disk_gb(resource_type),
        safe_int((selected_template or {}).get("disk_gb"), 0, minimum=0),
    )
    disk_gb = safe_int(
        raw_prefill.get("disk_gb") or (form_context.disk_gb if form_context else None),
        max(spec["disk_gb"], minimum_disk_gb),
        minimum=minimum_disk_gb,
        extract_digits=True,
    )
    username = ""
    if resource_type == "vm":
        username = (
            str(
                raw_prefill.get("username")
                or (form_context.username if form_context else "")
                or "student"
            ).strip()
            or "student"
        )

    mode = (
        str(
            raw_prefill.get("mode")
            or (form_context.mode if form_context else "")
            or "scheduled"
        )
        .strip()
        .lower()
    )
    if mode not in {"immediate", "scheduled"}:
        mode = "scheduled"

    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    start_at = form_context.start_at if form_context and form_context.start_at else None
    end_at = form_context.end_at if form_context and form_context.end_at else None
    if mode == "scheduled" and not (start_at and end_at):
        requested_start = _parse_datetime(raw_prefill.get("start_at"))
        requested_end = _parse_datetime(raw_prefill.get("end_at"))
        schedule_options = list(form_context.schedule_options) if form_context else []
        # 使用者在配置模式選過使用時間就照那個長度挑，不讓模型改
        selected_schedule = _schedule_for_duration(
            schedule_options, request.duration_days
        ) or next(
            (
                option
                for option in schedule_options
                if option.start_at == requested_start and option.end_at == requested_end
            ),
            schedule_options[0] if schedule_options else None,
        )
        if selected_schedule:
            start_at, end_at = selected_schedule.start_at, selected_schedule.end_at
    if mode == "immediate":
        start_at = None
        # 模型常回字串 "false"；bool("false") 是 True 會把結束時間吃掉，要用 safe_bool
        if safe_bool(
            raw_prefill.get("immediate_no_end"),
            bool(form_context.immediate_no_end) if form_context else True,
        ):
            end_at = None

    immediate_no_end = mode == "immediate" and end_at is None
    storage = (
        str(
            raw_prefill.get("storage")
            or (form_context.storage if form_context else "")
            or "local-lvm"
        ).strip()
        or "local-lvm"
    )

    service_name = " ".join(
        str(application_target.get("service_name") or request.goal).split()
    )[:40]

    form_prefill = {
        "resource_type": resource_type,
        "mode": mode,
        "hostname": hostname,
        "lxc_os_image": selected_lxc_image if resource_type == "lxc" else "",
        "lxc_template_id": selected_lxc_template_id if resource_type == "lxc" else 0,
        "vm_os_choice": selected_vm_os if resource_type == "vm" else "",
        "vm_template_id": selected_vm_template_id if resource_type == "vm" else 0,
        "gpu_mapping_id": selected_gpu_mapping_id if resource_type == "vm" else "",
        "start_at": start_at.isoformat() if start_at else "",
        "end_at": end_at.isoformat() if end_at else "",
        "immediate_no_end": immediate_no_end,
        # 表單還沒開、沒有可用時段可挑時，申請頁載入可用日期後照這個長度帶入
        "duration_days": request.duration_days,
        "cores": cores,
        "memory_mb": memory_mb,
        "disk_gb": disk_gb,
        "storage": storage,
        "username": username,
        "reason": _build_submission_reason(
            request=request,
            resource_type=resource_type,
            service_name=service_name,
            cores=cores,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
        ),
    }

    if resource_type == "lxc":
        source_label = (
            str(selected_template.get("name") or "")
            if selected_template
            else selected_lxc_label or selected_lxc_image
        )
    else:
        source_label = selected_vm_os
    summary = render_plan_summary(
        resource_type=resource_type,
        source_label=source_label,
        from_template=selected_template is not None,
        cores=cores,
        memory_mb=memory_mb,
        disk_gb=disk_gb,
        gpu_label=selected_gpu_label,
        gpu_missing=request.requires_gpu and not selected_gpu,
        fallback=fallback,
    )
    environment_reason = _environment_reason(
        request, resource_type, gpu_selected=bool(selected_gpu)
    )

    return {
        "persona": {
            "role": request.role,
            "preset": request.preset,
            "course_context": request.course_context,
            "sharing_scope": request.sharing_scope,
            "budget_mode": request.budget_mode,
            "resource_baseline": request.resource_baseline,
        },
        "device_profile": summarize_device_nodes(nodes),
        "summary": summary,
        "fallback": fallback,
        "workload_profile": "rule-based" if fallback else "ai-planned",
        "rule_basis": {
            "reasons": [
                reason for reason in (environment_reason, gpu_reason) if reason
            ],
        },
        "recommended_path": {
            "fit": "rule-based plan" if fallback else "ai-generated plan",
            "why": [t("recommendation.recommendedPathWhy")],
            "upgrade_when": "",
        },
        "final_plan": {
            "summary": summary,
            "fallback": fallback,
            "application_target": {
                "service_name": service_name,
                "execution_environment": resource_type,
                "environment_reason": environment_reason,
            },
            "form_prefill": form_prefill,
            "gpu_recommendation": {
                "should_use_gpu": bool(selected_gpu_mapping_id),
                "selected_gpu_mapping_id": selected_gpu_mapping_id,
                "selected_gpu_label": selected_gpu_label,
                "reason": gpu_reason,
                "candidates": gpu_candidates,
            },
        },
    }
