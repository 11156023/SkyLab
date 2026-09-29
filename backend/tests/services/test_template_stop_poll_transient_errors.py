"""範本 convert／update 前的關機輪詢：單次狀態查詢失敗不可中斷任務。"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.template import template_service


def test_wait_until_stopped_keeps_polling_after_transient_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replies: list[Any] = [RuntimeError("pve 502"), {"status": "running"}, {"status": "stopped"}]

    def _status(node: str, vmid: int, resource_type: str) -> dict[str, Any]:
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(template_service.proxmox_ops, "get_status", _status)
    monkeypatch.setattr(template_service, "_POLL_INTERVAL_SECONDS", 0)

    assert template_service._wait_until_stopped("pve1", 101, "qemu", 5) is True
    assert replies == []


def test_wait_until_stopped_never_treats_errors_as_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _always_fails(node: str, vmid: int, resource_type: str) -> dict[str, Any]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(template_service.proxmox_ops, "get_status", _always_fails)
    monkeypatch.setattr(template_service, "_POLL_INTERVAL_SECONDS", 0)

    assert template_service._wait_until_stopped("pve1", 101, "qemu", 0.05) is False
