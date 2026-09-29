"""AI 對話服務 — vLLM Tool Calling（支援 Gemma-4 / Qwen3 等模型）

流程：
  1. 帶著工具定義向 vLLM 發出請求
  2. 若 AI 回傳 tool_calls，逐一執行：
     - PVE 工具：內部呼叫 collector，不走 HTTP
     - ssh_exec：由資料庫取得 VM IP 與 SSH key，經人工確認後 SSH 進入 VM 執行
  3. 將工具結果加回 messages，持續進行下一個 agent step
  4. 遇到人工確認時中斷；確認後由呼叫端帶著同一份 messages 恢復
  5. AI 產生最終回答後回傳 ChatResponse

設計重點：
  - 一次 chat 請求使用 request-local lazy PveToolContext；各工具只取所需資料，
    已取得的 node／storage／resource detail 在同一 request 內重用。
  - 工具可連續呼叫多輪，但有固定上限，避免模型陷入無限工具迴圈。
  - ssh_exec 一律需要人工確認；同一輪多個 ssh_exec 時只有第一個 pending，
    其餘 deferred，確認後接續時再逐一產生下一個待確認指令。
  - 若呼叫端提供 VMID 範圍，工具輸出與 SSH 執行都只允許該範圍。
  - Gemma-4/Qwen3 的 <think> 與 tool call 標記會在每個 agent step 前清除，
    避免 message history 污染導致 LLM 無法正確總結。
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlmodel import Session

from app.ai.monitoring import new_ai_request_id, record_ai_template_call, usage_metrics
from app.ai.pve_log.collector import PveToolContext
from app.ai.pve_log.config import settings
from app.ai.pve_log.history import (
    PveHistoryValidationError,
    merge_pve_messages,
)
from app.ai.pve_log.schemas import ChatResponse, SSHExecRequest, ToolCallRecord
from app.core.i18n import t
from app.infrastructure.ai.pve_log import client as vllm_client

logger = logging.getLogger(__name__)
_MAX_TOOL_ROUNDS = 6

# ---------------------------------------------------------------------------
# 系統提示詞
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是 SkyLab PVE 管理助手，專門協助管理員查詢 Proxmox VE 虛擬化平台的資源狀態。

任務範圍原則：
- 將每一輪使用者訊息視為一個明確任務，只處理使用者實際詢問的問題；問題 A 不得自行
  擴張成問題 B。發現異常後，可以在同一問題範圍內使用 tools 做必要的下一步診斷，
  但不得順帶檢查無關資源或問題。
- 查詢不得自行擴張成修復或變更操作。除非使用者明確要求執行處置，否則只允許查詢、
  診斷與說明；需要確認的工具仍必須交由後端確認流程處理。
- 只取得完成本輪任務必要的資料。問題只涉及節點時不查 VM/LXC；只涉及 VM/LXC 時
  不補查叢集或儲存；指定 VMID、節點、資源類型或狀態時，工具參數與回答必須維持該範圍。
- 即使工具回傳全域資料，也只呈現與問題直接相關的欄位與項目。除非使用者明確要求完整
  清單或完整健檢，否則不要列出正常資源，也不要複述完整工具結果。

工具使用原則：
- 問題只涉及一種資源時，優先呼叫最精確的工具（例如只查儲存空間就用 get_storage，不要呼叫 get_resources）。
- 需要特定 VM/LXC 詳情時才呼叫 get_resource_detail，並傳入正確的 vmid。
- 若問題同時涉及多類資料，可以在同一輪呼叫多個工具。
- 不要為了讓回答看起來完整而額外呼叫工具。

異常判定：
- 節點只有在 status 顯示離線，或節點 CPU、記憶體、磁碟使用率達 100% 滿載時，才視為異常；
  不得自行套用其他使用率門檻。
- VM/LXC 的 stopped、關機，以及 CPU、記憶體或磁碟滿載都視為正常狀態，不得因此列為異常。
- VM/LXC 只有在工具明確回傳上述正常狀態以外的額外錯誤或故障訊號時，才列為異常。
- 偵測到異常時，可以繼續呼叫與該異常直接相關的 tools，以確認影響範圍、取得必要證據
  或縮小原因；取得足夠證據後停止，不做無關或重複檢查。
- 工具本身收集失敗或資料缺漏時，應說明無法完整判斷，不得把收集錯誤算成特定 VM/LXC
  的異常，也不得把缺少資料解讀為正常。
- 只根據工具實際回傳內容下結論。沒有直接證據的原因一律標示為尚未確認，不得將可能原因
  寫成確定原因。
- 異常判定規則是內部回答準則，不得在一般結果中解釋或附註，例如不要輸出「stopped 視為
  正常，故未列入異常」。只有使用者明確詢問判定標準時才說明。

SSH 工具（ssh_exec）使用原則：
- **優先使用 PVE API 工具**，PVE API 已可取得 CPU、記憶體、磁碟、網路的即時使用率。
- 只有在 PVE API 無法取得足夠細節時，才使用 ssh_exec。
- **適合 SSH 的場景**：程序列表（ps aux）、服務狀態（systemctl status）、
  詳細日誌（journalctl）、Python 環境查詢、自訂腳本執行、
  應用層資訊（nginx、docker、資料庫等）。
- **指令風格**：保持簡單實用，優先使用單行指令；Python 片段以 python3 -c '...' 格式。
- **必填 reason**：每次呼叫 ssh_exec 必須在 reason 欄位說明執行目的，
  讓使用者在確認對話中做出知情決策。
- 需要 ssh_exec 時直接呼叫工具，不要先用文字詢問使用者是否同意，也不要在回覆中只展示
  指令等待使用者再次要求。後端會自動判定直接執行、等待確認或 hard-deny。
- 被攔截的危險指令（如 rm -rf）無法執行。

Guest 廣度診斷工具（get_guest_diagnostic_summary）使用原則：
- 「現在怎麼了／快速檢查／狀態不明／為什麼很慢」等針對單一 VM/LXC 的廣度問題，
  優先呼叫 get_guest_diagnostic_summary，一次取得 PVE 資源、systemd 服務、
  Top processes 與最近一小時 warning/error 記錄。
- 彙總結果已包含 PVE resource detail；已有彙總結果時，不要重複呼叫 get_resource_detail。
- 只有針對彙總結果發現的特定 service、process 或 log 線索，才繼續用 ssh_exec 深入；
  廣度收集不要用 ssh_exec 逐條重做。
- section 標記 unavailable 或 error 時，該層必須標「❓ 未取得」或說明資料不足，
  不得把缺少資料寫成正常；PVE disk 數值不等於 Guest 內檔案系統使用率，
  未取得 df 時不得宣稱 VM 內檔案系統空間正常。
- 使用此工具不代表必須輸出完整報告；仍依使用者本輪問題，套用下方「回覆格式」。
  快速檢查或追問原因時，只摘要相關發現；只有明確要求完整健檢、完整診斷或逐層檢查時才展開分層表格。
  表格內狀態標記只用 ✅ 正常、⚠️ 注意、❌ 異常、❓ 未取得；
  分層標籤固定 [PVE] [VM] [OS] [Service] [Application]，保留前端可辨識的格式。
- 整體狀態判定：有 failed service、VM stopped 或明確錯誤證據 → ❌ 異常；沒有故障但
  資源偏高或出現 warning → ⚠️ 注意；沒有異常證據但必要 section 未取得 → ❓ 資料不足；
  必要資料皆取得且無明顯異常 → ✅ 未發現明顯異常。
- 證據只放支持結論的數值、failed item 或 log 摘要；同一資訊不要在分層表格與正文重複列出。
- 一般回答最多顯示 3 個 failed services、3 個 processes、3 筆 log 證據，其餘以數量摘要；
  預設不貼完整 JSON、完整 service/process list、完整 journal 或內部固定指令；不顯示推理過程，
  只說結論、證據、限制與下一步。
- 沒有實際資料時不得寫「正常」「已確認」或給出特定數值。

回覆格式：
- 使用繁體中文，面向系統管理員。第一句直接指出目標節點、VMID 或服務及本輪結論；
  不加問候、開場白或「以下是分析結果」，不重述使用者問題。
- 依使用者問的是狀態、清單還是原因決定格式，不依呼叫的工具決定篇幅。追問只回答新增的
  問題與證據，不重新貼上一輪報告；若使用者指定欄位或詳細程度，優先遵循。
- 狀態查詢：直接用一至三句回答，不建立標題、分層表格或建議段落。沒有足夠資料時，
  簡短說明哪些資料未取得、因此哪一項無法判斷，不把資料不足寫成正常。
- 清單或排名：一句摘要加精簡表格（僅一兩項時可用條列），只保留辨識對象所需資訊與
  使用者要求的欄位、範圍、排序及筆數。資料不足要求的筆數時說明實際取得數量，不補造項目；
  不在表格前後重複逐項描述，也不附帶未要求的健檢或改善計畫。
- 故障原因或快速診斷：依「結論 → 主要證據 → 建議下一步」呈現。
  結論最多三句，可用粗體強調；主要證據最多三點；建議下一步最多三項，按優先順序排列。
  短答可用一段結論加精簡條列，不強制每段都有標題；需要分段時沿用「## 診斷結論」、
  「## 主要證據」、「## 建議下一步」。沒有必要的建議時省略，不湊滿項目。
- 完整健檢：只有使用者明確要求完整健檢、完整診斷或逐層檢查，才依序展開
  「## 診斷結論」→「## 分層結果」→「## 主要證據」→「## 建議下一步」。
  分層表格可包含已取得證據的正常項目；主要證據最多五點，僅補充表格未呈現且支持結論的細節。
  有影響判斷的資料缺口時加「## 資料缺口」，沒有缺口或沒有補充證據時省略該段。
- 異常盤點只列異常項目；同一項狀態、異常與證據不得換句話重複。若「狀況」與「異常」
  會表達相同內容，只保留「異常」。必要資料皆取得且未發現異常時，用一句話回答。
  偵測到異常時，可補「**建議操作：**」，最多三項，限於與該異常直接相關的下一步。
  正常項目只在完整清單或健檢中展開；僅有助於排除本輪原因時才簡短提及。
- 將已確認事實與推測分開：可能原因一律標「待確認」，並指出需要哪項證據才能驗證。
  故障影響也必須有證據；單一服務啟動失敗，不足以斷言整個網站或所有使用者都無法存取。
  未經執行結果證實，不得宣稱已修復；指令執行成功與服務恢復正常須分別依據實際結果說明。
- 正文不逐項描述查詢過程，不列工具函式名稱或重複工具紀錄。只呈現有助於判斷的資料；
  原始 JSON、完整日誌、程序清單與堆疊內容僅在使用者明確要求且與問題相關時提供，
  仍須隱去密碼、金鑰及 token。一般回答只引用必要的短日誌摘要。
- 原因查詢先提供診斷建議；修復或變更須有使用者明確要求，並維持既有指令確認流程。
  未詢問原因、建議或操作時，不主動加入其他延伸段落，異常盤點的必要建議除外。
- 請用 Markdown 格式輸出；表格只用於清單、比較或完整分層診斷，不為固定版面硬塞。
- 數字單位換算為人類可讀格式：bytes → GB / MB、比例 → %（保留一位小數）。
- 若問題與 PVE 無關，說明你只處理 PVE 相關查詢。\
"""

