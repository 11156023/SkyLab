"""AI 對話服務 — vLLM Tool Calling（支援 Gemma-4 / Qwen3 等模型）

流程：
  1. 帶著工具定義向 vLLM 發出第一次請求
  2. 若 AI 回傳 tool_calls，逐一執行：
     - PVE 工具：內部呼叫 collector，不走 HTTP
     - ssh_exec：呼叫 SkyLab API 取得 SSH key，SSH 進入 VM 執行
  3. 將工具結果加回 messages，發出第二次請求取得最終回答
  4. 回傳 ChatResponse

設計重點：
  - 一次 chat 請求只收集一次 PVE 快照（lazy），多個 tool_calls 共用同一份快照。
  - ssh_exec 在 AI Tool 呼叫時直接執行（不走 pending 確認），黑名單仍有效。
  - 若對話帶有群組範圍，工具輸出與 SSH 執行都只允許該群組可見的 VMID。
  - Gemma-4/Qwen3 的 <think> 與 tool call 標記會在第二次請求前清除，
    避免 message history 污染導致 LLM 無法正確總結。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from sqlmodel import Session

from app.ai.pve_log.collector import collect_snapshot
from app.ai.pve_log.config import settings
from app.ai.pve_log.schemas import ChatResponse, ToolCallRecord
from app.infrastructure.ai.pve_log import client as vllm_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 系統提示詞
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是 SkyLab PVE 管理助手，專門協助管理員查詢 Proxmox VE 虛擬化平台的資源狀態。

工具使用原則：
- 問題只涉及一種資源時，優先呼叫最精確的工具（例如只查儲存空間就用 get_storage，不要呼叫 get_resources）。
- 需要特定 VM/LXC 詳情時才呼叫 get_resource_detail，並傳入正確的 vmid。
- 若問題同時涉及多類資料，可以在同一輪呼叫多個工具。

SSH 工具（ssh_exec）使用原則：
- **優先使用 PVE API 工具**，PVE API 已可取得 CPU、記憶體、磁碟、網路的即時使用率。
- 只有在 PVE API 無法取得足夠細節時，才使用 ssh_exec。
- **適合 SSH 的場景**：程序列表（ps aux）、服務狀態（systemctl status）、
  詳細日誌（journalctl）、Python 環境查詢、自訂腳本執行、
  應用層資訊（nginx、docker、資料庫等）。
- **指令風格**：保持簡單實用，優先使用單行指令；Python 片段以 python3 -c '...' 格式。
- **必填 reason**：每次呼叫 ssh_exec 必須在 reason 欄位說明執行目的，
  讓使用者在確認對話中做出知情決策。
- ssh_exec 會先向使用者請求確認，被攔截的危險指令（如 rm -rf）無法執行。

回覆格式：
- 使用繁體中文，語氣清楚、簡潔。
- 請用 Markdown 格式輸出，優先使用標題、條列、粗體來整理內容。
- 數字單位換算為人類可讀格式：bytes → GB / MB、比例 → %（保留一位小數）。
- 若適合，允許使用 Markdown 表格，但不要為了湊版面而硬塞表格。
- 若問題與 PVE 無關，說明你只處理 PVE 相關查詢。\
"""

