"""Guest 內的探測與指令執行：QEMU 走 guest agent，LXC 走 node SSH ``pct exec``。

- QEMU：agent ping（丟例外或回 bool 兩種版本）、``get-osinfo``，以及
  agent exec + exec-status 輪詢執行指令；執行前先 ping，agent 未回應回可讀的 400。
- LXC：SSH 到容器所在節點本身（``pct`` 只能操作本機容器）後以 ``pct exec``
  執行 shell 指令；憑節點歸屬路由到正確連線的帳密。
"""

from __future__ import annotations

import logging
import shlex
import time
from typing import Any

from app.core.i18n import t
from app.exceptions import AppError
from app.infrastructure.proxmox import (
    get_active_host,
    get_connection_id_for_node,
    get_node_host,
    get_proxmox_api_for_node,
    get_proxmox_settings,
)
from app.infrastructure.ssh import create_password_client, exec_command

logger = logging.getLogger(__name__)


def _agent_ping(node: str, vmid: int) -> None:
    """送出 agent ping；agent 未回應時由 PVE 拋出原始例外。"""
    get_proxmox_api_for_node(node).nodes(node).qemu(vmid).agent("ping").post()


def _ping_agent(node: str, vmid: int) -> None:
    try:
        _agent_ping(node, vmid)
    except Exception as exc:
        raise AppError(
            t("guest.agentNotResponding", vmid=vmid),
            400,
        ) from exc


def ping_qemu_agent(node: str, vmid: int) -> bool:
    """agent ping；未回應回 False（不丟例外），供開機後輪詢等待用。"""
    try:
        _agent_ping(node, vmid)
        return True
    except Exception:
        return False


def get_osinfo_qemu(node: str, vmid: int) -> dict[str, Any] | None:
    """agent get-osinfo；失敗回 None（舊版 agent 不支援此指令）。

    正常回傳如 {"id": "mswindows", "name": "Microsoft Windows", ...}。
    """
    try:
        resp = (
            get_proxmox_api_for_node(node)
            .nodes(node)
            .qemu(vmid)
            .agent("get-osinfo")
            .get()
        )
    except Exception:
        return None
    if isinstance(resp, dict):
        result = resp.get("result", resp)
        if isinstance(result, dict):
            return result
    return None


def exec_qemu(
    node: str,
    vmid: int,
    command: list[str],
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
) -> tuple[int, str, str]:
    """透過 guest agent 在 VM 內執行指令，輪詢至結束。

    回傳 (exitcode, stdout, stderr)；PVE 端已解碼 out-data/err-data。
    agent 未回應丟 AppError(400)，逾時丟 AppError(504)。
    """
    _ping_agent(node, vmid)
    api = get_proxmox_api_for_node(node).nodes(node).qemu(vmid)
    started = api.agent("exec").post(command=command)
    pid = int(started["pid"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.agent("exec-status").get(pid=pid)
        if status.get("exited"):
            return (
                int(status.get("exitcode", -1)),
                str(status.get("out-data") or ""),
                str(status.get("err-data") or ""),
            )
        time.sleep(poll_interval)
    raise AppError(t("guest.execTimeout", vmid=vmid, timeout=f"{timeout:.0f}"), 504)


def exec_lxc(
    node: str,
    vmid: int,
    command: str,
    *,
    timeout: float = 60.0,
    stdin: str | None = None,
) -> tuple[int, str, str]:
    """SSH 到容器所在節點，以 ``pct exec`` 在容器內執行 shell 指令。

    ``stdin`` 會餵進容器內的指令（``pct exec`` 會轉接標準輸入）。密碼一類
    的機密要走這條路，不要串進 command —— 指令列在節點上是公開資訊。
    """
    client = _node_ssh_client(node)
    try:
        return exec_command(
            client,
            f"pct exec {int(vmid)} -- /bin/sh -c {shlex.quote(command)}",
            timeout=timeout,
            stdin=stdin,
        )
    finally:
        client.close()


def _node_ssh_client(node: str) -> Any:
    """SSH 到指定節點本身；未知節點時退回其連線的 active host。"""
    connection_id = get_connection_id_for_node(node)
    cfg = get_proxmox_settings(connection_id)
    host = get_node_host(node) or get_active_host(connection_id)
    ssh_user = cfg.user.split("@")[0] if "@" in cfg.user else cfg.user
    return create_password_client(host, 22, ssh_user, cfg.password, timeout=30)