# ---------------------------------------------------------------------------
# Tool 定義（OpenAI function-calling 格式）
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_resources",
            "description": (
                "取得所有 VM 與 LXC 容器的摘要清單。"
                "可依節點名稱、資源類型（qemu/lxc）、狀態（running/stopped）篩選。"
                "回傳：vmid、名稱、類型、節點、狀態、CPU/記憶體/磁碟使用率等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "篩選特定節點名稱（可選，不填則回傳所有節點）",
                    },
                    "resource_type": {
                        "type": "string",
                        "enum": ["qemu", "lxc"],
                        "description": "篩選資源類型：qemu（VM）或 lxc（容器）（可選）",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["running", "stopped"],
                        "description": "篩選狀態（可選）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nodes",
            "description": (
                "取得所有 PVE 節點的清單，包含每個節點的"
                "CPU 使用率、核心數、記憶體使用量、磁碟使用量、開機時間。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_storage",
            "description": (
                "取得所有儲存空間資訊，包含容量、已用空間、使用率、類型。可依節點篩選。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {
                        "type": "string",
                        "description": "篩選特定節點的儲存空間（可選）",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_resource_detail",
            "description": (
                "取得指定 vmid 的完整詳細資訊，包含："
                "摘要、即時狀態（CPU/記憶體/磁碟讀寫/網路流量）、"
                "設定檔（CPU 核心數、記憶體大小、磁碟大小、是否開機自啟）、"
                "LXC 網路介面（IP 位址）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "VM 或 LXC 的 ID",
                    },
                },
                "required": ["vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cluster",
            "description": "取得叢集整體概覽：叢集名稱、是否為多節點叢集、節點數、quorum 狀態。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_guest_diagnostic_summary",
            "description": (
                "對指定 VMID 一次取得 Guest 廣度診斷摘要：PVE 資源詳情、systemd 服務統計與"
                " failed services、CPU/RAM Top 10 processes、最近一小時最多 100 筆"
                " warning/error 系統記錄。適合回答「這台 VM 現在怎麼了」等廣度問題；"
                "不需要也不允許傳入任何 shell 指令。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "目標 VM 或 LXC 的 VMID",
                    },
                },
                "required": ["vmid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": (
                "透過 SSH 連線到指定 VMID 的 VM/LXC，執行遠端指令取得內部系統細節或執行管理操作。"
                "可執行任意 shell 指令或 Python 腳本片段。"
                "PVE API 工具無法提供足夠細節時才使用（如程序列表、服務狀態、日誌、Python 環境等）。"
                "模型應直接呼叫本工具，不得先用自然語言詢問是否同意。"
                "後端會判定直接執行或回傳待確認；危險指令會被黑名單直接攔截。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vmid": {
                        "type": "integer",
                        "description": "目標 VM 或 LXC 的 VMID",
                    },
                    "command": {
                        "type": "string",
                        "description": (
                            "要在遠端執行的指令（保持簡單實用）。"
                            "範例：ps aux | grep python、df -h、free -m、"
                            "systemctl status nginx、journalctl -n 50 --no-pager、"
                            "python3 -c 'import sys; print(sys.version)'"
                        ),
                    },
                    "ssh_user": {
                        "type": "string",
                        "description": "SSH 登入帳號（預設 root）",
                    },
                    "ssh_port": {
                        "type": "integer",
                        "description": "SSH 埠號（預設 22）",
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "說明為何需要執行此指令（必填），顯示給使用者作為確認依據。"
                            "例如：查詢 VM 101 內的 Python 程序列表、"
                            "取得 nginx 服務運行狀態"
                        ),
                    },
                },
                "required": ["vmid", "command", "reason"],
            },
        },
    },
]

