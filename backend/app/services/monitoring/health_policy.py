"""系統健康判定規則（純函式，不碰 DB／Redis／PVE，方便單元測試）。

狀態值：
- 元件：ok／down／disabled（功能關閉）／unknown（查不到，例如 Redis 掛了看不到 worker）
         ／attention（還在服務但需要處理，例如 Gateway 憑證快到期）
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
) -> SystemAlertDecision:
    """比對目前的問題與已開啟的系統告警：新問題開告警、已恢復的收掉。

    ``last_created``：target → 最近一次建立告警的 unix 時間，冷卻期內同一
    目標不重複開（避免任務在成功／失敗間跳動時洗信箱）。
    """
    open_set = set(open_targets)
    current = {finding.target: finding for finding in findings}
    new: list[SystemFinding] = []
    for target, finding in current.items():
        if target in open_set:
            continue
        last = last_created.get(target)
        if last is not None and now - last < cooldown_seconds:
            continue
        new.append(finding)
    resolved = sorted(target for target in open_set if target not in current)
    return SystemAlertDecision(new_findings=new, resolved_targets=resolved)


__all__ = [
    "CRITICAL_COMPONENTS",
    "FAILING_THRESHOLD",
    "GATEWAY_CERT_WARN_DAYS",
    "SystemAlertDecision",
    "SystemFinding",
    "build_findings",
    "evaluate_system_alerts",
    "gateway_status",
    "loop_status",
    "overall_status",
    "stale_after_seconds",
    "task_status",
]
