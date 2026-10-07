"""入口節點斷線後，快取的 PVE client 要讓位給重新探測。

client 會快取近兩小時；入口節點一斷就沿用到 ticket 過期的話，同連線的每個
請求都要等滿 API timeout（部署環境看到的是逐節點列 LXC 範本時每台卡 30 秒），
即使叢集其他節點都正常。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import requests

from app.infrastructure.proxmox import client as proxmox_client

NODES = [
    SimpleNamespace(id=1, name="pve-a", host="10.0.0.1", port=8006),
    SimpleNamespace(id=2, name="pve-b", host="10.0.0.2", port=8006),
]


class _Session:
    """假的 proxmoxer session：依 ``error`` 決定要不要丟例外。"""

    def __init__(self) -> None:
        self.error: Exception | None = None

    def request(self, method: str, url: str, **_kwargs: Any) -> str:
        if self.error is not None:
            raise self.error
        return f"{method} {url}"


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host
        self.session = _Session()
        self._store = {"session": self.session}


@pytest.fixture(autouse=True)
def _reset_client_state():
    proxmox_client.invalidate_proxmox_client()
    yield
    proxmox_client.invalidate_proxmox_client()


@pytest.fixture
def cluster(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"down": set(), "clients": []}

    def connect(host: str, _cfg: Any) -> _Client:
        client = _Client(host)
        state["clients"].append(client)
        return client

    monkeypatch.setattr(
        proxmox_client,
        "get_proxmox_settings",
        lambda _connection_id=None: SimpleNamespace(
            connection_id=None, connection_name=None, host="10.0.0.1"
        ),
    )
    monkeypatch.setattr(proxmox_client, "get_nodes_for_ha", lambda _cid=None: NODES)
    monkeypatch.setattr(
        proxmox_client, "_tcp_ping", lambda host, _port: host not in state["down"]
    )
    monkeypatch.setattr(proxmox_client, "update_node_online", lambda *_args: None)
    monkeypatch.setattr(proxmox_client, "try_connect", connect)
    return state


def test_connect_error_fails_over_to_next_node(cluster: dict[str, Any]) -> None:
    first = proxmox_client.get_proxmox_api()
    assert first.host == "10.0.0.1"

    cluster["down"].add("10.0.0.1")
    first.session.error = requests.exceptions.ConnectTimeout("connect timeout=30")
    with pytest.raises(requests.exceptions.ConnectTimeout):
        first._store["session"].request("GET", "/nodes/pve-a/storage/ISO/content")

    second = proxmox_client.get_proxmox_api()
    assert second is not first
    assert second.host == "10.0.0.2"
    assert proxmox_client.get_active_host() == "10.0.0.2"


def test_read_timeout_keeps_cached_client(cluster: dict[str, Any]) -> None:
    first = proxmox_client.get_proxmox_api()
    first.session.error = requests.exceptions.ReadTimeout("read timeout=30")

    with pytest.raises(requests.exceptions.ReadTimeout):
        first._store["session"].request("GET", "/cluster/resources")

    assert proxmox_client.get_proxmox_api() is first
    assert len(cluster["clients"]) == 1


def test_late_error_from_replaced_client_keeps_new_client(
    cluster: dict[str, Any],
) -> None:
    first = proxmox_client.get_proxmox_api()
    first.session.error = requests.exceptions.ConnectionError("reset")
    with pytest.raises(requests.exceptions.ConnectionError):
        first._store["session"].request("GET", "/version")
    second = proxmox_client.get_proxmox_api()

    # 舊 client 上還在跑的請求晚一步失敗，不能把已換上的新 client 丟掉
    with pytest.raises(requests.exceptions.ConnectionError):
        first._store["session"].request("GET", "/version")

    assert proxmox_client.get_proxmox_api() is second


def test_successful_requests_pass_through(cluster: dict[str, Any]) -> None:
    client = proxmox_client.get_proxmox_api()

    assert client._store["session"].request("GET", "/version") == "GET /version"
    assert proxmox_client.get_proxmox_api() is client