_ALLOWED_TOOL_NAMES = frozenset(
    tool.get("function", {}).get("name")
    for tool in _TOOLS
    if isinstance(tool.get("function"), dict)
)
_SCOPE_PROMPT = (
    "本次對話僅可讀取與操作指定範圍內的 VM/LXC，不得查詢或操作範圍外的 VMID。"
)

# ---------------------------------------------------------------------------
# Tool 執行器
# ---------------------------------------------------------------------------


def _execute_tool_sync(
    context: PveToolContext,
    name: str,
    args: dict[str, Any],
    *,
    allowed_vmids: set[int] | None = None,
) -> Any:
    """執行 PVE 查詢工具，同步版本（供 asyncio.to_thread 包裝）。"""
    result = context.execute(name, args, allowed_vmids=allowed_vmids)
    if context.errors:
        # Preserve successful fields, but never let empty/partial data look like
        # evidence that the cluster is healthy or a resource is absent.
        # Raw collection errors may mention resources outside the caller's scope.
        warning = "部分快照資料未能取得；缺少資料不代表資源正常或不存在。"
        if isinstance(result, dict):
            return {**result, "error": result.get("error") or warning}
        return {"data": result, "error": warning}
    return result


async def _execute_ssh_tool(
    args: dict[str, Any],
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
    requester_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """執行 ssh_exec 工具（async）。

    一律以 require_confirm=True 建立請求：結果只會是 pending（等待使用者
    確認）或被攔截，不會直接執行。黑名單永遠先執行。
    """
    from app.ai.pve_log.ssh_exec import ssh_exec as _ssh_exec

    try:
        vmid = int(args["vmid"])
        command = str(args["command"])
    except (KeyError, ValueError, TypeError) as e:
        return {"error": t("pveLog.missingRequiredParams", error=e), "pending": False}

    if allowed_vmids is not None and vmid not in allowed_vmids:
        return {
            "vmid": vmid,
            "host": "",
            "ssh_user": str(args.get("ssh_user", "root")),
            "command": command,
            "blocked": True,
            "block_reason": t("pveLog.scopeRestricted"),
            "pending": False,
        }

    req = SSHExecRequest(
        vmid=vmid,
        command=command,
        ssh_user=str(args.get("ssh_user", "root")),
        ssh_port=int(args.get("ssh_port", 22)),
        require_confirm=True,
    )
    result = await _ssh_exec(
        req,
        session=session,
        allowed_vmids=allowed_vmids,
        requester_id=requester_id,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    data = result.model_dump(mode="json")
    # 補充 reason 給前端顯示（AI 提供的說明）
    data["reason"] = str(args.get("reason", t("pveLog.reasonNotProvided")))
    return data


async def _execute_guest_diagnostics_tool(
    args: dict[str, Any],
    *,
    context: PveToolContext | None,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> dict[str, Any]:
    """執行 get_guest_diagnostic_summary 工具（async）。

    執行順序（依固定契約）：
      1. 驗證 vmid 型別與 allowed_vmids（PVE/SSH 前即拒絕）。
      2. 重用 request-local PveToolContext 取得 PVE resource detail。
      3. PVE 顯示 stopped → guest sections 全部 unavailable，不嘗試 SSH。
      4. PVE 失敗但未確認 stopped → 仍嘗試 Guest 收集。
      5. server-owned batch runner 一次連線執行固定 probes。
      6. 各 section 獨立解析、組固定 JSON。
    """
    from app.ai.pve_log.guest_diagnostics import (
        ERROR_CODE_CONNECTION_FAILED,
        ERROR_CODE_RESOLVE_FAILED,
        ERROR_CODE_SCOPE_RESTRICTED,
        GUEST_DIAGNOSTIC_PROBES,
        build_guest_diagnostics_result,
        build_resource_section,
        empty_guest_section,
        parse_guest_probe_results,
        resource_detail_says_stopped,
    )
    from app.ai.pve_log.ssh_exec import run_guest_probe_batch

    started = time.monotonic()
    try:
        vmid = int(args["vmid"])
    except (KeyError, TypeError, ValueError):
        return build_guest_diagnostics_result(
            vmid=None,
            collected_at=datetime.now(timezone.utc),
            collection_duration_ms=int((time.monotonic() - started) * 1000),
            resource={
                "collection_status": "error",
                "data": None,
                "error_code": "vmidInvalid",
            },
            sections={
                name: empty_guest_section(name, "error", error_code="vmidInvalid")
                for name in ("services", "processes", "recent_logs")
            },
            warnings=[t("pveLog.guestDiagVmidInvalid")],
        )

    warnings: list[str] = []
    if allowed_vmids is not None and vmid not in allowed_vmids:
        return build_guest_diagnostics_result(
            vmid=vmid,
            collected_at=datetime.now(timezone.utc),
            collection_duration_ms=int((time.monotonic() - started) * 1000),
            resource=build_resource_section(
                None,
                status="error",
                error_code=ERROR_CODE_SCOPE_RESTRICTED,
            ),
            sections={
                name: empty_guest_section(
                    name, "error", error_code=ERROR_CODE_SCOPE_RESTRICTED
                )
                for name in ("services", "processes", "recent_logs")
            },
            warnings=[t("pveLog.scopeRestricted")],
        )

    # ── PVE resource detail（重用 request-local context）─────────────────
    resource_detail: dict[str, Any] | None = None
    vm_stopped = False
    if context is None:
        resource_section = build_resource_section(
            None, status="error", error_code="contextUnavailable"
        )
        warnings.append(t("pveLog.guestDiagWarnPveDetailFailed"))
    else:
        try:
            detail = await asyncio.to_thread(
                context.execute,
                "get_resource_detail",
                {"vmid": vmid},
                allowed_vmids=allowed_vmids,
            )
        except Exception as exc:
            logger.error("Guest 診斷 PVE detail 失敗 vmid=%d：%s", vmid, exc)
            detail = {"error": "collectorFailed"}
        if isinstance(detail, dict) and detail.get("error"):
            resource_section = build_resource_section(
                None, status="error", error_code="pveResourceUnavailable"
            )
            warnings.append(t("pveLog.guestDiagWarnPveDetailFailed"))
        else:
            resource_detail = detail if isinstance(detail, dict) else None
            resource_section = build_resource_section(resource_detail, status="ok")
            vm_stopped = resource_detail_says_stopped(resource_detail)

    # ── Guest probes（固定指令，模型不可控）──────────────────────────────
    sections: dict[str, dict[str, Any]]
    if vm_stopped:
        sections = {
            name: empty_guest_section(name, "unavailable")
            for name in ("services", "processes", "recent_logs")
        }
        warnings.append(t("pveLog.guestDiagWarnVmStopped"))
    else:
        probe_results = await run_guest_probe_batch(
            vmid,
            GUEST_DIAGNOSTIC_PROBES,
            session=session,
            allowed_vmids=allowed_vmids,
        )
        sections = parse_guest_probe_results(probe_results, warnings=warnings)
        if all(
            result.error_code
            in {ERROR_CODE_RESOLVE_FAILED, ERROR_CODE_CONNECTION_FAILED}
            for result in probe_results.values()
        ):
            warnings.append(t("pveLog.guestDiagWarnGuestCollectFailed"))

    return build_guest_diagnostics_result(
        vmid=vmid,
        collected_at=datetime.now(timezone.utc),
        collection_duration_ms=int((time.monotonic() - started) * 1000),
        resource=resource_section,
        sections=sections,
        warnings=warnings,
    )


def _deferred_ssh_result(args: dict[str, Any]) -> dict[str, Any]:
    try:
        vmid = int(args["vmid"])
        command = str(args["command"])
    except (KeyError, TypeError, ValueError):
        return {
            "pending": False,
            "deferred": True,
            "error": t("pveLog.deferredSshPending"),
        }
    return {
        "vmid": vmid,
        "command": command,
        "pending": False,
        "deferred": True,
        "error": t("pveLog.deferredSshPending"),
    }


def _next_deferred_ssh_call(
    messages: list[dict[str, Any]],
) -> tuple[int, str, dict[str, Any]] | None:
    """Find the next server-deferred SSH call in an existing tool-call round."""
    for message_index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        try:
            content = json.loads(str(message.get("content", "")))
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(content, dict) or not content.get("deferred"):
            continue

        tool_call_id = message.get("tool_call_id")
        for assistant in reversed(messages[:message_index]):
            if assistant.get("role") != "assistant":
                continue
            for tool_call in assistant.get("tool_calls") or []:
                if tool_call.get("id") != tool_call_id:
                    continue
                function = tool_call.get("function") or {}
                if function.get("name") != "ssh_exec":
                    return None
                return (
                    message_index,
                    str(tool_call_id),
                    _parse_tool_arguments(function.get("arguments") or "{}"),
                )
    return None


def _normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Normalize native and Qwen text-encoded tool calls into one message shape."""
    assistant_msg = dict(message)
    raw_content = assistant_msg.get("content") or ""

    if not assistant_msg.get("tool_calls") and "call:" in raw_content:
        match = re.search(
            r"<\|?tool_call\|?>\s*call:([a-zA-Z0-9_]+)\s*(\{.+?\})\s*"
            r"<\|?/?tool_call\|?>",
            raw_content,
            flags=re.DOTALL,
        )
        if not match:
            match = re.search(
                r"<\|?tool_call\|?>\s*call:([a-zA-Z0-9_]+)\s*(\{.+\})",
                raw_content,
                flags=re.DOTALL,
            )
        if match:
            func_name = match.group(1)
            args_fixed = match.group(2).replace('<|"|>', '"')
            args_fixed = re.sub(
                r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)",
                r'\1"\2"\3',
                args_fixed,
            )
            try:
                parsed_args = json.loads(args_fixed)
                assistant_msg["tool_calls"] = [
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": json.dumps(parsed_args, ensure_ascii=False),
                        },
                    }
                ]
                logger.info(
                    "成功手動解析 Qwen tool call: %s(%s)", func_name, parsed_args
                )
            except (TypeError, json.JSONDecodeError) as exc:
                logger.error(
                    "手動解析 Qwen tool call 失敗: %s, 修正後: %s",
                    exc,
                    args_fixed,
                )

    if not assistant_msg.get("tool_calls"):
        return assistant_msg

    cleaned = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL)
    cleaned = re.sub(
        r"<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{.+?\}\s*"
        r"<\|?/?tool_call\|?>",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{.+\}",
        "",
        cleaned,
        flags=re.DOTALL,
    )
    cleaned = re.sub(
        r"<\|tool_call\|>.*?<\|/tool_call\|>", "", cleaned, flags=re.DOTALL
    )
    cleaned = re.sub(r"<\|tool_call>.*?<tool_call\|>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r'```json\s*\{\s*"tool_call".*?```', "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<\|[^>]*\|>", "", cleaned)
    return {**assistant_msg, "content": cleaned.strip() or None}


def _canonicalize_model_tool_calls(
    message: dict[str, Any],
    *,
    reserved_ids: set[str],
    allowed_names: frozenset[str] | set[str] = _ALLOWED_TOOL_NAMES,
) -> dict[str, Any]:
    """Canonicalize model tool calls before adding them to history.

    回傳給前端的 messages 下一輪會原樣送回並經 history._canonical_tool_calls
    驗證；這裡必須套用同一套規則，否則一次「可執行」的 tool call（未知工具名、
    寬鬆解析的非 JSON arguments、缺 function object）會讓之後每一輪都 422。
    - 每個 call 給唯一 id、type 固定為 function
    - 丟棄缺 function object 或工具名稱不被允許的 call（全部丟棄時移除 tool_calls）
    - arguments 以 _parse_tool_arguments 解析後重新序列化為嚴格 JSON
    """
    raw_calls = message.get("tool_calls")
    if not raw_calls:
        return message
    if not isinstance(raw_calls, list):
        logger.warning("模型回傳的 tool_calls 不是陣列，已忽略：%r", raw_calls)
        return {key: value for key, value in message.items() if key != "tool_calls"}

    calls: list[dict[str, Any]] = []
    used_ids = set(reserved_ids)
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            logger.warning("模型回傳的 tool call 不是 object，已忽略：%r", raw_call)
            continue
        function = raw_call.get("function")
        if not isinstance(function, dict):
            logger.warning("模型回傳的 tool call 缺少 function object，已忽略：%r", raw_call)
            continue
        name = function.get("name")
        if not isinstance(name, str) or name not in allowed_names:
            logger.warning("模型呼叫不被允許的工具 %r，已忽略", name)
            continue
        call = dict(raw_call)
        raw_id = call.get("id")
        call_id = raw_id.strip() if isinstance(raw_id, str) else ""
        while not call_id or call_id in used_ids:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
        call["id"] = call_id
        call["type"] = "function"
        call["function"] = {
            **function,
            "name": name,
            "arguments": json.dumps(
                _parse_tool_arguments(function.get("arguments") or "{}"),
                ensure_ascii=False,
                separators=(",", ":"),
                # ast.literal_eval 退路可能產生 set/bytes 等非 JSON 值
                default=str,
            ),
        }
        used_ids.add(call_id)
        calls.append(call)
    if not calls:
        return {key: value for key, value in message.items() if key != "tool_calls"}
    return {**message, "tool_calls": calls}


def _validate_confirmation_history(
    messages: list[dict[str, Any]],
    *,
    requester_id: uuid.UUID | None,
    scope_type: str | None,
    scope_id: uuid.UUID | None,
    allowed_vmids: set[int] | None,
) -> None:
    """Validate and consume server-owned SSH results in a resumed history."""
    from app.ai.pve_log.ssh_exec import (
        consume_completed_confirmation,
        find_completed_confirmation_by_tool_call,
        peek_completed_confirmation,
    )

    tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
    for item in messages:
        if item.get("role") != "assistant":
            continue
        for tool_call in item.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            call_id = tool_call.get("id")
            function = tool_call.get("function")
            if isinstance(call_id, str) and isinstance(function, dict):
                tool_calls[call_id] = (
                    str(function.get("name") or ""),
                    _parse_tool_arguments(function.get("arguments") or "{}"),
                )

    tokens: list[str] = []

    def _validate_record(
        item: dict[str, Any],
        content: dict[str, Any],
        record: dict[str, Any],
        *,
        token_present: bool,
    ) -> dict[str, Any]:
        tool_call_id = item.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            raise PveHistoryValidationError(
                "PVE confirmation result 缺少有效的 tool_call_id"
            )
        if (
            record.get("requester_id") != requester_id
            or record.get("scope_type") != scope_type
            or record.get("scope_id") != scope_id
        ):
            raise PveHistoryValidationError("PVE confirmation token 與目前 scope 不符")
        stored_vmids = record.get("allowed_vmids")
        if (allowed_vmids is None) != (stored_vmids is None) or (
            allowed_vmids is not None
            and stored_vmids is not None
            and set(allowed_vmids) != set(stored_vmids)
        ):
            raise PveHistoryValidationError(
                "PVE confirmation token 與目前 VM scope 不符"
            )
        if record.get("tool_call_id") != tool_call_id:
            raise PveHistoryValidationError(
                "PVE confirmation token 與 assistant tool-call 不符"
            )

        call = tool_calls.get(tool_call_id)
        request = record.get("request")
        if (
            call is None
            or call[0] != "ssh_exec"
            or request is None
            or not hasattr(request, "vmid")
            or not hasattr(request, "command")
        ):
            raise PveHistoryValidationError(
                "PVE confirmation result 缺少對應的 ssh_exec tool-call"
            )
        call_args = call[1]
        # 與 _execute_ssh_tool 建立 SSHExecRequest 時的轉型一致，否則模型給
        # "vmid": "101" 之類字串參數時，已確認的結果會在接續時被誤判不符。
        try:
            call_vmid = int(call_args["vmid"])
            call_command = str(call_args["command"])
            call_ssh_user = str(call_args.get("ssh_user", "root"))
            call_ssh_port = int(call_args.get("ssh_port", 22))
        except (KeyError, TypeError, ValueError) as exc:
            raise PveHistoryValidationError(
                "PVE confirmation result 與原始 ssh_exec 參數不符"
            ) from exc
        if (
            call_vmid != request.vmid
            or call_command != request.command
            or call_ssh_user != getattr(request, "ssh_user", "root")
            or call_ssh_port != getattr(request, "ssh_port", 22)
        ):
            raise PveHistoryValidationError(
                "PVE confirmation result 與原始 ssh_exec 參數不符"
            )

        expected = record.get("result")
        if not isinstance(expected, dict):
            raise PveHistoryValidationError("PVE confirmation result server state 無效")
        candidate = dict(content)
        candidate.pop("confirmation_token", None)
        candidate.pop("confirmation_decision", None)
        allowed_extra = {"reason"}
        if set(candidate) - set(expected) - allowed_extra:
            raise PveHistoryValidationError("PVE confirmation result 含有未授權欄位")
        if any(candidate.get(key) != value for key, value in expected.items()):
            raise PveHistoryValidationError(
                "PVE confirmation result 與 server result 不符"
            )

        if not token_present and not record.get("consumed"):
            raise PveHistoryValidationError(
                "PVE confirmation result 必須帶 server confirmation token"
            )
        return candidate

    for item in messages:
        if item.get("role") != "tool":
            continue
        try:
            content = json.loads(str(item.get("content", "")))
        except (TypeError, json.JSONDecodeError):
            continue
        tool_call_id = item.get("tool_call_id")
        if not isinstance(content, dict):
            if isinstance(
                tool_call_id, str
            ) and find_completed_confirmation_by_tool_call(tool_call_id):
                raise PveHistoryValidationError(
                    "PVE confirmation result 必須是 server 產生的 JSON object"
                )
            continue

        token_present = "confirmation_token" in content
        if token_present:
            token = content.get("confirmation_token")
            record = (
                peek_completed_confirmation(str(token))
                if isinstance(token, str) and token
                else None
            )
            if record is None or record.get("consumed"):
                raise PveHistoryValidationError(
                    "PVE 對話 history 的 confirmation token 無效、已過期或已重放"
                )
            candidate = _validate_record(
                item,
                content,
                record,
                token_present=True,
            )
            item["content"] = json.dumps(
                candidate,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            tokens.append(str(token))
            continue

        if not isinstance(tool_call_id, str):
            continue
        record = find_completed_confirmation_by_tool_call(tool_call_id)
        if record is None:
            continue
        _validate_record(item, content, record, token_present=False)
        if not record.get("consumed"):
            token = record.get("token")
            if not isinstance(token, str):
                raise PveHistoryValidationError(
                    "PVE confirmation server state 缺少 token"
                )
            tokens.append(token)

    for token in tokens:
        if consume_completed_confirmation(token) is None:
            raise PveHistoryValidationError("PVE confirmation result 已被其他請求使用")


def _parse_tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}

    args_str = value.strip() or "{}"
    try:
        parsed = json.loads(args_str)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        args_str = args_str.replace('<|"|>', '"').replace("'", '"')
        args_str = re.sub(
            r"([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)",
            r'\1"\2"\3',
            args_str,
        )

    try:
        parsed = json.loads(args_str)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(args_str)
        except (SyntaxError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# 主對話函式
# ---------------------------------------------------------------------------


def _record_chat_usage(
    response_data: dict[str, Any],
    *,
    session: Session | None,
    requester_id: uuid.UUID | None,
    request_id: str,
    started: float,
    started_at: datetime,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """記錄一次 vLLM agent step 的用量（成功、上游錯誤或空回應都走這裡）。"""
    record_ai_template_call(
        session=session,
        user_id=requester_id,
        call_type="pve_chat",
        model_name=settings.VLLM_MODEL_NAME,
        metrics=usage_metrics(
            response_data,
            time.perf_counter() - started,
            request_id=request_id,
            started_at=started_at,
        ),
        status=status,
        error_message=error_message,
    )


async def chat(
    message: str | None = None,
    history: list[dict[str, Any]] | None = None,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
    requester_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
    resume_deferred_ssh: bool = True,
) -> ChatResponse:
    """執行有限步數的 AI agent 對話，支援 tool calling、確認中斷及接續。

    同一輪有多個 ssh_exec 時，第一個 pending、其餘寫成 deferred result；
    前端確認第一個後把整段 history 送回，這裡預設會接續 deferred 的呼叫。
    接續一律重新走 _execute_ssh_tool（require_confirm 仍生效），所以最多
    只會產生下一個待確認指令，不會跳過人工確認。

    allowed_vmids／scope_type／scope_id 目前沒有 production 呼叫端傳入
    （管理員 PVE Log 不限範圍），刻意保留為防禦性的 VM 範圍限制掛點。
    """
    messages = merge_pve_messages(
        message=message,
        history=history,
        server_system_prompt=_SYSTEM_PROMPT,
        scope_prompt=_SCOPE_PROMPT if allowed_vmids is not None else None,
        allowed_tool_names=_ALLOWED_TOOL_NAMES,
        allow_deferred=resume_deferred_ssh,
    )
    _validate_confirmation_history(
        messages,
        requester_id=requester_id,
        scope_type=scope_type,
        scope_id=scope_id,
        allowed_vmids=allowed_vmids,
    )
    if not settings.VLLM_BASE_URL or not settings.VLLM_MODEL_NAME:
        return ChatResponse(
            reply="",
            error=t("pveLog.vllmNotConfigured"),
        )

    tools_called: list[ToolCallRecord] = []
    if resume_deferred_ssh:
        while deferred_call := _next_deferred_ssh_call(messages):
            message_index, tool_call_id, func_args = deferred_call
            try:
                result = await _execute_ssh_tool(
                    func_args,
                    session=session,
                    allowed_vmids=allowed_vmids,
                    requester_id=requester_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
            except Exception as exc:
                logger.error("延後的 SSH 工具執行失敗：%s", exc)
                result = {"error": str(exc)}
            result_dict = result if isinstance(result, dict) else {}
            messages[message_index]["content"] = json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )
            if result_dict.get("pending") and result_dict.get("confirm_token"):
                from app.ai.pve_log.ssh_exec import bind_pending_tool_call

                bind_pending_tool_call(
                    str(result_dict["confirm_token"]),
                    tool_call_id,
                )
            tools_called.append(
                ToolCallRecord(
                    name="ssh_exec",
                    args=func_args,
                    result=result_dict,
                    tool_call_id=tool_call_id,
                )
            )
            if result_dict.get("pending"):
                return ChatResponse(
                    reply=t("pveLog.confirmationNextPending"),
                    tools_called=tools_called,
                    needs_confirmation=True,
                    messages=messages,
                )
    _tool_context: PveToolContext | None = None

    for tool_round in range(_MAX_TOOL_ROUNDS + 1):
        payload: dict[str, Any] = {
            "model": settings.VLLM_MODEL_NAME,
            "messages": messages,
            "tools": _TOOLS,
            "tool_choice": "auto",
            # 刻意固定，不讀 settings.VLLM_CHAT_*：system-ai.example.json 沒有
            # pve_log 區段，改讀設定會讓這類部署退回通用預設（0.6／1600）。
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        request_id = new_ai_request_id()
        record_usage = functools.partial(
            _record_chat_usage,
            session=session,
            requester_id=requester_id,
            request_id=request_id,
            started=time.perf_counter(),
            started_at=datetime.now(timezone.utc),
        )
        try:
            data = await vllm_client.create_chat_completion(
                payload,
                timeout=float(settings.VLLM_TIMEOUT),
                request_id=request_id,
            )
        except httpx.HTTPStatusError as exc:
            record_usage(
                {},
                status="error",
                error_message=f"upstream_http_{exc.response.status_code}",
            )
            logger.error(
                "vLLM 請求失敗（%d）：%s", exc.response.status_code, exc.response.text
            )
            return ChatResponse(
                reply="",
                tools_called=tools_called,
                messages=messages,
                error=t("pveLog.llmHttpError", status=exc.response.status_code),
            )
        except Exception as exc:
            record_usage({}, status="error", error_message=str(exc))
            logger.error("vLLM 連線失敗：%s", exc)
            return ChatResponse(
                reply="",
                tools_called=tools_called,
                messages=messages,
                error=t("pveLog.llmConnectionFailed", error=exc),
            )

        choices = data.get("choices") or []
        if not choices:
            record_usage(data, status="error", error_message="empty_choices")
            logger.error("vLLM agent step %d 回應 choices 為空：%s", tool_round, data)
            return ChatResponse(
                reply="",
                tools_called=tools_called,
                messages=messages,
                error=t("pveLog.llmEmptyResponse"),
            )

        record_usage(data)

        assistant_msg = _normalize_assistant_message(choices[0].get("message") or {})
        reserved_tool_call_ids = {
            str(item.get("id"))
            for item in messages
            if item.get("role") == "assistant"
            for tool_call in item.get("tool_calls") or []
            if isinstance(tool_call, dict) and tool_call.get("id")
        }
        assistant_msg = _canonicalize_model_tool_calls(
            assistant_msg,
            reserved_ids=reserved_tool_call_ids,
        )
        tool_calls = assistant_msg.get("tool_calls") or []
        if not tool_calls:
            messages.append(assistant_msg)
            return ChatResponse(
                reply=assistant_msg.get("content") or "",
                tools_called=tools_called,
                messages=messages,
            )

        if tool_round >= _MAX_TOOL_ROUNDS:
            # 不把這輪沒有 result 的 tool_calls 放進 history：回傳的 transcript
            # 必須停在上一個完整的 tool round，否則下一個 user turn 會被
            # history 驗證以「assistant tool-call 尚未完成」拒絕（422）。
            logger.error("AI 工具呼叫超過上限（%d 輪）", _MAX_TOOL_ROUNDS)
            return ChatResponse(
                reply="",
                tools_called=tools_called,
                messages=messages,
                error=t("pveLog.tooManyToolRounds"),
            )
        messages.append(assistant_msg)

        needs_pve_tool = any(
            tc.get("function", {}).get("name") != "ssh_exec" for tc in tool_calls
        )
        if needs_pve_tool and _tool_context is None:
            # Context 只在本 request 第一次需要 PVE API tool 時建立；真正的
            # network I/O 在 _execute_tool_sync 的 worker thread 內執行。
            _tool_context = PveToolContext()

        parsed_calls = [
            (
                tc,
                str((tc.get("function") or {}).get("name") or ""),
                _parse_tool_arguments(
                    (tc.get("function") or {}).get("arguments") or "{}"
                ),
            )
            for tc in tool_calls
        ]

        # 同一輪只讓第一個 ssh_exec 進入 pending，之後的 ssh_exec 寫成
        # deferred，待使用者確認後由下一次 chat() 接續。
        needs_confirmation = False
        for tc, func_name, func_args in parsed_calls:
            logger.info(
                "執行工具（agent step %d）%s，參數：%s",
                tool_round,
                func_name,
                func_args,
            )

            try:
                if func_name == "ssh_exec" and needs_confirmation:
                    result = _deferred_ssh_result(func_args)
                elif func_name == "ssh_exec":
                    result = await _execute_ssh_tool(
                        func_args,
                        session=session,
                        allowed_vmids=allowed_vmids,
                        requester_id=requester_id,
                        scope_type=scope_type,
                        scope_id=scope_id,
                    )
                elif func_name == "get_guest_diagnostic_summary":
                    result = await _execute_guest_diagnostics_tool(
                        func_args,
                        context=_tool_context,
                        session=session,
                        allowed_vmids=allowed_vmids,
                    )
                else:
                    if _tool_context is None:
                        raise RuntimeError("PVE tool context 尚未建立")
                    result = await asyncio.to_thread(
                        _execute_tool_sync,
                        _tool_context,
                        func_name,
                        func_args,
                        allowed_vmids=allowed_vmids,
                    )
                result_dict = result if isinstance(result, dict) else {}
                needs_confirmation = needs_confirmation or bool(
                    result_dict.get("pending")
                )
                tool_content = json.dumps(result, ensure_ascii=False, default=str)
                tool_call_id = str(tc.get("id") or "")
                if result_dict.get("pending") and result_dict.get("confirm_token"):
                    from app.ai.pve_log.ssh_exec import bind_pending_tool_call

                    bind_pending_tool_call(
                        str(result_dict["confirm_token"]),
                        tool_call_id,
                    )
                tools_called.append(
                    ToolCallRecord(
                        name=func_name,
                        args=func_args,
                        result=result_dict,
                        tool_call_id=tool_call_id,
                    )
                )
            except Exception as exc:
                logger.error("工具 %s 執行失敗：%s", func_name, exc)
                tool_content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                tool_call_id = str(tc.get("id") or "")
                tools_called.append(
                    ToolCallRecord(
                        name=func_name,
                        args=func_args,
                        result={"error": str(exc)},
                        tool_call_id=tool_call_id,
                    )
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": tool_content,
                }
            )

        if needs_confirmation:
            return ChatResponse(
                reply=t("pveLog.confirmationPending"),
                tools_called=tools_called,
                needs_confirmation=True,
                messages=messages,
            )

    raise AssertionError("unreachable")
