"""SSH 遠端執行服務

流程（兩條路徑）：
  內部路徑（主後端內嵌模組，傳入 session）：
      a. _resolve_vm_info_from_db → 直接查 DB 取 IP / SSH key
      b. paramiko SSH 連線並執行指令
  HTTP 回呼路徑（獨立 ai-pve-log 子服務，不傳 session）：
      a. POST /api/v1/login/access-token → 取得 SkyLab JWT
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
import logging
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

import httpx
from sqlmodel import Session

from app.ai.pve_log.config import settings
from app.ai.pve_log.guest_diagnostics import (
    ERROR_CODE_COMMAND_FAILED,
    ERROR_CODE_CONNECTION_FAILED,
    ERROR_CODE_PROBE_TIMEOUT,
    ERROR_CODE_RESOLVE_FAILED,
    GuestProbe,
    ProbeResult,
)
from app.ai.pve_log.schemas import SSHConfirmRequest, SSHExecRequest, SSHExecResult
from app.ai.pve_log.ssh_guard import check_command
from app.core.i18n import t
from app.core.security import decrypt_value
from app.infrastructure.ssh import create_key_client
from app.repositories import resource as resource_repo
from app.services.proxmox import proxmox_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pending token 暫存（內存，TTL 5 分鐘）
# ---------------------------------------------------------------------------

_PENDING_TTL = 300  # 秒
_MAX_OUTPUT_CHARS = 16 * 1024
_SENSITIVE_OUTPUT_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.+?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)
_pending_store: dict[str, dict[str, Any]] = {}  # token → {request, created_at}
_completed_store: dict[str, dict[str, Any]] = {}


def _store_pending(
    req: SSHExecRequest,
    *,
    allowed_vmids: set[int] | None = None,
    requester_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
) -> str:
    """儲存待確認請求，回傳 token。"""
    token = str(uuid.uuid4())
    _pending_store[token] = {
        "request": req,
        "created_at": time.monotonic(),
        "allowed_vmids": set(allowed_vmids) if allowed_vmids is not None else None,
        "requester_id": requester_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
    }
    _cleanup_expired()
    return token


def _pop_pending(token: str) -> dict[str, Any] | None:
    """取出待確認請求（同時從 store 移除）。"""
    _cleanup_expired()
    return _pending_store.pop(token, None)


def _peek_pending(token: str) -> dict[str, Any] | None:
    _cleanup_expired()
    return _pending_store.get(token)


def bind_pending_tool_call(token: str, tool_call_id: str) -> bool:
    """Bind a pending confirmation token to the assistant tool-call id."""
    entry = _peek_pending(token)
    if entry is None or not tool_call_id:
        return False
    entry["tool_call_id"] = tool_call_id
    return True


def _store_completed(
    token: str,
    *,
    entry: dict[str, Any],
    result: SSHExecResult,
) -> None:
    _completed_store[token] = {
        "token": token,
        "created_at": time.monotonic(),
        "request": entry.get("request"),
        "tool_call_id": entry.get("tool_call_id"),
        "requester_id": entry.get("requester_id"),
        "scope_type": entry.get("scope_type"),
        "scope_id": entry.get("scope_id"),
        "allowed_vmids": entry.get("allowed_vmids"),
        "result": result.model_dump(mode="json"),
        "consumed": False,
    }


def peek_completed_confirmation(token: str) -> dict[str, Any] | None:
    """Read a just-consumed confirmation result for one history continuation."""
    _cleanup_expired()
    return _completed_store.get(token)


def consume_completed_confirmation(token: str) -> dict[str, Any] | None:
    """Mark a confirmation result as consumed after history validation."""
    _cleanup_expired()
    entry = _completed_store.get(token)
    if entry is None or entry.get("consumed"):
        return None
    entry["consumed"] = True
    return entry


def find_completed_confirmation_by_tool_call(
    tool_call_id: str,
) -> dict[str, Any] | None:
    """Find a confirmation record by its immutable assistant tool-call id."""
    _cleanup_expired()
    for entry in _completed_store.values():
        if entry.get("tool_call_id") == tool_call_id:
            return entry
    return None


def peek_pending_scope(token: str) -> tuple[str | None, uuid.UUID | None]:
    """Read token scope without consuming it."""
    entry = _peek_pending(token)
    if entry is None:
        return None, None
    return entry.get("scope_type"), entry.get("scope_id")


def peek_pending_request(token: str) -> SSHExecRequest | None:
    """Read a pending request so the caller can re-authorize its VMID."""
    entry = _peek_pending(token)
    if entry is None:
        return None
    request = entry.get("request")
    return request if isinstance(request, SSHExecRequest) else None


def _cleanup_expired() -> None:
    now = time.monotonic()
    expired = [k for k, v in _pending_store.items() if now - v["created_at"] > _PENDING_TTL]
    for k in expired:
        _pending_store.pop(k, None)
    completed_expired = [
        k for k, v in _completed_store.items()
        if now - v["created_at"] > _PENDING_TTL
    ]
    for k in completed_expired:
        _completed_store.pop(k, None)


# ---------------------------------------------------------------------------
# SkyLab API 呼叫
# ---------------------------------------------------------------------------


async def _get_campus_token(client: httpx.AsyncClient) -> str:
    """取得 SkyLab JWT access token。"""
    url = f"{settings.skylab_api_base}/login/access-token"
    resp = await client.post(
        url,
        data={
            "username": settings.skylab_api_user,
            "password": settings.skylab_api_password,
        },
    )
    if not resp.is_success:
        raise RuntimeError(
            t("pveLog.skylabLoginFailed", status=resp.status_code, detail=resp.text[:200])
        )
    data = resp.json()
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(t("pveLog.skylabTokenMissing"))
    return token


async def _get_vm_ip(client: httpx.AsyncClient, token: str, vmid: int) -> str:
    """取得 VM/LXC 的 IP 位址。"""
    url = f"{settings.skylab_api_base}/resources/{vmid}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if not resp.is_success:
        raise RuntimeError(
            t(
                "pveLog.resourceFetchFailed",
                vmid=vmid,
                status=resp.status_code,
                detail=resp.text[:200],
            )
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
        raise RuntimeError(t("pveLog.noIpAddress", vmid=vmid))
    return ip


async def _get_ssh_private_key(client: httpx.AsyncClient, token: str, vmid: int) -> str:
    """取得 VM/LXC 的 SSH private key（PEM 格式）。"""
    url = f"{settings.skylab_api_base}/resources/{vmid}/ssh-key"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    if not resp.is_success:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = resp.text[:200]
        if resp.status_code in {404, 502} and "not found" in detail.lower():
            raise RuntimeError(t("pveLog.sshKeyNotRegistered", vmid=vmid))
        raise RuntimeError(
            t(
                "pveLog.sshKeyFetchFailed",
                status=resp.status_code,
                detail=detail or resp.text[:200],
            )
        )
    data = resp.json()
    key = data.get("ssh_private_key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError(t("pveLog.sshKeyEmpty", vmid=vmid))
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
    """建立 SSH 連線並執行指令，回傳 (exit_code, stdout, stderr)。

    使用 infrastructure 共用的 create_key_client：
    首次連線記錄 host key（trust-on-first-use），之後 key 變更會拒絕連線。
    """
    client = create_key_client(
        host,
        port,
        username,
        private_key_pem,
        timeout=timeout,
    )
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out_text = stdout.read().decode(errors="replace")
        err_text = stderr.read().decode(errors="replace")
        return exit_code, out_text, err_text
    finally:
        client.close()


def _redact_and_truncate(value: str) -> tuple[str, bool]:
    redacted = value
    for pattern in _SENSITIVE_OUTPUT_PATTERNS:
        if pattern.pattern.startswith("(?i)(password"):
            redacted = pattern.sub(
                lambda match: f"{match.group(1)}=[REDACTED]",
                redacted,
            )
        else:
            redacted = pattern.sub("[REDACTED PRIVATE KEY]", redacted)
    if len(redacted) <= _MAX_OUTPUT_CHARS:
        return redacted, False
    return redacted[:_MAX_OUTPUT_CHARS] + "\n...[truncated]", True


# ---------------------------------------------------------------------------
# Server-owned guest probe batch runner（供 get_guest_diagnostic_summary 使用）
# ---------------------------------------------------------------------------


def _read_limited(stream: Any, max_bytes: int) -> tuple[str, bool]:
    """讀取 stream 到 EOF，最多保留 max_bytes；其餘持續 drain。"""
    chunks: list[str] = []
    kept = 0
    truncated = False
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        data = chunk.encode() if isinstance(chunk, str) else chunk
        if truncated:
            continue
        if kept + len(data) > max_bytes:
            allowed = max_bytes - kept
            if allowed > 0:
                chunks.append(data[:allowed].decode(errors="replace"))
            truncated = True
            continue
        chunks.append(data.decode(errors="replace"))
        kept += len(data)
    return "".join(chunks), truncated


def _execute_single_probe(client: Any, probe: GuestProbe) -> ProbeResult:
    try:
        _, stdout_ch, stderr_ch = client.exec_command(
            probe.command, timeout=probe.exec_timeout
        )
        stdout, stdout_truncated = _read_limited(
            stdout_ch, probe.max_output_bytes
        )
        stderr, stderr_truncated = _read_limited(
            stderr_ch, probe.max_output_bytes
        )
        exit_code = stdout_ch.channel.recv_exit_status()
        return ProbeResult(
            name=probe.name,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=stdout_truncated or stderr_truncated,
        )
    except TimeoutError:
        logger.warning("Guest probe %s 執行逾時", probe.name)
        return ProbeResult(name=probe.name, error_code=ERROR_CODE_PROBE_TIMEOUT)
    except Exception as exc:
        logger.warning("Guest probe %s 執行失敗：%s", probe.name, exc)
        return ProbeResult(name=probe.name, error_code=ERROR_CODE_COMMAND_FAILED)


def _run_probe_batch_sync(
    host: str,
    private_key_pem: str,
    probes: Sequence[GuestProbe],
    *,
    connect_timeout: int,
    ssh_user: str,
    ssh_port: int,
) -> dict[str, ProbeResult]:
    """一次 SSH connection 逐項執行固定 probes（同步，供 to_thread 包裝）。"""
    client = create_key_client(
        host,
        ssh_port,
        ssh_user,
        private_key_pem,
        timeout=connect_timeout,
    )
    try:
        return {
            probe.name: _execute_single_probe(client, probe) for probe in probes
        }
    finally:
        client.close()


async def run_guest_probe_batch(
    vmid: int,
    probes: Sequence[GuestProbe],
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
) -> dict[str, ProbeResult]:
    """Server-owned guest 診斷 probe 批次執行。

    模型不可控：probes 由後端固定產生，SSH user/port 由後端決定，
    IP 與金鑰沿用既有授權解析。單一 probe 失敗不影響其他 probe。
    """
    if allowed_vmids is not None and vmid not in allowed_vmids:
        raise ValueError(t("pveLog.scopeRestricted"))
    try:
        host, private_key = await _resolve_vm_credentials(vmid, session=session)
    except Exception as exc:
        logger.error("Guest probe VMID=%s 解析失敗：%s", vmid, exc)
        return {
            probe.name: ProbeResult(
                name=probe.name, error_code=ERROR_CODE_RESOLVE_FAILED
            )
            for probe in probes
        }
    logger.info(
        "Guest probe batch vmid=%d host=%s probes=%d", vmid, host, len(probes)
    )
    try:
        return await asyncio.to_thread(
            _run_probe_batch_sync,
            host,
            private_key,
            probes,
            connect_timeout=settings.ssh_timeout,
            ssh_user=settings.ssh_default_user,
            ssh_port=22,
        )
    except Exception as exc:
        logger.error("Guest probe batch VMID=%s 連線失敗：%s", vmid, exc)
        return {
            probe.name: ProbeResult(
                name=probe.name, error_code=ERROR_CODE_CONNECTION_FAILED
            )
            for probe in probes
        }


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
        raise RuntimeError(t("pveLog.vmNotRegistered", vmid=vmid))

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
        raise RuntimeError(t("pveLog.noIpAddressCached", vmid=vmid))

    if not resource.ssh_private_key_encrypted:
        raise RuntimeError(t("pveLog.sshKeyNotRegistered", vmid=vmid))
    private_key = decrypt_value(resource.ssh_private_key_encrypted)
    return host, private_key


# ---------------------------------------------------------------------------
# 共用 VM 認證解析（DB 路徑 / HTTP 回呼路徑）
# ---------------------------------------------------------------------------

async def _resolve_vm_credentials(vmid: int, *, session: Session | None) -> tuple[str, str]:
    """取得 VM 的 (host_ip, private_key_pem)。

    session 傳入時走內部 DB 查詢路徑（主後端內嵌模組用）；
    未傳入時走 HTTP 回呼路徑（獨立 ai-pve-log 子服務用）。
    """
    if session is not None:
        return _resolve_vm_info_from_db(session, vmid)
    if not settings.skylab_api_user or not settings.skylab_api_password:
        raise RuntimeError(t("pveLog.skylabCredentialsMissing"))
    async with httpx.AsyncClient(timeout=settings.ssh_timeout) as client:
        token = await _get_campus_token(client)
        host = await _get_vm_ip(client, token, vmid)
        private_key = await _get_ssh_private_key(client, token, vmid)
    return host, private_key


# ---------------------------------------------------------------------------
# 主要公開函式
# ---------------------------------------------------------------------------

async def ssh_exec(
    req: SSHExecRequest,
    *,
    session: Session | None = None,
    allowed_vmids: set[int] | None = None,
    requester_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
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
            block_reason=t("pveLog.scopeRestricted"),
        )

    # ── 層二：執行前確認（AI 呼叫時） ────────────────────────────────────
    if req.require_confirm:
        token = _store_pending(
            req,
            allowed_vmids=allowed_vmids,
            requester_id=requester_id,
            scope_type=scope_type,
            scope_id=scope_id,
        )
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
    requester_id: uuid.UUID | None = None,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
    allowed_vmids: set[int] | None = None,
) -> SSHExecResult:
    """處理使用者確認（允許 or 拒絕）。"""
    token = confirm_req.token or confirm_req.confirm_token
    if not token:
        return SSHExecResult(
            vmid=0,
            host="",
            ssh_user="",
            command="",
            error=t("pveLog.missingConfirmToken"),
        )
    entry = _peek_pending(token)
    if entry is None:
        return SSHExecResult(
            vmid=0,
            host="",
            ssh_user="",
            command="",
            error=t("pveLog.confirmTokenInvalid"),
        )
    req = entry["request"]
    stored_vmids = entry.get("allowed_vmids")
    if (
        entry.get("requester_id") != requester_id
        or entry.get("scope_type") != scope_type
        or entry.get("scope_id") != scope_id
        or (
            allowed_vmids is not None
            and stored_vmids is not None
            and set(allowed_vmids) != set(stored_vmids)
        )
    ):
        return SSHExecResult(
            vmid=req.vmid,
            command=req.command,
            error=t("pveLog.confirmTokenScopeMismatch"),
        )
    # Only a successfully re-authorized caller may consume the one-time token.
    entry = _pop_pending(token)
    if entry is None:
        return SSHExecResult(
            vmid=req.vmid,
            command=req.command,
            error=t("pveLog.confirmTokenInvalid"),
        )
    allowed_vmids = stored_vmids

    def _completed(result: SSHExecResult) -> SSHExecResult:
        _store_completed(token, entry=entry, result=result)
        return result

    if not confirm_req.approved:
        logger.info("使用者拒絕執行 vmid=%d cmd=%r", req.vmid, req.command)
        return _completed(SSHExecResult(
            vmid=req.vmid,
            host="",
            ssh_user=req.ssh_user,
            command=req.command,
            error=t("pveLog.userRejected"),
        ))

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
            return _completed(SSHExecResult(
                vmid=req.vmid,
                host="",
                ssh_user=req.ssh_user,
                command=override_command,
                blocked=True,
                block_reason=guard.reason,
            ))
        req = req.model_copy(update={"command": override_command})

    result = await _do_exec(req, session=session, allowed_vmids=allowed_vmids)
    return _completed(result)


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
                block_reason=t("pveLog.scopeRestricted"),
            )

        host, private_key = await _resolve_vm_credentials(
            req.vmid, session=session
        )

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

        stdout, stdout_truncated = _redact_and_truncate(stdout)
        stderr, stderr_truncated = _redact_and_truncate(stderr)
        return SSHExecResult(
            vmid=req.vmid,
            host=host,
            ssh_user=req.ssh_user,
            command=req.command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
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
