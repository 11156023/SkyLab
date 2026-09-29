"""初始化精靈的「測試 PVE 連線」必須全程使用表單填的 API port。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.setup import SetupProxmoxTestRequest
from app.services.system import setup_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


def test_test_proxmox_passes_form_port_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_resolve_verify(
        host: str, verify_ssl: bool, ca_cert: str | None, port: int = 8006
    ) -> bool:
        seen["verify_port"] = port
        return verify_ssl

    def fake_fetch_cluster_nodes(**kwargs: Any) -> list[dict]:
        seen["fetch_port"] = kwargs.get("port")
        return [
            {"name": "pve1", "host": "10.0.0.1", "is_primary": True},
            {"name": "pve2", "host": "10.0.0.2", "port": 8443},
        ]

    def fake_open_client(host: str, **kwargs: Any) -> Any:
        seen["client_port"] = kwargs["port"]
        return SimpleNamespace()

    monkeypatch.setattr(setup_service, "resolve_verify", fake_resolve_verify)
    monkeypatch.setattr(setup_service, "fetch_cluster_nodes", fake_fetch_cluster_nodes)
    monkeypatch.setattr(setup_service, "open_client", fake_open_client)
    monkeypatch.setattr(setup_service, "_collect_storages", lambda _c, _n: [])

    result = setup_service.test_proxmox(
        data=SetupProxmoxTestRequest(
            host="pve.example", port=443, user="root@pam", password="pw"
        )
    )

    assert result.success is True
    assert seen == {"verify_port": 443, "fetch_port": 443, "client_port": 443}
    # 節點沒帶 port 時退回這次連線的 port，而不是寫死 8006
    assert [(n.name, n.port) for n in result.nodes] == [("pve1", 443), ("pve2", 8443)]
