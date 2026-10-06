from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import requests
from proxmoxer import ProxmoxAPI

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox.router import (
    get_nodes_for_ha,
    try_connect,
    update_node_online,
)
from app.infrastructure.proxmox.settings import (
    get_proxmox_settings,
    invalidate_proxmox_settings_cache,
)
from app.infrastructure.proxmox.tls import _tcp_ping

logger = logging.getLogger(__name__)

PROXMOX_TICKET_TTL = 7000
PROXMOX_FAILURE_CACHE_TTL = 15.0
NODE_CONNECTION_MAP_TTL = 60.0
# 等待 PVE 任務時，輪詢狀態連續連不到 API 多久才放棄（任務本身不受影響）
TASK_STATUS_OUTAGE_TOLERANCE_SECONDS = 300.0

# 連線池的 key：connection_id；None 代表「預設連線」（含舊版單連線相容）。
_ClientKey = int | None


class _ProxmoxClientState:
    """單一連線的 Proxmox client 快取狀態。"""

    def __init__(self) -> None:
        self.client: ProxmoxAPI | None = None
        self.created_at = 0.0
        self.active_host: str | None = None
        self.failure_until = 0.0
        self.last_error: str | None = None
        self.connecting = False
        self.connection_event: threading.Event | None = None


_states: dict[_ClientKey, _ProxmoxClientState] = {}
_states_lock = threading.Lock()

# node 名稱 → (connection_id, node host) 的快取映射（TTL 更新）
_node_connection_map: dict[str, tuple[int | None, str]] = {}
_node_connection_map_at = 0.0
_node_connection_map_lock = threading.Lock()

# 每次 invalidate_proxmox_client() 遞增；依連線設定建立的上層快取（例如 LXC
# 範本清單）記下建立時的世代，世代不同就視為過期。
_client_generation = 0


def client_generation() -> int:
    return _client_generation


def _get_state(key: _ClientKey) -> _ProxmoxClientState:
    with _states_lock:
        state = _states.get(key)
        if state is None:
            state = _ProxmoxClientState()
            _states[key] = state
        return state


def invalidate_proxmox_client() -> None:
    """清除所有連線的 client 快取與節點映射（設定變更後所有連線都可能失效）。"""
    global _node_connection_map_at, _client_generation
    invalidate_proxmox_settings_cache()
    with _states_lock:
        _states.clear()
        _client_generation += 1
    with _node_connection_map_lock:
        _node_connection_map.clear()
        _node_connection_map_at = 0.0


def _refresh_node_connection_map() -> None:
    global _node_connection_map_at
    try:
        from sqlmodel import Session

        from app.core.db import engine
        from app.repositories.proxmox_node import get_all_nodes

        with Session(engine) as session:
            nodes = get_all_nodes(session)
        with _node_connection_map_lock:
            _node_connection_map.clear()
            for node in nodes:
                _node_connection_map[node.name] = (node.connection_id, node.host)
            _node_connection_map_at = time.monotonic()
    except Exception as exc:
        logger.warning("Unable to refresh node-connection map: %s", exc)


def _node_map_entry(node_name: str) -> tuple[int | None, str] | None:
    now = time.monotonic()
    with _node_connection_map_lock:
        fresh = (now - _node_connection_map_at) < NODE_CONNECTION_MAP_TTL
        if fresh and node_name in _node_connection_map:
            return _node_connection_map[node_name]
    _refresh_node_connection_map()
    with _node_connection_map_lock:
        return _node_connection_map.get(node_name)


def get_connection_id_for_node(node_name: str) -> int | None:
    """查出節點所屬的連線 id；查不到時回 None（使用預設連線）。"""
    entry = _node_map_entry(node_name)
    return entry[0] if entry is not None else None


def get_node_host(node_name: str) -> str | None:
    """查出節點自身的 host（IP/hostname）；查不到時回 None。

    SSH（pct exec）必須直接連到節點本身，不能靠 API 入口轉發。
    """
    entry = _node_map_entry(node_name)
    return entry[1] if entry is not None else None


