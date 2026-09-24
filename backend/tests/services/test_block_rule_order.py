"""額外封鎖網段的 out-DROP 必須排在「不限目的的 out ACCEPT」之前。

2026-09-21 實機查到：gateway:default（out ACCEPT、不限目的）排在所有封鎖 DROP
前面，PVE 先匹配先贏，管理網段的封鎖等於沒有。這裡鎖住排序、孤兒清理與
「封鎖網段不可與實驗室子網重疊」三條規則。
"""

from __future__ import annotations

import ipaddress
from typing import Any

import pytest

from app.services.network import firewall_service as fw
from app.services.network import ip_management_service as ipm

GATEWAY = {"type": "out", "action": "ACCEPT", "comment": "SkyLab:gateway:default"}
FULL_ACCESS = {
    "type": "in",
    "action": "ACCEPT",
    "source": "10.10.0.2",
    "comment": "SkyLab:gateway:full-access",
}
WHITELIST = {
    "type": "out",
    "action": "ACCEPT",
    "dest": "10.10.0.19",
    "comment": "SkyLab:class-net:704664cd:441>487:tcp/16000",
}


def _block(dest: str) -> dict[str, Any]:
    return {
        "type": "out",
        "action": "DROP",
        "dest": dest,
        "comment": fw._extra_block_comment(dest),
    }


def _numbered(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, "pos": i} for i, r in enumerate(rules)]


# ─── block_rule_insert_pos ───────────────────────────────────────────────────


def test_insert_pos_is_the_first_unrestricted_out_accept() -> None:
    rules = _numbered([FULL_ACCESS, FULL_ACCESS, GATEWAY])
    assert fw.block_rule_insert_pos(rules) == 2


def test_insert_pos_ignores_whitelist_accepts_with_a_dest() -> None:
    """拓撲連線、課程互通的白名單指定了 dest，本來就該排在 DROP 前面。"""
    rules = _numbered([WHITELIST, FULL_ACCESS, GATEWAY])
    assert fw.block_rule_insert_pos(rules) == 2


def test_insert_pos_counts_port_limited_gateway_accepts() -> None:
    """「機器 → Internet」只開特定埠的規則一樣不限目的，也會蓋掉 DROP。"""
    port_only = {
        "type": "out",
        "action": "ACCEPT",
        "proto": "tcp",
        "dport": "443",
        "comment": "SkyLab:441->gateway:443/tcp",
    }
    rules = _numbered([FULL_ACCESS, port_only, GATEWAY])
    assert fw.block_rule_insert_pos(rules) == 1


def test_insert_pos_appends_when_nothing_would_shadow_the_drop() -> None:
    rules = _numbered([FULL_ACCESS, WHITELIST])
    assert fw.block_rule_insert_pos(rules) == 2
    assert fw.block_rule_insert_pos([]) == 0


# ─── is_stale_block_comment ──────────────────────────────────────────────────


def test_stale_block_comments() -> None:
    keep = fw._extra_block_comment("192.168.100.0/24")
    gone = fw._extra_block_comment("10.10.0.0/16")
    desired = {keep}

    assert fw.is_stale_block_comment(keep, desired) is False
    assert fw.is_stale_block_comment(gone, desired) is True
    # 專案改名前的殘留：現行程式不會再更新它們
    assert fw.is_stale_block_comment("campus-cloud:block-extra:f90f109e", desired) is True
    assert fw.is_stale_block_comment("campus-cloud:block-local-subnet", desired) is True
    # 保護 PVE 主機的那條寧可多留；其他規則一律不碰
    assert fw.is_stale_block_comment("campus-cloud:block-proxmox-host", desired) is False
    assert fw.is_stale_block_comment("SkyLab:gateway:default", desired) is False
    assert fw.is_stale_block_comment("", desired) is False


# ─── 假的 PVE 防火牆端點 ──────────────────────────────────────────────────────


class _FakeRule:
    def __init__(self, store: list[dict[str, Any]], pos: int) -> None:
        self._store = store
        self._pos = int(pos)

    def put(self, **kwargs: Any) -> None:
        if "moveto" in kwargs:
            rule = self._store.pop(self._pos)
            self._store.insert(int(kwargs["moveto"]), rule)
        else:
            self._store[self._pos].update(kwargs)

    def delete(self) -> None:
        self._store.pop(self._pos)


class _FakeRules:
    def __init__(self, store: list[dict[str, Any]]) -> None:
        self._store = store

    def __call__(self, pos: int) -> _FakeRule:
        return _FakeRule(self._store, pos)

    def get(self) -> list[dict[str, Any]]:
        return _numbered(self._store)

    def post(self, **kwargs: Any) -> None:
        pos = kwargs.pop("pos", 0)  # PVE 不給 pos 會插在最上面
        self._store.insert(int(pos), dict(kwargs))


class _FakeFirewall:
    def __init__(self, store: list[dict[str, Any]]) -> None:
        self.rules = _FakeRules(store)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    monkeypatch.setattr(
        fw, "_firewall_api", lambda node, vmid, resource_type: _FakeFirewall(rules)
    )
    return rules


