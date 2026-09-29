"""list_vm_firewall_rules_strict：讀不到規則要拋錯，不可當成「沒有規則」。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import ProxmoxError
from app.services.network import firewall_service as fw


def _api_returning(get) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    return SimpleNamespace(rules=SimpleNamespace(get=get))


def test_strict_listing_returns_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"pos": 0, "comment": "SkyLab:x"}]
    monkeypatch.setattr(fw, "_firewall_api", lambda *_a: _api_returning(lambda: rows))
    assert fw.list_vm_firewall_rules_strict("pve1", 101, "qemu") == rows


def test_strict_listing_maps_none_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fw, "_firewall_api", lambda *_a: _api_returning(lambda: None))
    assert fw.list_vm_firewall_rules_strict("pve1", 101, "qemu") == []


def test_strict_listing_raises_when_pve_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> list[dict]:
        raise RuntimeError("node offline")

    monkeypatch.setattr(fw, "_firewall_api", lambda *_a: _api_returning(boom))
    with pytest.raises(ProxmoxError):
        fw.list_vm_firewall_rules_strict("pve1", 101, "qemu")
    # 寬鬆版維持原本的行為（回空清單），不影響既有呼叫端
    assert fw.get_vm_firewall_rules("pve1", 101, "qemu") == []
