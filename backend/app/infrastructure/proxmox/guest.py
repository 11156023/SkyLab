"""Guest 內檔案寫入：QEMU 走 guest agent file-write，LXC 走 node SSH pct push。

- QEMU：POST /nodes/{node}/qemu/{vmid}/agent/file-write。內容自行 base64
  並帶 ``encode=0``（二進位安全）。前置 agent ping，失敗回可讀 400。
- LXC：SSH 至 active host（與 script_deploy_service 相同的單主機假設），
  SFTP 寫暫存檔 → ``pct push --perms`` → 清理暫存。
"""

from __future__ import annotations

import base64
import logging
import shlex
import uuid
from typing import Any

from app.exceptions import AppError, BadRequestError
from app.infrastructure.proxmox import (
    get_active_host,
    get_proxmox_api,
    get_proxmox_settings,
)
from app.infrastructure.ssh import create_password_client, exec_command

logger = logging.getLogger(__name__)

MAX_CONFIG_FILE_BYTES = 1_048_576  # 1 MB


def validate_target_path(path: str) -> None:
    if not path.startswith("/"):
        raise BadRequestError("目標路徑必須為絕對路徑")
    if ".." in path.split("/"):
        raise BadRequestError("目標路徑不可包含 ..")


def _ping_agent(node: str, vmid: int) -> None:
    try:
        get_proxmox_api().nodes(node).qemu(vmid).agent("ping").post()
    except Exception as exc:
        raise AppError(
            f"VM {vmid} 的 QEMU guest agent 未回應（可能未安裝 agent 或 VM 未開機）",
            400,
        ) from exc


def write_file_qemu(node: str, vmid: int, path: str, content: bytes) -> None:
    validate_target_path(path)
    _ping_agent(node, vmid)
    encoded = base64.b64encode(content).decode("ascii")
    get_proxmox_api().nodes(node).qemu(vmid).agent("file-write").post(
        file=path, content=encoded, encode=0
    )
    logger.info("Wrote %d bytes to %s on VM %s via guest agent", len(content), path, vmid)


def _node_ssh_client() -> Any:
    cfg = get_proxmox_settings()
    host = get_active_host()
    ssh_user = cfg.user.split("@")[0] if "@" in cfg.user else cfg.user
    return create_password_client(host, 22, ssh_user, cfg.password, timeout=30)


def write_file_lxc(
    _node: str, vmid: int, path: str, content: bytes, *, perms: str = "0644"
) -> None:
    validate_target_path(path)
    client = _node_ssh_client()
    tmp_path = f"/tmp/skylab-push-{uuid.uuid4().hex}"
    try:
        sftp = client.open_sftp()
        try:
            with sftp.file(tmp_path, "wb") as handle:
                handle.write(content)
        finally:
            sftp.close()
        code, _out, err = exec_command(
            client,
            f"pct push {int(vmid)} {tmp_path} {shlex.quote(path)} --perms {perms}",
            timeout=60,
        )
        if code != 0:
            raise AppError(
                f"pct push 失敗（VMID {vmid}）：{(err or _out or '').strip()[:300]}",
                502,
            )
        logger.info("Pushed %d bytes to %s on CT %s", len(content), path, vmid)
    finally:
        try:
            exec_command(client, f"rm -f {tmp_path}", timeout=10)
        except Exception:
            logger.debug("Temp cleanup failed for %s", tmp_path)
        client.close()
