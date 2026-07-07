from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from proxmoxer import ProxmoxAPI

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox.router import (
    get_nodes_for_ha,
    try_connect,
    update_node_online,
)
from app.infrastructure.proxmox.settings import get_proxmox_settings
from app.infrastructure.proxmox.tls import _tcp_ping

logger = logging.getLogger(__name__)

PROXMOX_TICKET_TTL = 7000

class _ProxmoxClientState:
    """共享的 Proxmox 連線快取狀態。"""

    def __init__(self) -> None:
        self.client: ProxmoxAPI | None = None
        self.created_at = 0.0
        self.active_host: str | None = None


_state = _ProxmoxClientState()
_proxmox_lock = threading.Lock()


def invalidate_proxmox_client() -> None:
    with _proxmox_lock:
        _state.client = None
        _state.created_at = 0.0
        _state.active_host = None


def get_proxmox_api() -> ProxmoxAPI:
    now = time.monotonic()
    if _state.client is not None and (now - _state.created_at) < PROXMOX_TICKET_TTL:
        return _state.client

    with _proxmox_lock:
        if _state.client is not None and (now - _state.created_at) < PROXMOX_TICKET_TTL:
            return _state.client

        cfg = get_proxmox_settings()
        nodes = get_nodes_for_ha()

        if nodes:
            last_error: Exception | None = None
            for node in nodes:
                if not _tcp_ping(node.host, node.port):
                    logger.info(
                        "Skipping unreachable Proxmox node %s (%s)",
                        node.name,
                        node.host,
                    )
                    update_node_online(node.id, False)
                    continue

                try:
                    client = try_connect(node.host, cfg)
                    update_node_online(node.id, True)
                    _state.client = client
                    _state.created_at = time.monotonic()
                    _state.active_host = node.host
                    logger.info(
                        "Connected to Proxmox node %s (%s)",
                        node.name,
                        node.host,
                    )
                    return _state.client
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Failed to connect Proxmox node %s (%s): %s",
                        node.name,
                        node.host,
                        exc,
                    )
                    update_node_online(node.id, False)

            raise ProxmoxError(
                f"All Proxmox nodes are unavailable. Last error: {last_error}"
            )

        logger.info("Using configured single Proxmox host %s", cfg.host)
        _state.client = try_connect(cfg.host, cfg)
        _state.created_at = time.monotonic()
        _state.active_host = cfg.host
        return _state.client


def get_active_host() -> str:
    if _state.active_host:
        return _state.active_host
    return get_proxmox_settings().host


def _task_log_tail(
    proxmox: ProxmoxAPI,
    *,
    node_name: str,
    task_id: str,
    limit: int,
) -> list[str]:
    try:
        raw_entries = proxmox.nodes(node_name).tasks(task_id).log.get() or []
    except Exception as exc:
        logger.warning("Failed to fetch task log for %s on %s: %s", task_id, node_name, exc)
        return []

    lines: list[str] = []
    for entry in raw_entries[-max(limit, 0) :]:
        if isinstance(entry, dict):
            text = (
                entry.get("t")
                or entry.get("msg")
                or entry.get("message")
                or entry.get("line")
                or ""
            )
        else:
            text = entry
        rendered = str(text or "").strip()
        if rendered:
            lines.append(rendered)
    return lines


def basic_blocking_task_status(
    node_name: str,
    task_id: str,
    check_interval: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    task_log_tail_lines: int = 8,
    timeout_seconds: float | None = None,
) -> dict:
    """阻塞等待 PVE 任務完成。

    ``timeout_seconds`` 有值時，超過即拋 ``TimeoutError``（任務在 PVE 端
    繼續跑，不會被取消）— 供 best-effort 場景（如挖礦存證快照）設上限。
    """
    if check_interval is None:
        check_interval = get_proxmox_settings().task_check_interval

    proxmox = get_proxmox_api()
    logger.info("Waiting for task %s on node %s", task_id, node_name)
    deadline = (
        time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    )

    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(
                f"PVE task {task_id} on {node_name} did not finish within "
                f"{timeout_seconds:.0f}s"
            )
        data = proxmox.nodes(node_name).tasks(task_id).status.get()

        status = data.get("status", "")
        exitstatus = data.get("exitstatus")

        logger.debug(
            "Task %s status=%s exitstatus=%s",
            task_id,
            status,
            exitstatus,
        )

        if progress_callback is not None:
            try:
                progress_callback(data)
            except Exception as exc:
                logger.warning(
                    "Task progress callback failed for %s on %s: %s",
                    task_id,
                    node_name,
                    exc,
                )

        if status == "stopped":
            if exitstatus == "OK" or (
                isinstance(exitstatus, str) and exitstatus.startswith("WARNINGS")
            ):
                if exitstatus != "OK":
                    logger.warning(
                        "Task %s completed with warnings: %s",
                        task_id,
                        exitstatus,
                    )
                else:
                    logger.info("Task %s completed successfully", task_id)
                return data

            error_msg = f"Task {task_id} failed with exitstatus: {exitstatus}"
            log_tail = _task_log_tail(
                proxmox,
                node_name=node_name,
                task_id=task_id,
                limit=task_log_tail_lines,
            )
            if log_tail:
                error_msg = f"{error_msg}. Task log tail: {' | '.join(log_tail)}"
            logger.error(error_msg)
            raise ProxmoxError(error_msg)

        time.sleep(check_interval)


async def wait_for_task_status(
    node_name: str,
    task_id: str,
    check_interval: int | None = None,
    progress_callback: Callable[[dict], None] | None = None,
    task_log_tail_lines: int = 8,
) -> dict:
    return await asyncio.to_thread(
        basic_blocking_task_status,
        node_name,
        task_id,
        check_interval,
        progress_callback,
        task_log_tail_lines,
    )
