"""app.infrastructure.proxmox.client 的分支覆蓋：節點映射、HA 探測、client 快取等待與任務輪詢。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
import requests

from app.exceptions import ProxmoxError
from app.infrastructure.proxmox import client
from app.infrastructure.proxmox.settings import ProxmoxSettings
from app.repositories import proxmox_node as node_repo


@pytest.fixture(autouse=True)
def _fresh_client_state() -> Any:
    client.invalidate_proxmox_client()
    yield
    client.invalidate_proxmox_client()


def _settings(**overrides: Any) -> ProxmoxSettings:
    values: dict[str, Any] = {
        "host": "10.0.0.1",
        "user": "root@pam",
        "password": "pw",
        "verify_ssl": False,
        "iso_storage": "local",
        "data_storage": "local-lvm",
        "api_timeout": 20,
        "task_check_interval": 7,
        "pool_name": "SkyLab",
        "connection_id": 1,
        "connection_name": "lab",
    }
    values.update(overrides)
    return ProxmoxSettings(**values)


def _db_nodes(monkeypatch: pytest.MonkeyPatch, nodes: list[Any]) -> list[int]:
    calls: list[int] = []

    def _get_all_nodes(_session: Any, **_kwargs: Any) -> list[Any]:
        calls.append(1)
        return nodes

    monkeypatch.setattr(node_repo, "get_all_nodes", _get_all_nodes)
    return calls


_NODES = [
    SimpleNamespace(name="pve1", connection_id=1, host="10.0.0.11"),
    SimpleNamespace(name="pve2", connection_id=1, host="10.0.0.12"),
    SimpleNamespace(name="pve9", connection_id=2, host="10.0.9.1"),
]


# ---------------------------------------------------------------------------
# node → connection map
# ---------------------------------------------------------------------------


def test_node_map_refreshes_once_then_serves_from_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _db_nodes(monkeypatch, _NODES)

    assert client.get_connection_id_for_node("pve9") == 2
    assert client.get_node_host("pve1") == "10.0.0.11"
    assert client.get_connection_id_for_node("pve2") == 1
    assert len(calls) == 1


def test_get_nodes_for_connection_refreshes_stale_map_then_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _db_nodes(monkeypatch, _NODES)

    assert client.get_nodes_for_connection(1) == {"pve1", "pve2"}
    assert client.get_nodes_for_connection(2) == {"pve9"}
    assert len(calls) == 1


def test_get_host_and_active_host_fall_back_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _db_nodes(monkeypatch, _NODES)
    monkeypatch.setattr(
        client, "get_proxmox_settings", lambda cid=None: _settings(host=f"entry-{cid}")
    )

    assert client.get_active_host(5) == "entry-5"
    assert client.get_host_for_node("pve9") == "entry-2"

    client._get_state(2).active_host = "10.0.9.1"
    assert client.get_host_for_node("pve9") == "10.0.9.1"


# ---------------------------------------------------------------------------
# _connect_proxmox
# ---------------------------------------------------------------------------


class _FakeProxmoxClient:
    def __init__(self) -> None:
        self._store = {"session": requests.Session()}


def test_connect_fails_over_after_api_error_then_reports_last_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ha_nodes = [
        SimpleNamespace(id=1, name="pve1", host="10.0.0.11", port=8006),
        SimpleNamespace(id=2, name="pve2", host="10.0.0.12", port=8006),
    ]
    online: list[tuple[int, bool]] = []
    monkeypatch.setattr(client, "get_proxmox_settings", lambda _cid=None: _settings())
    monkeypatch.setattr(client, "get_nodes_for_ha", lambda _cid=None: ha_nodes)
    monkeypatch.setattr(client, "_tcp_ping", lambda _h, _p: True)
    monkeypatch.setattr(
        client, "update_node_online", lambda nid, ok: online.append((nid, ok))
    )

    def _connect(host: str, _cfg: Any) -> Any:
        raise RuntimeError(f"401 auth failed on {host}")

    monkeypatch.setattr(client, "try_connect", _connect)

    with pytest.raises(ProxmoxError, match="401 auth failed on 10.0.0.12"):
        client._connect_proxmox(1)
    assert online == [(1, False), (2, False)]


def test_connect_without_ha_nodes_uses_configured_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeProxmoxClient()
    seen: list[str] = []
    monkeypatch.setattr(client, "get_proxmox_settings", lambda _cid=None: _settings())
    monkeypatch.setattr(client, "get_nodes_for_ha", lambda _cid=None: [])

    def _connect(host: str, _cfg: Any) -> Any:
        seen.append(host)
        return fake

    monkeypatch.setattr(client, "try_connect", _connect)

    result, host = client._connect_proxmox(None)

    assert (result, host, seen) == (fake, "10.0.0.1", ["10.0.0.1"])
    assert fake._store["session"].request.__name__ == "request"


# ---------------------------------------------------------------------------
# get_proxmox_api: waiting for another prober
# ---------------------------------------------------------------------------


def test_waiting_caller_returns_client_published_by_probe_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = object()
    state = client._get_state(3)

    class _Event(threading.Event):
        def wait(self, timeout: float | None = None) -> bool:
            # 模擬另一個執行緒完成探測後發布 client
            state.client = fake  # type: ignore[assignment]
            state.created_at = client.time.monotonic()
            state.connecting = False
            return True

    state.connecting = True
    state.connection_event = _Event()

    def _never(_cid: Any) -> Any:
        raise AssertionError("waiting caller must not probe")

    monkeypatch.setattr(client, "_connect_proxmox", _never)

    assert client.get_proxmox_api(3) is fake


# ---------------------------------------------------------------------------
# _task_log_tail / basic_blocking_task_status
# ---------------------------------------------------------------------------


class _TaskApi:
    def __init__(self, statuses: list[Any], log: Any = None) -> None:
        self.statuses = list(statuses)
        self.log_result = log

    def nodes(self, _node: str) -> _TaskApi:
        return self

    def tasks(self, _task: str) -> _TaskApi:
        return self

    @property
    def status(self) -> Any:
        return SimpleNamespace(get=self._status)

    @property
    def log(self) -> Any:
        return SimpleNamespace(get=self._log)

    def _status(self) -> Any:
        result = self.statuses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def _log(self) -> Any:
        if isinstance(self.log_result, BaseException):
            raise self.log_result
        return self.log_result


def test_task_log_tail_returns_empty_when_log_unavailable() -> None:
    api = _TaskApi([], log=RuntimeError("403"))
    assert client._task_log_tail(api, node_name="pve1", task_id="UPID:x", limit=5) == []


def test_task_log_tail_accepts_plain_string_entries() -> None:
    api = _TaskApi([], log=["first", {"t": "second"}, "  ", {"n": 3}, None])
    assert client._task_log_tail(api, node_name="pve1", task_id="UPID:x", limit=5) == [
        "first",
        "second",
    ]


def test_task_status_uses_connection_interval_and_survives_bad_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _TaskApi(
        [{"status": "running"}, {"status": "stopped", "exitstatus": "WARNINGS: 1"}]
    )
    sleeps: list[float] = []
    monkeypatch.setattr(client.time, "sleep", sleeps.append)
    monkeypatch.setattr(client, "get_proxmox_api_for_node", lambda _n: api)
    monkeypatch.setattr(client, "get_connection_id_for_node", lambda _n: 4)
    monkeypatch.setattr(
        client,
        "get_proxmox_settings",
        lambda cid=None: _settings(task_check_interval=cid),
    )

    def _callback(_data: dict) -> None:
        raise ValueError("ui gone")

    result = client.basic_blocking_task_status(
        "pve1", "UPID:x", progress_callback=_callback
    )

    assert result["exitstatus"] == "WARNINGS: 1"
    assert sleeps == [4]


def test_task_status_keeps_retrying_when_reconnect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _TaskApi([OSError("reset"), {"status": "stopped", "exitstatus": "OK"}])
    acquisitions: list[int] = []

    def _api_for_node(_node: str) -> Any:
        acquisitions.append(1)
        if len(acquisitions) > 1:
            raise ProxmoxError("temporarily unavailable")
        return api

    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(client, "get_proxmox_api_for_node", _api_for_node)

    result = client.basic_blocking_task_status("pve1", "UPID:x", check_interval=1)

    assert result["exitstatus"] == "OK"
    assert len(acquisitions) == 2