def get_nodes_for_connection(connection_id: int | None) -> set[str]:
    """列出屬於指定連線的所有節點名稱；映射不可用時回空集合。

    clone 不可跨連線：placement 以此把 VM 範本克隆限制在範本所屬連線內。
    """
    now = time.monotonic()
    with _node_connection_map_lock:
        fresh = (now - _node_connection_map_at) < NODE_CONNECTION_MAP_TTL
        if fresh and _node_connection_map:
            return {
                name
                for name, (cid, _host) in _node_connection_map.items()
                if cid == connection_id
            }
    _refresh_node_connection_map()
    with _node_connection_map_lock:
        return {
            name
            for name, (cid, _host) in _node_connection_map.items()
            if cid == connection_id
        }


def _drop_cached_client(connection_id: _ClientKey, client: ProxmoxAPI) -> None:
    """快取中的 client 仍是 ``client`` 時丟掉它，下次呼叫重新做 HA 探測。"""
    state = _get_state(connection_id)
    with _states_lock:
        if state.client is not client:
            return
        logger.warning(
            "Proxmox host %s is unreachable; dropping cached client for "
            "connection %s so the next call re-probes HA nodes",
            state.active_host,
            connection_id,
        )
        state.client = None
        state.created_at = 0.0
        state.active_host = None


def _watch_connection_errors(connection_id: _ClientKey, client: ProxmoxAPI) -> None:
    """入口節點連不上時讓快取的 client 失效，而不是沿用到 ticket 過期。

    client 會快取 ``PROXMOX_TICKET_TTL``（近兩小時）；沒有這層的話入口節點
    一斷，同連線的每個請求都要等滿 API timeout，即使叢集其他節點都正常。
    只看連線層錯誤（含 ConnectTimeout、TLS 握手失敗）；讀取逾時代表連得上
    只是慢，不換節點。原例外照常拋出，由呼叫端處理。
    """
    # proxmoxer 2.x 把所有請求都送進這個 requests.Session
    session = client._store["session"]
    send = session.request

    def request(*args: Any, **kwargs: Any) -> requests.Response:
        try:
            response: requests.Response = send(*args, **kwargs)
            return response
        except requests.exceptions.ConnectionError:
            _drop_cached_client(connection_id, client)
            raise

    session.request = request


def _connect_proxmox(connection_id: _ClientKey) -> tuple[ProxmoxAPI, str]:
    """Probe the connection's nodes and return a validated client and active host.

    Network I/O deliberately happens outside the state lock.  Callers are
    coordinated by ``get_proxmox_api`` so only one probe is active at a time
    per connection.
    """
    cfg = get_proxmox_settings(connection_id)
    nodes = get_nodes_for_ha(
        connection_id if connection_id is not None else cfg.connection_id
    )

    if nodes:
        last_error: Exception | None = None
        unreachable: list[str] = []
        for node in nodes:
            if not _tcp_ping(node.host, node.port):
                unreachable.append(f"{node.name} ({node.host})")
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
                logger.info(
                    "Connected to Proxmox node %s (%s) via connection %s",
                    node.name,
                    node.host,
                    cfg.connection_name or "default",
                )
                _watch_connection_errors(connection_id, client)
                return client, node.host
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Failed to connect Proxmox node %s (%s): %s",
                    node.name,
                    node.host,
                    exc,
                )
                update_node_online(node.id, False)

        detail = str(last_error) if last_error else (
            "TCP connection failed for " + ", ".join(unreachable)
        )
        raise ProxmoxError(
            f"All Proxmox nodes are unavailable for connection "
            f"{cfg.connection_name or cfg.host}. {detail}"
        )

    logger.info("Using configured Proxmox host %s", cfg.host)
    client = try_connect(cfg.host, cfg)
    _watch_connection_errors(connection_id, client)
    return client, cfg.host


