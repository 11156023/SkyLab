"""系統健康判定規則（純函式，不碰 DB／Redis／PVE，方便單元測試）。

狀態值：
- 元件：ok／down／disabled（功能關閉）／unknown（查不到，例如 Redis 掛了看不到 worker）
         ／attention（還在服務但需要處理，例如 Gateway 憑證快到期）
         ／pending（還沒有結果，例如 LiteLLM 背景健康檢查尚未跑完；不影響整體）
- 任務：ok／warning（剛失敗 1–2 次）／failing（連續失敗 ≥ FAILING_THRESHOLD）
         ／stale（太久沒跑）／pending（這次啟動後還沒跑過）
- 迴圈：ok／stale（leader 太久沒有 tick）／pending
- 整體：ok／degraded（有東西不正常但服務還在）／down（DB 或 Redis 掛了）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

FAILING_THRESHOLD = 3
# certbot 在到期前 30 天就會續期；剩不到 14 天代表續期一直失敗，要人處理
GATEWAY_CERT_WARN_DAYS = 14
# 任務每輪都會跑；超過「5 個間隔或 10 分鐘」沒有執行紀錄就視為停擺。
# 一輪 tick 依序跑十幾個任務，PVE 慢的時候單輪可能好幾分鐘，門檻不能太緊。
STALE_INTERVAL_MULTIPLIER = 5
STALE_MIN_SECONDS = 600.0

# DB 與 Redis 是核心依賴：任一掛掉，登入、限流、排程都會壞，整體算 down
CRITICAL_COMPONENTS = frozenset({"database", "redis"})


def stale_after_seconds(interval_seconds: float | None) -> float:
    interval = float(interval_seconds or 0)
    return max(interval * STALE_INTERVAL_MULTIPLIER, STALE_MIN_SECONDS)


def task_status(
    entry: Mapping[str, Any], *, interval_seconds: float | None, now: float
) -> str:
    last_run = entry.get("last_run_at")
    if not last_run:
        return "pending"
    failures = int(entry.get("consecutive_failures") or 0)
    if failures >= FAILING_THRESHOLD:
        return "failing"
    if now - float(last_run) > stale_after_seconds(interval_seconds):
        return "stale"
    if failures > 0:
        return "warning"
    return "ok"


def loop_status(entry: Mapping[str, Any], *, now: float) -> str:
    last_tick = entry.get("leader_last_tick_at")
    if not last_tick:
        return "pending"
    if now - float(last_tick) > stale_after_seconds(entry.get("interval_seconds")):
        return "stale"
    return "ok"


# warning（偶發失敗 1–2 次）只在卡片上標黃，不拉低整體狀態
_BAD_TASK_STATUSES = frozenset({"failing", "stale"})
_BAD_LOOP_STATUSES = frozenset({"stale"})


def gateway_status(
    probe: Mapping[str, Any], *, now: datetime
) -> tuple[str, str | None, str | None]:
    """Gateway 健康探測結果 → ``(元件狀態, 卡片上的說明, 告警訊息)``。

    ``probe`` 是 ``nginx_gateway_service.parse_health_output`` 的結構。
    服務停了或 nginx 設定壞掉算 down；只有憑證快到期／已過期算 attention
    （流量還在走，但再不處理 HTTPS 網址就會壞）。
    """
    problems: list[str] = []
    nginx = probe.get("nginx")
    if nginx != "active":
        problems.append(f"nginx 未執行（{nginx or 'unknown'}）")
    wireguard = probe.get("wireguard")
    if wireguard != "active":
        problems.append(f"WireGuard 未執行（{wireguard or 'unknown'}）")
    if probe.get("config_valid") is False:
        problems.append("nginx 設定未通過 nginx -t")
    if problems:
        detail = "；".join(problems)
        return "down", detail, f"Gateway 異常：{detail}"

    expiring: list[str] = []
    for cert in probe.get("certificates") or []:
        expires_at = cert.get("expires_at")
        if expires_at is None:
            continue
        days_left = (expires_at - now).total_seconds() / 86400
        if days_left < 0:
            expiring.append(f"{cert.get('name')} 已過期")
        elif days_left < GATEWAY_CERT_WARN_DAYS:
            expiring.append(f"{cert.get('name')} 剩 {int(days_left)} 天到期")
    if expiring:
        detail = "；".join(expiring)
        return (
            "attention",
            detail,
            f"Gateway 憑證需要處理（certbot 續期可能一直失敗）：{detail}",
        )
    return "ok", None, None


AI_GATEWAY_COMPONENT = "ai_gateway"
AI_GATEWAY_LABEL = "AI Gateway (LiteLLM)"
AI_MODEL_PREFIX = "ai_model:"


def ai_components(probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    """LiteLLM 探測結果 → 元件清單：gateway 一個，加上每個公開模型一個。

    ``probe``（由 system_health_service 組出來）：
    - ``reachable``：``/health/liveliness`` 有回應；``error``：連不到時的原因
    - ``db``：``/health/readiness`` 的 ``db`` 欄位（``connected`` 才正常）
    - ``models``：``/model/info`` 查到的公開 alias（``None`` = 查不到）
    - ``deployments``：alias → ``{"healthy": n, "unhealthy": n}``，來自 LiteLLM
      背景健康檢查（``None`` = ``/health`` 查不到）

    連不到或資料庫斷線算 down（金鑰驗證、用量紀錄都會失敗）。模型的所有部署都
    不健康算 down，只有部分不健康算 attention；背景健康檢查還沒跑出結果算
    pending（不拉低整體狀態、不發告警）。
    """
    if not probe.get("reachable"):
        detail = probe.get("error") or "無法連線"
        return [
            {
                "name": AI_GATEWAY_COMPONENT,
                "label": AI_GATEWAY_LABEL,
                "status": "down",
                "detail": detail,
                "alert_message": f"AI Gateway（LiteLLM）無法連線，AI API 與內建 AI 功能都無法使用：{detail}",
            }
        ]

    components: list[dict[str, Any]] = []
    db = probe.get("db")
    if db != "connected":
        detail = f"資料庫未連線（{db or 'unknown'}）"
        components.append(
            {
                "name": AI_GATEWAY_COMPONENT,
                "label": AI_GATEWAY_LABEL,
                "status": "down",
                "detail": detail,
                "alert_message": f"AI Gateway（LiteLLM）{detail}，API 金鑰驗證與用量紀錄會失敗",
            }
        )
    else:
        components.append(
            {"name": AI_GATEWAY_COMPONENT, "label": AI_GATEWAY_LABEL, "status": "ok", "detail": None}
        )

    deployments = probe.get("deployments")
    names = set(probe.get("models") or []) | set(deployments or {})
    for alias in sorted(names):
        counts = (deployments or {}).get(alias) or {}
        healthy = int(counts.get("healthy") or 0)
        unhealthy = int(counts.get("unhealthy") or 0)
        component: dict[str, Any] = {
            "name": f"{AI_MODEL_PREFIX}{alias}",
            "label": f"LLM · {alias}",
            "status": "ok",
            "detail": None,
        }
        if healthy + unhealthy == 0:
            component.update(status="pending", detail="等待 LiteLLM 健康檢查")
        elif unhealthy and healthy:
            component.update(
                status="attention",
                detail=f"{unhealthy}/{healthy + unhealthy} 個部署異常",
                alert_message=f"AI 模型 {alias} 有部分部署異常（{unhealthy}/{healthy + unhealthy}）",
            )
        elif unhealthy:
            component.update(
                status="down",
                detail="上游推論服務無回應",
                alert_message=f"AI 模型 {alias} 無法使用：LiteLLM 健康檢查連不到上游推論服務",
            )
        components.append(component)
    return components


def overall_status(
    components: Iterable[Mapping[str, Any]],
    loops: Iterable[Mapping[str, Any]],
    tasks: Iterable[Mapping[str, Any]],
) -> str:
    degraded = False
    for component in components:
        status = component.get("status")
        if status == "down" and component.get("name") in CRITICAL_COMPONENTS:
            return "down"
        if status in ("down", "unknown", "attention"):
            degraded = True
    if any(loop.get("status") in _BAD_LOOP_STATUSES for loop in loops):
        degraded = True
    if any(task.get("status") in _BAD_TASK_STATUSES for task in tasks):
        degraded = True
    return "degraded" if degraded else "ok"


# ─── 登入檢查（每次登入後的服務檢查畫面） ─────────────────────────────────

# 畫面上固定這幾項、依這個順序；學生／老師看到的是包裝過的文案，管理員看到真名
PREFLIGHT_CHECKS = ("database", "redis", "worker", "pve", "gateway", "ai")
# 憑證快到期這類 attention 仍在服務，不擋登入
_PREFLIGHT_PASS_STATUSES = frozenset({"ok", "attention"})


def _preflight_key(name: str) -> str | None:
    if name in ("database", "redis", "worker", "gateway"):
        return name
    if name == "pve" or name.startswith("pve:"):
        return "pve"
    if name == AI_GATEWAY_COMPONENT:
        return "ai"
    # 個別 AI 模型不列入：單一模型掛掉不該擋住所有人登入
    return None


def preflight_checks(components: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """系統健康元件 → 登入檢查的固定幾項，每項 ok／fail／skipped。

    同一項有多個元件（多個 PVE 連線）時任一失敗就算失敗；全部 disabled（沒設定
    Gateway、AI）或沒有對應元件算 skipped，不擋登入。unknown 算失敗——它只會在
    DB／Redis 自己掛掉、查不到其他元件時出現。
    """
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in PREFLIGHT_CHECKS}
    for component in components:
        key = _preflight_key(str(component.get("name") or ""))
        if key is None:
            continue
        grouped[key].append(
            {
                "label": str(component.get("label") or component.get("name")),
                "status": str(component.get("status") or "unknown"),
                "detail": component.get("detail"),
                "latency_ms": component.get("latency_ms"),
            }
        )

    checks: list[dict[str, Any]] = []
    for key in PREFLIGHT_CHECKS:
        items = grouped[key]
        active = [item for item in items if item["status"] != "disabled"]
        if not active:
            status = "skipped"
        elif all(item["status"] in _PREFLIGHT_PASS_STATUSES for item in active):
            status = "ok"
        else:
            status = "fail"
        checks.append({"key": key, "status": status, "components": items})
    return checks


# ─── 系統告警（AlertEvent scope=system）判定 ───────────────────────────────


@dataclass(frozen=True)
class SystemFinding:
    """目前有問題的一個目標；target 同時是 AlertEvent.target 的去重鍵。"""

    target: str
    value: float
    threshold: float
    message: str


def build_findings(
    components: Iterable[Mapping[str, Any]],
    loops: Iterable[Mapping[str, Any]],
    tasks: Iterable[Mapping[str, Any]],
) -> list[SystemFinding]:
    """由健康快照挑出該發告警的目標。

    - 任務連續失敗 ≥ FAILING_THRESHOLD、或太久沒跑
    - 背景迴圈（推播、WireGuard…）leader 停擺
    - 非核心元件掛掉（worker、PVE 連線、Gateway）；Redis 掛掉也發（排程還跑得動時）
    - 元件需要處理（attention，例如 Gateway 憑證快到期）
    DB 掛掉時告警本身寫不進 DB，只能從系統健康卡／Grafana 看到。

    元件可帶 ``alert_message`` 自訂告警文字；沒帶就用「<名稱> 無法連線」。
    """
    findings: list[SystemFinding] = []
    for task in tasks:
        status = task.get("status")
        name = f"{task.get('loop')}/{task.get('task')}"
        if status == "failing":
            failures = int(task.get("consecutive_failures") or 0)
            error = task.get("last_error") or ""
            findings.append(
                SystemFinding(
                    target=f"task:{name}",
                    value=float(failures),
                    threshold=float(FAILING_THRESHOLD),
                    message=f"排程任務 {name} 連續失敗 {failures} 次" + (f"：{error}" if error else ""),
                )
            )
        elif status == "stale":
            findings.append(
                SystemFinding(
                    target=f"task:{name}",
                    value=0.0,
                    threshold=float(FAILING_THRESHOLD),
                    message=f"排程任務 {name} 已超過 {int(stale_after_seconds(task.get('interval_seconds')) // 60)} 分鐘沒有執行",
                )
            )
    for loop in loops:
        if loop.get("status") == "stale":
            loop_name = str(loop.get("loop"))
            findings.append(
                SystemFinding(
                    target=f"loop:{loop_name}",
                    value=0.0,
                    threshold=1.0,
                    message=f"背景迴圈 {loop_name} 已停擺（沒有任何行程取得 leader 執行）",
                )
            )
    for component in components:
        if component.get("status") not in ("down", "attention"):
            continue
        name = str(component.get("name"))
        if name == "database":
            continue
        label = component.get("label") or name
        detail = component.get("detail") or ""
        message = component.get("alert_message") or (
            f"{label} 無法連線" + (f"：{detail}" if detail else "")
        )
        findings.append(
            SystemFinding(
                target=f"component:{name}",
                value=0.0,
                threshold=1.0,
                message=message,
            )
        )
    return findings


@dataclass(frozen=True)
class SystemAlertDecision:
    new_findings: list[SystemFinding]
    resolved_targets: list[str]


def evaluate_system_alerts(
    findings: Iterable[SystemFinding],
    *,
    open_targets: Iterable[str],
    last_created: Mapping[str, float],
    cooldown_seconds: float,
    now: float,
    current_targets: Iterable[str] | None = None,
) -> SystemAlertDecision:
    """比對目前的問題與已開啟的系統告警：新問題開告警、已恢復的收掉。

    ``last_created``：target → 最近一次建立告警的 unix 時間，冷卻期內同一
    目標不重複開（避免任務在成功／失敗間跳動時洗信箱）。

    ``current_targets``：這一輪實際看到的問題目標（未經連續出現確認）。
    ``findings`` 只用來開新告警；收掉告警要以實際看到的為準，否則行程重啟後
    確認計數歸零的第一輪，會把仍在發生的告警全部誤判為已恢復。
    未提供時退回用 ``findings`` 的目標。
    """
    open_set = set(open_targets)
    current = {finding.target: finding for finding in findings}
    still_present = set(current) if current_targets is None else set(current_targets)
    new: list[SystemFinding] = []
    for target, finding in current.items():
        if target in open_set:
            continue
        last = last_created.get(target)
        if last is not None and now - last < cooldown_seconds:
            continue
        new.append(finding)
    resolved = sorted(target for target in open_set if target not in still_present)
    return SystemAlertDecision(new_findings=new, resolved_targets=resolved)


__all__ = [
    "AI_GATEWAY_COMPONENT",
    "AI_MODEL_PREFIX",
    "CRITICAL_COMPONENTS",
    "FAILING_THRESHOLD",
    "GATEWAY_CERT_WARN_DAYS",
    "PREFLIGHT_CHECKS",
    "SystemAlertDecision",
    "SystemFinding",
    "ai_components",
    "build_findings",
    "evaluate_system_alerts",
    "gateway_status",
    "loop_status",
    "overall_status",
    "preflight_checks",
    "stale_after_seconds",
    "task_status",
]
