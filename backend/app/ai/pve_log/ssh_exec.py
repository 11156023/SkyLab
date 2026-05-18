"""SSH 遠端執行服務

流程（兩條路徑）：
  內部路徑（主後端內嵌模組，傳入 session）：
      a. _resolve_vm_info_from_db → 直接查 DB 取 IP / SSH key
      b. paramiko SSH 連線並執行指令
  HTTP 回呼路徑（獨立 ai-pve-log 子服務，不傳 session）：
      a. POST /api/v1/login/access-token → 取得 Campus Cloud JWT
      b. GET  /api/v1/resources/{vmid}   → 取得 VM IP
      c. GET  /api/v1/resources/{vmid}/ssh-key → 取得 SSH private key
      d. paramiko SSH 連線並執行指令
  共用流程：
      1. 黑名單過濾（ssh_guard）
      2. 若 require_confirm=True → 產生 pending token，等待使用者確認
      3. 回傳 SSHExecResult

設計重點：
  - 內部路徑不依賴 AI_API_PUBLIC_BASE_URL，避免變數名稱衝突
  - pending token 存於內存 dict，TTL 5 分鐘（適合 dev 環境）
  - 使用 asyncio.to_thread 包裝同步 paramiko，不阻塞 event loop
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from typing import Any

import httpx
import paramiko
from sqlmodel import Session

from app.ai.pve_log.config import settings
from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecRequest, SSHExecResult
from app.ai.pve_log.ssh_guard import check_command
from app.core.security import decrypt_value
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pending token 暫存（內存，TTL 5 分鐘）
# ---------------------------------------------------------------------------

_PENDING_TTL = 300  # 秒
_pending_store: dict[str, dict[str, Any]] = {}  # token → {request, created_at}


def _store_pending(
    req: SSHExecRequest,
    *,
    allowed_vmids: set[int] | None = None,
) -> str:
    """儲存待確認請求，回傳 token。"""
    token = str(uuid.uuid4())
    _pending_store[token] = {
        "request": req,
        "created_at": time.monotonic(),
        "allowed_vmids": set(allowed_vmids) if allowed_vmids is not None else None,
    }
    _cleanup_expired()
    return token


def _pop_pending(token: str) -> dict[str, Any] | None:
    """取出待確認請求（同時從 store 移除）。"""
    _cleanup_expired()
    return _pending_store.pop(token, None)


def _cleanup_expired() -> None:
    now = time.monotonic()
    expired = [k for k, v in _pending_store.items() if now - v["created_at"] > _PENDING_TTL]
    for k in expired:
        _pending_store.pop(k, None)


# ---------------------------------------------------------------------------
# Campus Cloud API 呼叫
# ---------------------------------------------------------------------------


async def _get_campus_token(client: httpx.AsyncClient) -> str:
    """取得 Campus Cloud JWT access token。"""
    url = f"{settings.campus_cloud_api_base}/login/access-token"
    resp = await client.post(
        url,
        data={
            "username": settings.campus_cloud_api_user,
            "password": settings.campus_cloud_api_password,
        },
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Campus Cloud 登入失敗（HTTP {resp.status_code}）：{resp.text[:200]}"
        )
    data = resp.json()
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Campus Cloud 登入回應中缺少 access_token")
    return token


async def _get_vm_ip(client: httpx.AsyncClient, token: str, vmid: int) -> str:
    """取得 VM/LXC 的 IP 位址。"""
    url = f"{settings.campus_cloud_api_base}/resources/{vmid}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if not resp.is_success:
        raise RuntimeError(
            f"取得 VMID={vmid} 資源失敗（HTTP {resp.status_code}）：{resp.text[:200]}"
        )
    data = resp.json()
    # /resources/{vmid} 回傳 {summary, status, config, network_interfaces}
    # ip_address 在 summary 層
    summary = data.get("summary") or data
    ip = summary.get("ip_address") if isinstance(summary, dict) else None
    if not ip:
        # 嘗試從 network_interfaces 取第一個非 lo 的 inet
        for iface in data.get("network_interfaces") or []:
            inet = iface.get("inet", "")
            if inet and not inet.startswith("127."):
                ip = inet.split("/")[0]
                break
    if not ip:
        raise RuntimeError(
            f"VMID={vmid} 沒有可用的 IP 位址。請確認 VM 正在運行且有設定網路。"
        )
    return ip


async def _get_ssh_private_key(client: httpx.AsyncClient, token: str, vmid: int) -> str:
    """取得 VM/LXC 的 SSH private key（PEM 格式）。"""
    url = f"{settings.campus_cloud_api_base}/resources/{vmid}/ssh-key"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if not resp.is_success:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text[:200]
        if resp.status_code in {404, 502} and "not found" in detail.lower():
            raise RuntimeError(
                f"VMID={vmid} 未在 Campus Cloud 資料庫中登記 SSH key。"
                "請先在 Campus Cloud 後端設定此 VM/LXC 的 SSH 金鑰。"
            )
        raise RuntimeError(
            f"取得 SSH key 失敗（HTTP {resp.status_code}）：{detail or resp.text[:200]}"
        )
    data = resp.json()
    key = data.get("ssh_private_key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(f"VMID={vmid} 的 SSH private key 為空。")
    return key


# ---------------------------------------------------------------------------
# SSH 執行（同步，供 asyncio.to_thread 包裝）
# ---------------------------------------------------------------------------


def _ssh_exec_sync(
    host: str,
    port: int,
    username: str,
    private_key_pem: str,
    command: str,
    timeout: int,
) -> tuple[int, str, str]:
    """建立 SSH 連線並執行指令，回傳 (exit_code, stdout, stderr)。"""
    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key_pem))
    client = paramiko.SSHClient()

    # 自動接受 unknown host key，避免首次連線因 known_hosts 缺少紀錄而中斷。
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    client.connect(
        hostname=host,
        port=port,
        username=username,
        pkey=pkey,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )

    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode(errors="replace")
        err_text = stderr.read().decode(errors="replace")
        return exit_code, out_text, err_text
    finally:
        client.close()


# ---------------------------------------------------------------------------
# 內部 VM 資訊解析（主後端內嵌模組用，不經 HTTP 回呼）
# ---------------------------------------------------------------------------

def _resolve_vm_info_from_db(session: Session, vmid: int) -> tuple[str, str]:
    """從資料庫直接取得 VM IP 與 SSH private key。

    回傳 (host_ip, private_key_pem)。
    僅供主後端內部使用；獨立 ai-pve-log 子服務請沿用 HTTP 回呼路徑。
    """
    resource = resource_repo.get_resource_by_vmid(session=session, vmid=vmid)
    if not resource:
        raise RuntimeError(f"VMID={vmid} 未在 Campus Cloud 資料庫中登記。")

    host = (resource_repo.get_cached_ip_address(session=session, vmid=vmid) or "").strip()
    if not host:
        # 對齊 /resources/{vmid} 行為：DB 無快取時，向 Proxmox 取即時 IP。
        try:
            vm_info = proxmox_service.find_resource(vmid)
            vm_node = str(vm_info.get("node") or "")
            vm_type = str(vm_info.get("type") or "")
            if vm_node and vm_type in {"qemu", "lxc"}:
                live_ip = proxmox_service.get_ip_address(vm_node, vmid, vm_type)
                if live_ip:
                    host = live_ip
                    try:
                        resource_repo.update_ip_address(
                            session=session,
                            vmid=vmid,
                            ip_address=live_ip,
                        )
                    except Exception:
                        session.rollback()
                        logger.warning(
                            "更新 VMID=%s IP 快取失敗（ip=%s）",
                            vmid,
                            live_ip,
                            exc_info=True,
                        )
        except Exception as exc:
            logger.warning("VMID=%s 即時 IP 解析失敗：%s", vmid, exc)

    if not host:
        raise RuntimeError(
            f"VMID={vmid} 沒有可用的 IP 位址。"
            "請確認 VM 正在運行且有設定網路，或 ip_address 快取已過期。"
        )

    if not resource.ssh_private_key_encrypted:
        raise RuntimeError(
            f"VMID={vmid} 未在 Campus Cloud 資料庫中登記 SSH key。"
            "請先在 Campus Cloud 後端設定此 VM/LXC 的 SSH 金鑰。"
        )
    private_key = decrypt_value(resource.ssh_private_key_encrypted)
    return host, private_key


# ---------------------------------------------------------------------------
# 主要公開函式
# ---------------------------------------------------------------------------

async def ssh_exec(
    req: SSHExecRequest,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> SSHExecResult:
    """SSH 執行主入口。

    呼叫端透過 SSHExecRequest.require_confirm 控制是否需要二次確認：
    - False（預設）：直接執行
    - True：回傳 pending=True + confirm_token，等待 /ssh/confirm 確認
    """
    # ── 層一：黑名單過濾 ──────────────────────────────────────────────────
    guard = check_command(req.command)
    if not guard.allowed:
        logger.warning("指令被黑名單攔截 vmid=%d cmd=%r reason=%s", req.vmid, req.command, guard.reason)
        return SSHExecResult(
            vmid=req.vmid,
            host="",
            ssh_user=req.ssh_user,
            command=req.command,
            blocked=True,
            block_reason=guard.reason,
        )

    if allowed_vmids is not None and req.vmid not in allowed_vmids:
        return SSHExecResult(
            vmid=req.vmid,
            host="",
            ssh_user=req.ssh_user,
            command=req.command,
            blocked=True,
            block_reason="目前只允許存取所在群組內的 VM/LXC",
        )

    # ── 層二：執行前確認（AI 呼叫時） ────────────────────────────────────
    if req.require_confirm:
        token = _store_pending(req, allowed_vmids=allowed_vmids)
        logger.info("SSH 待確認 vmid=%d cmd=%r token=%s", req.vmid, req.command, token)
        return SSHExecResult(
            vmid=req.vmid,
            host="",
            ssh_user=req.ssh_user,
            command=req.command,
            pending=True,
            confirm_token=token,
        )

    return await _do_exec(req, session=session, allowed_vmids=allowed_vmids)


async def confirm_exec(
    confirm_req: SSHConfirmRequest,
    *,
    session: Session | None = None,
) -> SSHExecResult:
    """處理使用者確認（允許 or 拒絕）。"""
    token = confirm_req.token or confirm_req.confirm_token
    if not token:
        return SSHExecResult(
            vmid=0,
            host="",
            ssh_user="",
            command="",
            error="缺少確認 token，請重新發起請求。",
        )
    entry = _pop_pending(token)
    if entry is None:
        return SSHExecResult(
            vmid=0,
            host="",
            ssh_user="",
            command="",
            error="確認 token 無效或已過期（TTL 5 分鐘）。請重新發起請求。",
        )
    req = entry["request"]
    allowed_vmids = entry.get("allowed_vmids")

    if not confirm_req.approved:
        logger.info("使用者拒絕執行 vmid=%d cmd=%r", req.vmid, req.command)
        return SSHExecResult(
            vmid=req.vmid,
            host="",
            ssh_user=req.ssh_user,
            command=req.command,
            error="使用者已拒絕執行此指令。",
        )

    override_command = (confirm_req.command or "").strip()
    if override_command:
        guard = check_command(override_command)
        if not guard.allowed:
            logger.warning(
                "使用者覆寫指令被黑名單攔截 vmid=%d cmd=%r reason=%s",
                req.vmid,
                override_command,
                guard.reason,
            )
            return SSHExecResult(
                vmid=req.vmid,
                host="",
                ssh_user=req.ssh_user,
                command=override_command,
                blocked=True,
                block_reason=guard.reason,
            )
        req = req.model_copy(update={"command": override_command})

    return await _do_exec(req, session=session, allowed_vmids=allowed_vmids)


async def _do_exec(
    req: SSHExecRequest,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> SSHExecResult:
    """實際執行 SSH 指令（通過安全檢查後）。

    當 session 傳入時走內部 DB 查詢路徑（主後端內嵌模組用）；
    未傳入時走 HTTP 回呼路徑（獨立 ai-pve-log 子服務用）。
    """
    timeout = settings.ssh_timeout
    host = ""

    try:
        if allowed_vmids is not None and req.vmid not in allowed_vmids:
            return SSHExecResult(
                vmid=req.vmid,
                host="",
                ssh_user=req.ssh_user,
                command=req.command,
                blocked=True,
                block_reason="目前只允許存取所在群組內的 VM/LXC",
            )

        if session is not None:
            host, private_key = _resolve_vm_info_from_db(session, req.vmid)
        else:
            if not settings.campus_cloud_api_user or not settings.campus_cloud_api_password:
                return SSHExecResult(
                    vmid=req.vmid,
                    host="",
                    ssh_user=req.ssh_user,
                    command=req.command,
                    error=(
                        "Campus Cloud 登入憑證未設定。"
                        "請確認 .env 中有 FIRST_SUPERUSER 與 FIRST_SUPERUSER_PASSWORD。"
                    ),
                )
            async with httpx.AsyncClient(timeout=timeout) as client:
                token = await _get_campus_token(client)
                host = await _get_vm_ip(client, token, req.vmid)
                private_key = await _get_ssh_private_key(client, token, req.vmid)

        # 4. SSH 連線執行（同步操作放進 thread）
        logger.info(
            "SSH 執行 vmid=%d host=%s user=%s cmd=%r",
            req.vmid, host, req.ssh_user, req.command,
        )
        exit_code, stdout, stderr = await asyncio.to_thread(
            _ssh_exec_sync,
            host,
            req.ssh_port,
            req.ssh_user,
            private_key,
            req.command,
            timeout,
        )

        return SSHExecResult(
            vmid=req.vmid,
            host=host,
            ssh_user=req.ssh_user,
            command=req.command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as exc:
        logger.error("SSH 執行失敗 vmid=%d host=%s: %s", req.vmid, host, exc)
        return SSHExecResult(
            vmid=req.vmid,
            host=host,
            ssh_user=req.ssh_user,
            command=req.command,
            error=str(exc),
        )
