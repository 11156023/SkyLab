"""VNC 主控台的剪貼簿旗標：由 VM 的 Display 設定（vga clipboard=vnc）判定。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.routes import vm as vm_routes
from app.services.resource import console_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.mark.parametrize(
    ("vga", "expected"),
    [
        ("std,clipboard=vnc", True),
        ("clipboard=vnc,type=qxl", True),
        ("std", False),
        ("type=std,clipboard=", False),
        (None, False),
        (123, False),
    ],
)
def test_vnc_clipboard_enabled_parses_vga(vga: Any, expected: bool) -> None:
    config = {"vga": vga} if vga is not None else {}
    assert console_service.vnc_clipboard_enabled(config) is expected


def test_get_vnc_clipboard_enabled_reads_effective_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_get_config(
        node: str, vmid: int, resource_type: str, *, current: bool = False
    ) -> dict:
        calls.append((node, vmid, resource_type, current))
        return {"vga": "std,clipboard=vnc"}

    monkeypatch.setattr(console_service.proxmox_service, "get_config", fake_get_config)

    assert console_service.get_vnc_clipboard_enabled(node="pve", vmid=105) is True
    # 改 Display 要重開機才生效，判定要看實際生效中的設定而非 pending 值
    assert calls == [("pve", 105, "qemu", True)]


def test_console_endpoint_reports_clipboard_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        vm_routes.proxmox_service, "list_booting_vmids", lambda nodes: set()
    )

    async def fake_session_ticket(node: str) -> tuple[str, str]:
        return ("PVEAuthCookie=x", "csrf")

    async def fake_vnc_ticket(node: str, vmid: int, cookie: str, csrf: str) -> dict:
        return {"ticket": "PVEVNC:abc", "port": 5900}

    monkeypatch.setattr(
        vm_routes.proxmox_service, "get_session_ticket", fake_session_ticket
    )
    monkeypatch.setattr(
        vm_routes.proxmox_service, "get_vnc_ticket_with_session", fake_vnc_ticket
    )
    monkeypatch.setattr(vm_routes, "register_vnc_session_cookie", lambda *a, **k: None)
    monkeypatch.setattr(
        vm_routes.console_service,
        "get_vnc_clipboard_enabled",
        lambda *, node, vmid: node == "pve" and vmid == 105,
    )

    result = asyncio.run(
        vm_routes.get_vm_console(105, {"type": "qemu", "node": "pve", "vmid": 105})
    )

    assert result["ticket"] == "PVEVNC:abc"
    assert result["port"] == "5900"
    assert result["clipboard"] is True
