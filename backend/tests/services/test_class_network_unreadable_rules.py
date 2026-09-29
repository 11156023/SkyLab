"""sync_scope_rules：讀不到 PVE 防火牆規則時要回報錯誤，不可當成「沒有規則」。"""

from __future__ import annotations

import pytest

from app.exceptions import ProxmoxError
from app.services.teaching import class_network_service

PREFIX = class_network_service.COMMENT_PREFIX


def test_unreadable_rules_are_reported_and_nothing_is_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict] = []
    deleted: list[int] = []

    def unreadable(_node: str, _vmid: int, _type: str) -> list[dict]:
        raise ProxmoxError("PVE unavailable")

    monkeypatch.setattr(
        class_network_service.proxmox_service,
        "find_resource",
        lambda vmid: {"node": "pve1", "type": "qemu", "vmid": vmid},
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "list_vm_firewall_rules_strict",
        unreadable,
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "create_rule",
        lambda _node, _vmid, _type, rule: created.append(rule),
    )
    monkeypatch.setattr(
        class_network_service.firewall_service,
        "delete_rule_by_pos",
        lambda _node, _vmid, _type, pos: deleted.append(pos),
    )

    errors = class_network_service.sync_scope_rules(
        comment_prefix=PREFIX,
        scope_vmids={101},
        planned=[
            class_network_service.PlannedRule(
                vmid=101,
                comment=f"{PREFIX}abc12345:101>102:any",
                rule={"type": "out", "action": "ACCEPT"},
            )
        ],
    )

    assert errors == ["101: firewall rules unreadable"]
    assert created == []
    assert deleted == []