def get_proxmox_api(connection_id: _ClientKey = None) -> ProxmoxAPI:
    """取得指定連線的 ProxmoxAPI client（None = 預設連線）。"""
    state = _get_state(connection_id)
    now = time.monotonic()
    if state.client is not None and (now - state.created_at) < PROXMOX_TICKET_TTL:
        return state.client

    while True:
        with _states_lock:
            now = time.monotonic()
            if (
                state.client is not None
                and (now - state.created_at) < PROXMOX_TICKET_TTL
            ):
                return state.client
            if now < state.failure_until:
                retry_in = max(state.failure_until - now, 0.0)
                raise ProxmoxError(
                    "Proxmox is temporarily unavailable; "
                    f"retry in {retry_in:.1f}s. {state.last_error or ''}".strip()
                )
            if state.connecting:
                connection_event = state.connection_event
                is_probe_owner = False
            else:
                connection_event = threading.Event()
                state.connection_event = connection_event
                state.connecting = True
                is_probe_owner = True

        if is_probe_owner:
            break
        if connection_event is not None:
            connection_event.wait()

    try:
        client, active_host = _connect_proxmox(connection_id)
    except Exception as exc:
        with _states_lock:
            state.client = None
            state.created_at = 0.0
            state.active_host = None
            state.failure_until = time.monotonic() + PROXMOX_FAILURE_CACHE_TTL
            state.last_error = str(exc)
            state.connecting = False
            if state.connection_event is not None:
                state.connection_event.set()
        raise

    with _states_lock:
        state.client = client
        state.created_at = time.monotonic()
        state.active_host = active_host
        state.failure_until = 0.0
        state.last_error = None
        state.connecting = False
        if state.connection_event is not None:
            state.connection_event.set()
        return client


def get_proxmox_api_for_node(node_name: str) -> ProxmoxAPI:
    """取得可操作指定節點的 ProxmoxAPI client（依節點歸屬路由連線）。"""
    return get_proxmox_api(get_connection_id_for_node(node_name))


def get_active_host(connection_id: _ClientKey = None) -> str:
    state = _get_state(connection_id)
    if state.active_host:
        return state.active_host
    return get_proxmox_settings(connection_id).host


def get_host_for_node(node_name: str) -> str:
    """回傳可存取指定節點的 API 入口 host（該節點所屬連線的 active host）。"""
    return get_active_host(get_connection_id_for_node(node_name))


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
        check_interval = get_proxmox_settings(
            get_connection_id_for_node(node_name)
        ).task_check_interval

    proxmox = get_proxmox_api_for_node(node_name)
    logger.info("Waiting for task %s on node %s", task_id, node_name)
    deadline = (
        time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    )

    outage_started: float | None = None

    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(
                f"PVE task {task_id} on {node_name} did not finish within "
                f"{timeout_seconds:.0f}s"
            )
        try:
            data = proxmox.nodes(node_name).tasks(task_id).status.get()
        except OSError as exc:
            # 只是問進度時連不到 API（requests 的 timeout／連線錯誤都是 OSError；
            # PVE 回的 HTTP 錯誤是 ResourceException，不在此列）：任務在 PVE 端
            # 照跑，不能當成任務失敗。連續斷線超過容忍時間才放棄。
            now = time.monotonic()
            if outage_started is None:
                outage_started = now
            unreachable_for = now - outage_started
            if unreachable_for >= TASK_STATUS_OUTAGE_TOLERANCE_SECONDS:
                error_msg = (
                    f"Lost contact with PVE for {unreachable_for:.0f}s while "
                    f"waiting for task {task_id} on {node_name}; the task may "
                    f"still be running on PVE: {exc}"
                )
                logger.error(error_msg)
                raise ProxmoxError(error_msg) from exc
            logger.warning(
                "Polling task %s on %s failed (%s); retrying, unreachable for %.0fs",
                task_id,
                node_name,
                exc,
                unreachable_for,
            )
            time.sleep(check_interval)
            continue
        outage_started = None

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
