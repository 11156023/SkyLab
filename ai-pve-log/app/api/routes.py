"""PVE 批量分析 API 路由"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    ChatRequest,
    ChatResponse,
    ClusterInfo,
    NetworkInterface,
    NodeInfo,
    ResourceConfig,
    ResourceStatus,
    ResourceSummary,
    StorageInfo,
    SystemSnapshot,
    ApiEndpointReference,
    PVE_API_REFERENCE,
    SSHExecRequest,
    SSHExecResult,
    SSHConfirmRequest,
)
from app.services.collector import collect_snapshot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["pve-log"])


# ---------------------------------------------------------------------------
# 完整快照（批量分析主要入口）
# ---------------------------------------------------------------------------


@router.get(
    "/snapshot",
    response_model=SystemSnapshot,
    summary="完整系統快照",
    description=(
        "一次性批量收集所有節點、VM、LXC 的最新資料。\n\n"
        "包含：叢集概覽、節點清單、儲存空間、VM/LXC 摘要、"
        "即時詳細狀態、設定檔、LXC 網路介面。\n\n"
        "這是批量分析的主要入口，適合定期排程呼叫後存入資料庫。"
    ),
)
async def get_snapshot() -> SystemSnapshot:
    try:
        return await asyncio.to_thread(collect_snapshot)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("收集快照失敗：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"收集失敗：{exc}")


# ---------------------------------------------------------------------------
# 個別分類查詢
# ---------------------------------------------------------------------------


@router.get(
    "/nodes",
    response_model=list[NodeInfo],
    summary="節點清單",
    description="取得所有 PVE 節點的 CPU / 記憶體 / 磁碟使用率。",
)
async def get_nodes() -> list[NodeInfo]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    return snapshot.nodes


@router.get(
    "/storage",
    response_model=list[StorageInfo],
    summary="儲存空間清單",
    description="取得所有節點上的儲存空間資訊（容量、使用率、類型）。",
)
async def get_storage(
    node: str | None = Query(default=None, description="篩選特定節點"),
) -> list[StorageInfo]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    if node:
        return [s for s in snapshot.storages if s.node == node]
    return snapshot.storages


@router.get(
    "/resources",
    response_model=list[ResourceSummary],
    summary="VM/LXC 摘要清單",
    description="取得所有 VM 與 LXC 容器的摘要（狀態、CPU、記憶體、磁碟、網路）。",
)
async def get_resources(
    node: str | None = Query(default=None, description="篩選特定節點"),
    resource_type: str | None = Query(
        default=None, description="篩選類型：qemu 或 lxc"
    ),
    status: str | None = Query(default=None, description="篩選狀態：running / stopped"),
) -> list[ResourceSummary]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    result = snapshot.resources
    if node:
        result = [r for r in result if r.node == node]
    if resource_type:
        result = [r for r in result if r.resource_type == resource_type]
    if status:
        result = [r for r in result if r.status == status]
    return result


@router.get(
    "/resources/{vmid}",
    summary="單一資源詳細",
    description="取得指定 vmid 的摘要 + 即時狀態 + 設定 + 網路介面（LXC）。",
)
async def get_resource_detail(vmid: int) -> dict:
    snapshot = await asyncio.to_thread(collect_snapshot)

    summary = next((r for r in snapshot.resources if r.vmid == vmid), None)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"找不到 vmid={vmid}")

    status_detail = next(
        (s for s in snapshot.resource_statuses if s.vmid == vmid), None
    )
    config = next((c for c in snapshot.resource_configs if c.vmid == vmid), None)
    interfaces = [i for i in snapshot.network_interfaces if i.vmid == vmid]

    return {
        "summary": summary,
        "status": status_detail,
        "config": config,
        "network_interfaces": interfaces,
    }


@router.get(
    "/resource-statuses",
    response_model=list[ResourceStatus],
    summary="運行中資源即時狀態",
    description="取得所有 running 狀態的 VM/LXC 的即時詳細數值（含磁碟讀寫、網路流量）。",
)
async def get_resource_statuses(
    resource_type: str | None = Query(default=None, description="篩選 qemu 或 lxc"),
) -> list[ResourceStatus]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    result = snapshot.resource_statuses
    if resource_type:
        result = [s for s in result if s.resource_type == resource_type]
    return result


@router.get(
    "/resource-configs",
    response_model=list[ResourceConfig],
    response_model_exclude={"__all__": {"raw"}},
    summary="所有資源設定檔",
    description="取得所有 VM/LXC 的設定摘要（CPU/記憶體配置、磁碟大小、是否開機自啟等），預設不包含原始 raw 設定。",
)
async def get_resource_configs(
    resource_type: str | None = Query(default=None, description="篩選 qemu 或 lxc"),
) -> list[ResourceConfig]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    result = snapshot.resource_configs
    if resource_type:
        result = [c for c in result if c.resource_type == resource_type]
    return result


@router.get(
    "/network-interfaces",
    response_model=list[NetworkInterface],
    summary="LXC 網路介面",
    description="取得所有運行中 LXC 容器的網路介面（含 IP 位址，無需 guest agent）。",
)
async def get_network_interfaces() -> list[NetworkInterface]:
    snapshot = await asyncio.to_thread(collect_snapshot)
    return snapshot.network_interfaces


@router.get(
    "/cluster",
    response_model=ClusterInfo,
    summary="叢集概覽",
    description="取得 PVE 叢集整體資訊（節點數、quorum 狀態）。",
)
async def get_cluster() -> ClusterInfo:
    snapshot = await asyncio.to_thread(collect_snapshot)
    return snapshot.cluster


# ---------------------------------------------------------------------------
# PVE API 資料欄位參考表
# ---------------------------------------------------------------------------


@router.get(
    "/reference",
    response_model=list[ApiEndpointReference],
    summary="PVE API 資料欄位參考表",
    description=(
        "列出本服務所使用的所有 PVE API 端點，"
        "以及每個端點可取得的欄位說明（中文）。\n\n"
        "適合開發時查閱「PVE API 能拿到什麼資料」。"
    ),
)
def get_reference() -> list[ApiEndpointReference]:
    return PVE_API_REFERENCE


# ---------------------------------------------------------------------------
# AI 對話（Tool Calling）
# ---------------------------------------------------------------------------


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="AI 自然語言查詢 PVE",
    description=(
        "輸入自然語言問題，AI 自動決定是否呼叫 PVE 工具取得資料，"
        "再整理成人類可讀的回答。\n\n"
        "**單次查詢**（不保留對話歷史）。\n\n"
        "**可用工具：** `get_resources`、`get_nodes`、`get_storage`、"
        "`get_resource_detail`、`get_cluster`\n\n"
        "範例問題：\n"
        "- `列出所有停止的 LXC`\n"
        "- `哪個節點 CPU 使用率最高？`\n"
        "- `vmid 100 現在的記憶體用了多少？`\n"
        "- `local-lvm 還剩多少空間？`"
    ),
)
async def post_chat(body: ChatRequest) -> ChatResponse:
    from app.services.chat import chat as _chat

    try:
        return await _chat(message=body.message, history=body.messages)
    except Exception as exc:
        logger.error("chat 發生未預期錯誤：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# SSH 遠端執行
# ---------------------------------------------------------------------------


@router.post(
    "/ssh/exec",
    response_model=SSHExecResult,
    summary="SSH 遠端執行指令",
    description=(
        "透過 SkyLab API 取得 SSH key 後，SSH 進入指定 VM/LXC 執行指令。\n\n"
        "**安全機制（雙重）：**\n"
        "1. **黑名單過濾**：危險指令（`rm -rf`、`mkfs`、`shutdown` 等）立即攔截，"
        "回傳 `blocked=true`。\n"
        "2. **執行前確認**：設定 `require_confirm=true` 時（AI Tool 預設），"
        "回傳 `pending=true` + `confirm_token`，"
        "需再呼叫 `POST /api/v1/ssh/confirm` 確認後才實際執行。\n\n"
        "**憑證來源**：自動讀取 `.env` 中的 `FIRST_SUPERUSER` 與 "
        "`FIRST_SUPERUSER_PASSWORD`，無需手動輸入。\n\n"
        "**支援指令類型**：任意 shell 指令、Python 腳本片段等。\n\n"
        "範例指令：\n"
        "- `df -h` — 磁碟使用狀況\n"
        "- `free -m` — 記憶體使用\n"
        "- `ps aux | grep python` — 查詢 Python 程序\n"
        "- `python3 -c 'import sys; print(sys.version)'` — Python 版本\n"
        "- `systemctl status nginx` — 服務狀態\n"
        "- `journalctl -n 50 --no-pager` — 最新系統日誌"
    ),
    tags=["ssh"],
)
async def post_ssh_exec(body: SSHExecRequest) -> SSHExecResult:
    from app.services.ssh_exec import ssh_exec as _ssh_exec

    try:
        return await _ssh_exec(body)
    except Exception as exc:
        logger.error("ssh_exec 發生未預期錯誤：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/ssh/confirm",
    response_model=SSHExecResult,
    summary="確認或拒絕 SSH 執行",
    description=(
        "當 `POST /api/v1/ssh/exec` 以 `require_confirm=true` 呼叫時，"
        "會回傳 `pending=true` 與 `confirm_token`。\n\n"
        "使用者確認後，呼叫此端點：\n"
        "- `approved=true` → 實際執行並回傳結果\n"
        "- `approved=false` → 取消執行\n\n"
        "**Token TTL：5 分鐘**，過期後需重新發起請求。"
    ),
    tags=["ssh"],
)
async def post_ssh_confirm(body: SSHConfirmRequest) -> SSHExecResult:
    from app.services.ssh_exec import confirm_exec as _confirm_exec

    try:
        return await _confirm_exec(body)
    except Exception as exc:
        logger.error("ssh_confirm 發生未預期錯誤：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