def _comments(store: list[dict[str, Any]]) -> list[str]:
    return [r.get("comment", "") for r in store]


# ─── _apply_extra_block_rules ────────────────────────────────────────────────


def test_apply_moves_shadowed_blocks_above_gateway_and_cleans_legacy(
    store: list[dict[str, Any]],
) -> None:
    """重現實機 vmid=477 的規則：DROP 全在 gateway:default 下面，還混著舊命名。"""
    mgmt = "192.168.100.0/24"
    store.extend(
        [
            dict(FULL_ACCESS),
            dict(GATEWAY),
            {"type": "out", "action": "DROP", "dest": mgmt,
             "comment": "campus-cloud:block-extra:bd81fec4"},
            {"type": "out", "action": "DROP", "dest": "192.168.100.2",
             "comment": "campus-cloud:block-proxmox-host"},
            _block("10.10.0.0/16"),
            _block(mgmt),
            {"type": "out", "action": "DROP", "dest": "10.10.0.0/16",
             "comment": "campus-cloud:block-local-subnet"},
        ]
    )

    stats = fw._apply_extra_block_rules("pve", 477, "qemu", [mgmt])

    assert stats["errors"] == []
    comments = _comments(store)
    assert comments.index(fw._extra_block_comment(mgmt)) < comments.index(
        "SkyLab:gateway:default"
    )
    # 實驗室子網的封鎖與舊命名殘留都清掉；保護 PVE 主機的那條留著
    assert fw._extra_block_comment("10.10.0.0/16") not in comments
    assert "campus-cloud:block-local-subnet" not in comments
    assert "campus-cloud:block-extra:bd81fec4" not in comments
    assert "campus-cloud:block-proxmox-host" in comments


def test_apply_is_idempotent(store: list[dict[str, Any]]) -> None:
    store.extend([dict(FULL_ACCESS), dict(GATEWAY)])
    targets = ["192.168.100.0/24", "172.16.0.0/12"]

    fw._apply_extra_block_rules("pve", 1, "lxc", targets)
    first = _comments(store)
    stats = fw._apply_extra_block_rules("pve", 1, "lxc", targets)

    assert _comments(store) == first
    assert stats["created"] == [] and stats["updated"] == []
    gateway_pos = first.index("SkyLab:gateway:default")
    for target in targets:
        assert first.index(fw._extra_block_comment(target)) < gateway_pos


def test_apply_with_empty_targets_removes_every_block(
    store: list[dict[str, Any]],
) -> None:
    store.extend([dict(GATEWAY), _block("192.168.100.0/24"), _block("10.10.0.0/16")])

    fw._apply_extra_block_rules("pve", 1, "lxc", [])

    assert _comments(store) == ["SkyLab:gateway:default"]


# ─── enforce_block_rule_order ────────────────────────────────────────────────


def test_enforce_order_after_gateway_rule_is_recreated_on_top(
    store: list[dict[str, Any]],
) -> None:
    """拓撲頁重新拉「機器 → Internet」時，補建的 ACCEPT 會插在最上面。"""
    a, b = _block("192.168.100.0/24"), _block("172.16.0.0/12")
    store.extend([dict(WHITELIST), a, b])
    fw._firewall_api("pve", 1, "lxc").rules.post(**GATEWAY)
    assert _comments(store)[0] == "SkyLab:gateway:default"

    moved = fw.enforce_block_rule_order("pve", 1, "lxc")

    assert moved == 2
    comments = _comments(store)
    gateway_pos = comments.index("SkyLab:gateway:default")
    assert comments.index(a["comment"]) < gateway_pos
    assert comments.index(b["comment"]) < gateway_pos
    assert fw.enforce_block_rule_order("pve", 1, "lxc") == 0


# ─── 封鎖網段不可與實驗室子網重疊 ─────────────────────────────────────────────


def test_blocked_subnet_overlapping() -> None:
    lab = ipaddress.IPv4Network("10.10.0.0/16")

    assert ipm.blocked_subnet_overlapping(lab, ["192.168.100.0/24"]) is None
    assert ipm.blocked_subnet_overlapping(lab, []) is None
    assert ipm.blocked_subnet_overlapping(lab, ["10.10.0.0/16"]) == "10.10.0.0/16"
    # 更大或更小的網段一樣算重疊
    assert ipm.blocked_subnet_overlapping(lab, ["10.0.0.0/8"]) == "10.0.0.0/8"
    assert ipm.blocked_subnet_overlapping(lab, ["10.10.5.0/24"]) == "10.10.5.0/24"
    assert ipm.blocked_subnet_overlapping(lab, ["10.10.0.7"]) == "10.10.0.7"
    # PVE alias／ipset 名稱無從比對，放行
    assert ipm.blocked_subnet_overlapping(lab, ["+mgmt", "192.168.100.0/24"]) is None