# ---------------------------------------------------------------------------
# Tool 定義（OpenAI function-calling 格式）
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
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
            "name": "ssh_exec",
            "description": (
                "透過 SSH 連線到指定 VMID 的 VM/LXC，執行遠端指令取得內部系統細節或執行管理操作。"
                "可執行任意 shell 指令或 Python 腳本片段。"
                "PVE API 工具無法提供足夠細節時才使用（如程序列表、服務狀態、日誌、Python 環境等）。"
                "執行前會向使用者請求確認；危險指令會被黑名單直接攔截。"
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

# ---------------------------------------------------------------------------
# Tool 執行器
# ---------------------------------------------------------------------------


def _execute_tool_sync(
    snapshot,
    name: str,
    args: dict,
    *,
    allowed_vmids: set[int] | None = None,
) -> Any:
    """使用已收集好的 snapshot 執行工具，同步版本（供 asyncio.to_thread 包裝）。"""
    if name == "get_nodes":
        return [n.model_dump(mode="json") for n in snapshot.nodes]

    elif name == "get_storage":
        result = snapshot.storages
        if args.get("node"):
            result = [s for s in result if s.node == args["node"]]
        return [s.model_dump(mode="json") for s in result]

    elif name == "get_resources":
        result = snapshot.resources
        if args.get("node"):
            result = [r for r in result if r.node == args["node"]]
        if args.get("resource_type"):
            result = [r for r in result if r.resource_type == args["resource_type"]]
        if args.get("status"):
            result = [r for r in result if r.status == args["status"]]
        if allowed_vmids is not None:
            result = [r for r in result if r.vmid in allowed_vmids]
        return [r.model_dump(mode="json") for r in result]

    elif name == "get_resource_detail":
        vmid = int(args["vmid"])
        if allowed_vmids is not None and vmid not in allowed_vmids:
            return {"error": "目前只允許存取所在群組內的 VM/LXC"}
        summary = next((r for r in snapshot.resources if r.vmid == vmid), None)
        if summary is None:
            return {"error": f"找不到 vmid={vmid}"}
        status_detail = next(
            (s for s in snapshot.resource_statuses if s.vmid == vmid), None
        )
        config = next((c for c in snapshot.resource_configs if c.vmid == vmid), None)
        interfaces = [i for i in snapshot.network_interfaces if i.vmid == vmid]
        return {
            "summary": summary.model_dump(mode="json"),
            "status": status_detail.model_dump(mode="json") if status_detail else None,
            # raw 欄位含完整 Proxmox 原始設定，資訊冗餘且大量消耗 LLM context，予以排除
            "config": config.model_dump(mode="json", exclude={"raw"})
            if config
            else None,
            "network_interfaces": [i.model_dump(mode="json") for i in interfaces],
        }

    elif name == "get_cluster":
        return snapshot.cluster.model_dump(mode="json")

    else:
        return {"error": f"未知工具：{name}"}


async def _execute_ssh_tool(
    args: dict,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> dict:
    """執行 ssh_exec 工具（async，需要等待 SSH 連線）。

    重要設計：
    - 在 AI Tool 呼叫時，不使用 require_confirm，直接執行取得真實資料
    - LLM 必須拿到真實結果才能繼續總結，若回傳 pending=True 則 LLM 沒有資料可總結
    - 黑名單依然產生作用（blocked=True 時回傳攔截說明）
    - 安全性：黑名單（自動）+ 系統提示詞已告知 AI 描述指令目的
    """
    from app.ai.pve_log.schemas import SSHExecRequest as _SSHExecRequest
    from app.ai.pve_log.ssh_exec import ssh_exec as _ssh_exec

    try:
        vmid = int(args["vmid"])
        command = str(args["command"])
    except (KeyError, ValueError, TypeError) as e:
        return {"error": f"缺少或無效的必填參數: {e}", "pending": False}

    if allowed_vmids is not None and vmid not in allowed_vmids:
        return {
            "vmid": vmid,
            "host": "",
            "ssh_user": str(args.get("ssh_user", "root")),
            "command": command,
            "blocked": True,
            "block_reason": "目前只允許存取所在群組內的 VM/LXC",
            "pending": False,
        }

    req = _SSHExecRequest(
        vmid=vmid,
        command=command,
        ssh_user=str(args.get("ssh_user", "root")),
        ssh_port=int(args.get("ssh_port", 22)),
        require_confirm=True,  # 支援中斷與接續確認，改為 True
    )
    result = await _ssh_exec(req, session=session, allowed_vmids=allowed_vmids)
    data = result.model_dump(mode="json")
    # 補充 reason 給前端顯示（AI 提供的說明）
    data["reason"] = str(args.get("reason", "未提供原因"))
    return data


# ---------------------------------------------------------------------------
# 主對話函式
# ---------------------------------------------------------------------------


async def chat(
    message: str | None = None,
    history: list[dict] | None = None,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> ChatResponse:
    """單次 AI 對話，支援 Tool Calling 及其接續。

    設計：
    - 第一次 LLM 請求帶工具定義
    - 若 AI 呼叫工具，收集快照（僅一次），執行所有工具
    - 若有需要確認的工具（pending=True），則中斷並回傳給前端
    - 否則進行第二次 LLM 請求取得最終回答
    """
    if not settings.VLLM_BASE_URL or not settings.VLLM_MODEL_NAME:
        return ChatResponse(
            reply="",
            error="vLLM 設定不完整，請確認 .env 中的 VLLM_* 設定",
        )

    messages: list[dict] = history or []
    if not messages:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
        ]
        if allowed_vmids is not None:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "本次對話僅可讀取目前群組可見的 VM/LXC 資源，"
                        "不得查詢或操作範圍外的 VMID。"
                    ),
                }
            )
        if message:
            messages.append({"role": "user", "content": message})

    tools_called: list[ToolCallRecord] = []
    _snapshot = None  # lazy，只有工具真的被呼叫時才收集

    # ── 第一次請求：帶工具定義 ──────────────────────────────────────
    payload: dict[str, Any] = {
        "model": settings.VLLM_MODEL_NAME,
        "messages": messages,
        "tools": _TOOLS,
        "tool_choice": "auto",
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    try:
        data = await vllm_client.create_chat_completion(
            payload,
            timeout=float(settings.VLLM_TIMEOUT),
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "vLLM 請求失敗（%d）：%s", exc.response.status_code, exc.response.text
        )
        return ChatResponse(
            reply="",
            error=f"LLM 服務回傳錯誤 {exc.response.status_code}",
        )
    except Exception as exc:
        logger.error("vLLM 連線失敗：%s", exc)
        return ChatResponse(reply="", error=f"無法連線至 LLM 服務：{exc}")

    choices = data.get("choices") or []
    if not choices:
        logger.error("vLLM 第一次回應 choices 為空：%s", data)
        return ChatResponse(reply="", error="LLM 回傳空回應（choices 為空）")

    assistant_msg = choices[0].get("message") or {}

    # ── Qwen3 tool call 標記清理與手動解析 ───────────────────────────────────────
    # Qwen3 模型有時會將工具呼叫標記放入 content 而非 tool_calls 欄位。
    # 格式可能使用 <|"|> 作為字串引號分隔符，例如：
    #   <|tool_call>call:ssh_exec{command:<|"|>python3 -V<|"|>,vmid:157}<tool_call|>
    import re as _re
    import uuid as _uuid
    raw_content = assistant_msg.get("content") or ""

    if not assistant_msg.get("tool_calls") and "call:" in raw_content:
        # 使用貪婪匹配：從 call:funcName{ 到結束標記 <tool_call|> 或 <|/tool_call|>
        match = _re.search(
            r"<\|?tool_call\|?>\s*call:([a-zA-Z0-9_]+)\s*(\{.+?\})\s*<\|?/?tool_call\|?>",
            raw_content, flags=_re.DOTALL,
        )
        if not match:
            # 備用：沒有結束標記的情況（模型截斷）
            match = _re.search(
                r"<\|?tool_call\|?>\s*call:([a-zA-Z0-9_]+)\s*(\{.+\})",
                raw_content, flags=_re.DOTALL,
            )
        if match:
            func_name = match.group(1)
            args_raw = match.group(2)
            # 1. 將 Qwen3 特殊引號 <|"|> 替換為標準雙引號
            args_fixed = args_raw.replace('<|"|>', '"')
            # 2. 幫未加引號的 key 加上雙引號（例如 command:"..." → "command":"..."）
            args_fixed = _re.sub(
                r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)',
                r'\1"\2"\3', args_fixed,
            )
            try:
                parsed_args = json.loads(args_fixed)
                assistant_msg["tool_calls"] = [
                    {
                        "id": f"call_{_uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": func_name,
                            "arguments": json.dumps(parsed_args, ensure_ascii=False),
                        },
                    }
                ]
                logger.info("成功手動解析 Qwen tool call: %s(%s)", func_name, parsed_args)
            except Exception as e:
                logger.error("手動解析 Qwen tool call 失敗: %s, 修正後: %s", e, args_fixed)

    if assistant_msg.get("tool_calls"):
        # 移除 Qwen3 內部 think 區塊（<think>...</think>）
        cleaned = _re.sub(r"<think>.*?</think>", "", raw_content, flags=_re.DOTALL)
        # 移除所有 tool call 標記（涵蓋 <|"|> 引號格式）
        cleaned = _re.sub(
            r"<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{.+?\}\s*<\|?/?tool_call\|?>",
            "", cleaned, flags=_re.DOTALL,
        )
        # 備用：無結束標記
        cleaned = _re.sub(
            r"<\|?tool_call\|?>\s*call:[a-zA-Z0-9_]+\s*\{.+\}",
            "", cleaned, flags=_re.DOTALL,
        )
        cleaned = _re.sub(r"<\|tool_call\|>.*?<\|/tool_call\|>", "", cleaned, flags=_re.DOTALL)
        cleaned = _re.sub(r"<\|tool_call>.*?<tool_call\|>", "", cleaned, flags=_re.DOTALL)
        cleaned = _re.sub(r'```json\s*\{\s*"tool_call".*?```', "", cleaned, flags=_re.DOTALL)
        cleaned = _re.sub(r"<tool_call>.*?</tool_call>", "", cleaned, flags=_re.DOTALL)
        # 清除殘留的特殊 token
        cleaned = _re.sub(r'<\|[^>]*\|>', "", cleaned)
        assistant_msg = {**assistant_msg, "content": cleaned.strip() or None}

    messages.append(assistant_msg)

    tool_calls = assistant_msg.get("tool_calls") or []

    # ── 執行工具 ─────────────────────────────────────────────────────
    needs_confirmation = False
    if tool_calls:
        # ssh_exec 不需要 PVE 快照，只有其他工具才需要收集
        needs_snapshot = any(tc["function"]["name"] != "ssh_exec" for tc in tool_calls)
        if needs_snapshot:
            try:
                _snapshot = await asyncio.to_thread(collect_snapshot)
            except Exception as exc:
                logger.error("收集 PVE 快照失敗：%s", exc)
                return ChatResponse(reply="", error=f"收集 PVE 資料失敗：{exc}")

        for tc in tool_calls:
            func_name: str = tc["function"]["name"]
            try:
                args_val = tc["function"].get("arguments") or "{}"
                if isinstance(args_val, dict):
                    func_args = args_val
                elif isinstance(args_val, str):
                    args_str = args_val.strip()
                    # 先嘗試標準解析
                    try:
                        func_args = json.loads(args_str)
                    except json.JSONDecodeError:
                        # 失敗時嘗試修復 Gemma 4 / Qwen 特殊引號
                        args_str = args_str.replace('<|"|>', '"')
                        args_str = args_str.replace("'", '"')
                        # 嘗試修正未加引號的 key (排除已經有引號的情況)
                        args_str = _re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)', r'\1"\2"\3', args_str)
                        try:
                            func_args = json.loads(args_str)
                        except json.JSONDecodeError:
                            # 如果還是失敗，嘗試用 eval (僅限安全的 dict literal)
                            import ast
                            try:
                                func_args = ast.literal_eval(args_str)
                                if not isinstance(func_args, dict):
                                    func_args = {}
                            except Exception:
                                func_args = {}
                else:
                    func_args = {}
            except Exception as e:
                logger.warning("Tool arguments 解析失敗: %s, 原始值: %s", e, tc["function"].get("arguments"))
                func_args = {}

            logger.info("執行工具 %s，參數：%s", func_name, func_args)

            try:
                # ssh_exec 是 async 操作（需要 HTTP + SSH 連線），走獨立路徑
                if func_name == "ssh_exec":
                    result = await _execute_ssh_tool(
                        func_args,
                        session=session,
                        allowed_vmids=allowed_vmids,
                    )
                else:
                    result = _execute_tool_sync(
                        _snapshot,
                        func_name,
                        func_args,
                        allowed_vmids=allowed_vmids,
                    )

                result_dict = result if isinstance(result, dict) else {}
                if result_dict.get("pending"):
                    needs_confirmation = True

                tool_content = json.dumps(result, ensure_ascii=False, default=str)
                tools_called.append(ToolCallRecord(name=func_name, args=func_args, result=result_dict))
            except Exception as exc:
                logger.error("工具 %s 執行失敗：%s", func_name, exc)
                tool_content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                tools_called.append(ToolCallRecord(name=func_name, args=func_args, result={"error": str(exc)}))

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_content,
                }
            )

    # ── 第二次請求：取得最終回答（僅在有工具呼叫時且不需等待確認） ────────────────
    if tool_calls and not needs_confirmation:
        payload2: dict[str, Any] = {
            "model": settings.VLLM_MODEL_NAME,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        try:
            data2 = await vllm_client.create_chat_completion(
                payload2,
                timeout=float(settings.VLLM_TIMEOUT),
            )
        except Exception as exc:
            logger.error("vLLM 第二次請求失敗：%s", exc)
            return ChatResponse(
                reply="",
                tools_called=tools_called,
                error=f"取得最終回答失敗：{exc}",
            )

        choices2 = data2.get("choices") or []
        reply = (
            (choices2[0].get("message") or {}).get("content") or ""
            if choices2
            else ""
        )
        if not choices2:
            logger.error("vLLM 第二次回應 choices 為空：%s", data2)

        # 將最終回覆也加入 messages
        if reply:
            messages.append(choices2[0].get("message") or {"role": "assistant", "content": reply})

    elif needs_confirmation:
        reply = "有指令需要您的確認，請允許後繼續。"
    else:
        reply = assistant_msg.get("content") or ""

    return ChatResponse(
        reply=reply,
        tools_called=tools_called,
        needs_confirmation=needs_confirmation,
        messages=messages
    )
