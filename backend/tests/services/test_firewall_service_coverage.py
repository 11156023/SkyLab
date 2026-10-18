"""app/services/network/firewall_service.py 的分支覆蓋：PVE 規則讀寫、封鎖網段、連線建立／刪除、拓撲。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import app.infrastructure.proxmox.operations as pve_ops
import app.repositories.gateway_config as gw_repo
import app.repositories.nat_rule as nat_repo
import app.repositories.reverse_proxy as rp_repo
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.schemas.firewall import (
    PortSpec,
    PublishedService,
    PublishedServiceCreate,
    PublishedServiceRef,
    TopologyEdge,
)
from app.services.network import firewall_service as fw
from app.services.network import (
    ip_management_service,
    nat_service,
    reverse_proxy_service,
)

GATEWAY_DEFAULT = "SkyLab:gateway:default"
PLATFORM_SSH = "SkyLab:platform:ssh"


# ─── 假 PVE 防火牆端點 ─────────────────────────────────────────────────────────


class _RuleAt:
    def __init__(self, firewall: FakeFirewall, pos: Any) -> None:
        self.firewall = firewall
        self.pos = pos

    def put(self, **kwargs: Any) -> None:
        if "put" in self.firewall.fail:
            raise RuntimeError("put boom")
        self.firewall.calls.append(("put", self.pos, kwargs))
        if self.firewall.on_put is not None:
            self.firewall.on_put(self.pos, kwargs)

    def delete(self) -> None:
        if "delete" in self.firewall.fail:
            raise RuntimeError("delete boom")
        self.firewall.calls.append(("delete", self.pos))


class _Rules:
    def __init__(self, firewall: FakeFirewall) -> None:
        self.firewall = firewall

    def __call__(self, pos: Any) -> _RuleAt:
        return _RuleAt(self.firewall, pos)

    def get(self) -> list[dict] | None:
        if "get" in self.firewall.fail:
            raise RuntimeError("get boom")
        return self.firewall.rules_data

    def post(self, **kwargs: Any) -> None:
        if "post" in self.firewall.fail or (
            self.firewall.fail_post_comment is not None
            and kwargs.get("comment") == self.firewall.fail_post_comment
        ):
            raise RuntimeError("post boom")
        self.firewall.calls.append(("post", kwargs))


class _Options:
    def __init__(self, firewall: FakeFirewall) -> None:
        self.firewall = firewall

    def get(self) -> dict:
        if "options_get" in self.firewall.fail:
            raise RuntimeError("options get boom")
        return self.firewall.options_data

    def put(self, **kwargs: Any) -> None:
        if "options_put" in self.firewall.fail:
            raise RuntimeError("options put boom")
        self.firewall.calls.append(("options_put", kwargs))


class FakeFirewall:
    def __init__(
        self,
        rules: list[dict] | None = None,
        options: dict | None = None,
        fail: tuple[str, ...] = (),
        fail_post_comment: str | None = None,
    ) -> None:
        self.rules_data = rules
        self.options_data = options if options is not None else {}
        self.fail = set(fail)
        self.fail_post_comment = fail_post_comment
        self.calls: list[tuple] = []
        self.on_put: Any = None
        self.rules = _Rules(self)
        self.options = _Options(self)


def _install_fw(
    monkeypatch: pytest.MonkeyPatch, firewall: FakeFirewall
) -> FakeFirewall:
    monkeypatch.setattr(fw, "_firewall_api", lambda node, vmid, rtype: firewall)
    return firewall


# ─── _firewall_api ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rtype", ["qemu", "lxc"])
def test_firewall_api_routes_by_resource_type(
    monkeypatch: pytest.MonkeyPatch, rtype: str
) -> None:
    api = MagicMock()
    monkeypatch.setattr(fw, "get_proxmox_api_for_node", lambda node: api)

    result = fw._firewall_api("pve1", 100, rtype)  # type: ignore[arg-type]

    node_api = api.nodes.return_value
    expected = getattr(node_api, rtype).return_value.firewall
    assert result is expected
    api.nodes.assert_called_with("pve1")
    getattr(node_api, rtype).assert_called_with(100)


# ─── _upsert_marker_rule ─────────────────────────────────────────────────────


def test_upsert_marker_rule_raises_when_rules_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fw(monkeypatch, FakeFirewall(fail=("get",)))
    with pytest.raises(ProxmoxError):
        fw._upsert_marker_rule("pve1", 100, "qemu", dest="10.9.0.0/16", comment="c")


def test_upsert_marker_rule_updates_dest_and_moves_above_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        {"pos": 0, "type": "out", "action": "ACCEPT", "comment": GATEWAY_DEFAULT},
        {
            "pos": 1,
            "type": "out",
            "action": "DROP",
            "dest": "10.8.0.0/16",
            "comment": "c",
        },
    ]
    firewall = _install_fw(monkeypatch, FakeFirewall(rules=rules))

    result = fw._upsert_marker_rule(
        "pve1", 100, "qemu", dest="10.9.0.0/16", comment="c"
    )

    assert result == "updated"
    assert firewall.calls[0] == (
        "put",
        1,
        {
            "type": "out",
            "action": "DROP",
            "dest": "10.9.0.0/16",
            "enable": 1,
            "comment": "c",
        },
    )
    assert firewall.calls[1] == ("put", 1, {"moveto": 0})


def test_upsert_marker_rule_reorder_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pos 缺值時 int(None) 失敗，只記錄警告；dest 相同所以是 skipped
    rules = [
        {
            "pos": None,
            "type": "out",
            "action": "DROP",
            "dest": "10.9.0.0/16",
            "comment": "c",
        }
    ]
    firewall = _install_fw(monkeypatch, FakeFirewall(rules=rules))

    result = fw._upsert_marker_rule(
        "pve1", 100, "qemu", dest="10.9.0.0/16", comment="c"
    )

    assert result == "skipped"
    assert firewall.calls == []


def test_enforce_block_rule_order_swallows_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(node: str, vmid: int, rtype: str) -> Any:
        raise RuntimeError("no api")

    monkeypatch.setattr(fw, "_firewall_api", boom)
    assert fw.enforce_block_rule_order("pve1", 100, "qemu") == 0


# ─── _apply_extra_block_rules ────────────────────────────────────────────────


def test_apply_extra_block_rules_records_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fw(monkeypatch, FakeFirewall(fail=("get",)))

    stats = fw._apply_extra_block_rules("pve1", 100, "qemu", ["10.9.0.0/16"])

    assert stats["errors"] == [{"vmid": 100, "error": "list rules failed: get boom"}]


def test_apply_extra_block_rules_records_delete_and_upsert_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        {
            "pos": 0,
            "type": "out",
            "action": "DROP",
            "comment": "SkyLab:block-extra:deadbeef",
        },
        {
            "pos": 1,
            "type": "out",
            "action": "DROP",
            "comment": "campus-cloud:block-local-subnet",
        },
    ]
    _install_fw(monkeypatch, FakeFirewall(rules=rules, fail=("delete",)))

    def failing_upsert(*args: Any, **kwargs: Any) -> str:
        raise ProxmoxError("upsert boom")

    monkeypatch.setattr(fw, "_upsert_marker_rule", failing_upsert)

    stats = fw._apply_extra_block_rules("pve1", 100, "qemu", ["10.9.0.0/16"])

    assert stats["deleted"] == []
    errors = stats["errors"]
    assert {"vmid": 100, "dest": "10.9.0.0/16", "error": "upsert boom"} in errors
    assert sum(1 for e in errors if "delete orphan" in e["error"]) == 2


# ─── 低階 PVE 包裝 ──────────────────────────────────────────────────────────


def test_get_vm_firewall_rules_none_becomes_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fw(monkeypatch, FakeFirewall(rules=None))
    assert fw.get_vm_firewall_rules("pve1", 100, "qemu") == []


def test_rule_wrappers_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fw(
        monkeypatch,
        FakeFirewall(fail=("post", "put", "delete", "options_get", "options_put")),
    )

    with pytest.raises(ProxmoxError):
        fw.create_rule("pve1", 100, "qemu", {"type": "in"})
    with pytest.raises(ProxmoxError):
        fw.update_rule("pve1", 100, "qemu", 1, {})
    with pytest.raises(ProxmoxError):
        fw.delete_rule_by_pos("pve1", 100, "qemu", 1)
    assert fw.get_firewall_options("pve1", 100, "qemu") == {}
    with pytest.raises(ProxmoxError):
        fw._set_firewall_options("pve1", 100, "qemu", enable=1)


# ─── ensure_firewall_enabled ────────────────────────────────────────────────


def test_ensure_firewall_enabled_logs_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fw(monkeypatch, FakeFirewall(options={}, fail=("options_put",)))
    # 不拋出，只記錄 ERROR
    fw.ensure_firewall_enabled("pve1", 100, "qemu")


# ─── _load_subnet_settings / sync_extra_block_rules ─────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, ([], None, None)),
        (
            SimpleNamespace(gateway_vm_ip="10.0.0.254", platform_ip="10.0.0.5"),
            (["10.9.0.0/16"], "10.0.0.254", "10.0.0.5"),
        ),
    ],
)
def test_load_subnet_settings(
    monkeypatch: pytest.MonkeyPatch, config: Any, expected: tuple
) -> None:
    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda s: config)
    monkeypatch.setattr(
        ip_management_service,
        "get_extra_blocked_subnets",
        lambda c: [] if c is None else ["10.9.0.0/16"],
    )

    assert fw._load_subnet_settings() == expected


def test_sync_extra_block_rules_records_listing_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fw, "_load_subnet_settings", lambda: (["10.9.0.0/16"], None, None)
    )

    def boom() -> list[dict]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(pve_ops, "list_all_resources", boom)

    result = fw.sync_extra_block_rules()

    assert result["extra_blocks"]["targets"] == ["10.9.0.0/16"]
    assert result["extra_blocks"]["errors"] == [{"vmid": None, "error": "pve down"}]


def test_sync_extra_block_rules_aggregates_per_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fw, "_load_subnet_settings", lambda: (["10.9.0.0/16"], None, "10.0.0.5")
    )
    monkeypatch.setattr(
        pve_ops,
        "list_all_resources",
        lambda: [
            {"vmid": 100, "node": "pve1", "type": "qemu"},
            {"vmid": "101", "node": "pve1", "type": "lxc"},
            {"vmid": 102, "node": None, "type": "qemu"},
            {"vmid": 103, "node": "pve2", "type": "qemu"},
        ],
    )
    seen: list[tuple] = []

    def fake_apply(node: str, vmid: int, rtype: str, targets: list[str]) -> dict:
        seen.append((node, vmid, rtype))
        if vmid == 103:
            raise RuntimeError("apply boom")
        return {"created": [f"{vmid}"], "deleted": ["old"]}

    monkeypatch.setattr(fw, "_apply_extra_block_rules", fake_apply)
    ssh_seen: list[tuple] = []

    def fake_platform(node: str, vmid: int, rtype: str, platform_ip: str | None) -> str:
        ssh_seen.append((vmid, platform_ip))
        if vmid == 101:
            raise RuntimeError("ssh boom")
        return "created"

    monkeypatch.setattr(fw, "_apply_platform_ssh_rule", fake_platform)

    full = fw.sync_extra_block_rules()
    result = full["extra_blocks"]

    assert seen == [("pve1", 100, "qemu"), ("pve1", 101, "lxc"), ("pve2", 103, "qemu")]
    assert result["created"] == ["100", "101"]
    assert result["deleted"] == ["old", "old"]
    # 封鎖網段失敗的那台仍要同步主系統 SSH 規則；SSH 規則的失敗併進同一份 errors
    assert ssh_seen == [(100, "10.0.0.5"), (101, "10.0.0.5"), (103, "10.0.0.5")]
    assert result["errors"] == [
        {"vmid": 101, "error": "platform-ssh: ssh boom"},
        {"vmid": 103, "error": "apply boom"},
    ]
    assert full["platform_ssh"]["platform_ip"] == "10.0.0.5"
    assert full["platform_ssh"]["created"] == [100, 103]


# ─── setup_default_rules ────────────────────────────────────────────────────


def test_setup_default_rules_full_path(monkeypatch: pytest.MonkeyPatch) -> None:
    firewall = _install_fw(monkeypatch, FakeFirewall())
    applied: list[tuple] = []
    monkeypatch.setattr(
        fw,
        "_load_subnet_settings",
        lambda: (["10.9.0.0/16"], "10.0.0.254", "10.0.0.5"),
    )

    def fake_apply(node: str, vmid: int, rtype: str, targets: list[str]) -> dict:
        applied.append((vmid, targets))
        return {"created": targets, "updated": [], "deleted": [], "errors": []}

    monkeypatch.setattr(fw, "_apply_extra_block_rules", fake_apply)

    fw.setup_default_rules("pve1", 100, "qemu")

    assert firewall.calls[0] == (
        "options_put",
        {"enable": 1, "policy_in": "DROP", "policy_out": "ACCEPT"},
    )
    posts = [c[1] for c in firewall.calls if c[0] == "post"]
    assert posts[0]["comment"] == GATEWAY_DEFAULT
    assert [p["proto"] for p in posts[1:3]] == ["tcp", "udp"]
    assert all(p["source"] == "10.0.0.254" for p in posts[1:3])
    assert posts[3] == {
        "type": "in",
        "action": "ACCEPT",
        "source": "10.0.0.5",
        "proto": "tcp",
        "dport": "22",
        "enable": 1,
        "comment": PLATFORM_SSH,
        "pos": 0,
    }
    assert applied == [(100, ["10.9.0.0/16"])]


def test_setup_default_rules_settings_unreadable_skips_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(monkeypatch, FakeFirewall())

    def boom() -> tuple:
        raise RuntimeError("db down")

    monkeypatch.setattr(fw, "_load_subnet_settings", boom)

    fw.setup_default_rules("pve1", 100, "lxc")

    posts = [c[1] for c in firewall.calls if c[0] == "post"]
    assert [p["comment"] for p in posts] == [GATEWAY_DEFAULT]


def test_setup_default_rules_nonfatal_step_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(
        monkeypatch,
        FakeFirewall(fail_post_comment="SkyLab:gateway:full-access"),
    )
    monkeypatch.setattr(
        fw, "_load_subnet_settings", lambda: (["10.9.0.0/16"], "10.0.0.254", None)
    )

    def boom(*args: Any) -> dict:
        raise RuntimeError("apply boom")

    monkeypatch.setattr(fw, "_apply_extra_block_rules", boom)

    fw.setup_default_rules("pve1", 100, "qemu")

    posts = [c[1] for c in firewall.calls if c[0] == "post"]
    assert [p["comment"] for p in posts] == [GATEWAY_DEFAULT]


def test_setup_default_rules_platform_ssh_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(monkeypatch, FakeFirewall(fail_post_comment=PLATFORM_SSH))
    monkeypatch.setattr(fw, "_load_subnet_settings", lambda: ([], None, "10.0.0.5"))

    fw.setup_default_rules("pve1", 100, "lxc")

    posts = [c[1] for c in firewall.calls if c[0] == "post"]
    assert [p["comment"] for p in posts] == [GATEWAY_DEFAULT]


# ─── 主系統 SSH 規則 ─────────────────────────────────────────────────────────


def _ssh_rule(pos: int, source: str = "10.0.0.5", **extra: Any) -> dict:
    return {
        "pos": pos,
        "type": "in",
        "action": "ACCEPT",
        "source": source,
        "proto": "tcp",
        "dport": "22",
        "enable": 1,
        "comment": PLATFORM_SSH,
        **extra,
    }


def test_apply_platform_ssh_rule_creates_at_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(
        monkeypatch, FakeFirewall(rules=[{"pos": 0, "comment": GATEWAY_DEFAULT}])
    )

    assert fw._apply_platform_ssh_rule("pve1", 100, "qemu", "10.0.0.5") == "created"
    assert firewall.calls == [
        ("post", {**{k: v for k, v in _ssh_rule(0).items() if k != "pos"}, "pos": 0})
    ]


def test_apply_platform_ssh_rule_updates_source_and_moves_to_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(
        monkeypatch,
        FakeFirewall(rules=[{"pos": 0, "type": "in", "action": "DROP"}, _ssh_rule(1)]),
    )

    assert fw._apply_platform_ssh_rule("pve1", 100, "lxc", "10.0.0.9") == "updated"
    desired = {k: v for k, v in _ssh_rule(0, source="10.0.0.9").items() if k != "pos"}
    assert firewall.calls == [("put", 1, desired), ("put", 1, {"moveto": 0})]


def test_apply_platform_ssh_rule_removes_when_ip_cleared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    firewall = _install_fw(
        monkeypatch, FakeFirewall(rules=[_ssh_rule(0), {"pos": 1}, _ssh_rule(2)])
    )

    assert fw._apply_platform_ssh_rule("pve1", 100, "qemu", None) == "deleted"
    assert firewall.calls == [("delete", 2), ("delete", 0)]


def test_ensure_platform_ssh_rule_applies_configured_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple] = []
    monkeypatch.setattr(
        ip_management_service,
        "get_subnet_config",
        lambda s: SimpleNamespace(platform_ip="10.0.0.5"),
    )
    monkeypatch.setattr(
        fw, "_apply_platform_ssh_rule", lambda *args: applied.append(args) or "created"
    )

    fw.ensure_platform_ssh_rule(object(), "pve1", 100, "qemu")

    assert applied == [("pve1", 100, "qemu", "10.0.0.5")]


@pytest.mark.parametrize("config", [None, SimpleNamespace(platform_ip=None)])
def test_ensure_platform_ssh_rule_noop_without_platform_ip(
    monkeypatch: pytest.MonkeyPatch, config: Any
) -> None:
    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda s: config)
    monkeypatch.setattr(fw, "_firewall_api", lambda *a: pytest.fail("不該碰 PVE"))

    fw.ensure_platform_ssh_rule(object(), "pve1", 100, "qemu")


def test_ensure_platform_ssh_rule_logs_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(s: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(ip_management_service, "get_subnet_config", boom)

    fw.ensure_platform_ssh_rule(object(), "pve1", 100, "qemu")

    assert any(
        r.levelname == "ERROR" and "db down" in r.getMessage() for r in caplog.records
    )


def test_setup_default_rules_raises_when_options_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fw(monkeypatch, FakeFirewall(fail=("options_put",)))
    with pytest.raises(ProxmoxError):
        fw.setup_default_rules("pve1", 100, "qemu")


# ─── VM IP 查詢 ─────────────────────────────────────────────────────────────


def test_get_vm_ip_live_without_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(
            find_resource=lambda vmid: {"node": "pve1", "type": "qemu"},
            get_ip_address=lambda node, vmid, rtype: "10.0.0.5",
        ),
    )
    assert fw._get_vm_ip(100) == "10.0.0.5"


def test_get_vm_ip_falls_back_to_cache_with_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(vmid: int) -> dict:
        raise RuntimeError("pve down")

    monkeypatch.setattr(fw, "proxmox_service", SimpleNamespace(find_resource=boom))
    calls: list[tuple] = []

    def fake_sync(*, session: Any, vmid: int, live_ip: str | None) -> str:
        calls.append((vmid, live_ip))
        return "10.0.0.9"

    monkeypatch.setattr(fw.resource_repo, "sync_ip_cache", fake_sync)

    assert fw._get_vm_ip(100, session=object()) == "10.0.0.9"
    assert calls == [(100, None)]


def test_get_publishable_vm_ip_prefers_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fw.resource_repo,
        "get_allocated_ip_address",
        lambda *, session, vmid: "10.0.0.20",
    )
    monkeypatch.setattr(fw, "_get_vm_ip", lambda vmid, session=None: "10.0.0.99")

    assert fw._get_publishable_vm_ip(100, session=object()) == "10.0.0.20"


def test_get_publishable_vm_ip_allocation_error_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*, session: Any, vmid: int) -> str:
        raise RuntimeError("db error")

    monkeypatch.setattr(fw.resource_repo, "get_allocated_ip_address", boom)
    monkeypatch.setattr(fw, "_get_vm_ip", lambda vmid, session=None: "10.0.0.99")

    assert fw._get_publishable_vm_ip(100, session=object()) == "10.0.0.99"
    assert fw._get_publishable_vm_ip(100) == "10.0.0.99"


# ─── comment 解析 ───────────────────────────────────────────────────────────


# ─── _rollback_gateway_rules ────────────────────────────────────────────────


def test_rollback_gateway_rules_logs_removal_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[Any] = []
    monkeypatch.setattr(
        nat_service, "remove_nat_rules", lambda session, rules: removed.extend(rules)
    )

    def boom(session: Any, rules: list) -> None:
        raise RuntimeError("proxy removal boom")

    monkeypatch.setattr(reverse_proxy_service, "remove_reverse_proxy_rules", boom)

    fw._rollback_gateway_rules(object(), 100, ["nat1"], ["proxy1"])

    assert removed == ["nat1"]


# ─── create_connection ──────────────────────────────────────────────────────


@pytest.fixture
def conn_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        resources={
            100: {"node": "pve1", "type": "qemu"},
            200: {"node": "pve2", "type": "lxc"},
        },
        ips={100: "10.0.0.100", 200: "10.0.0.200"},
        rules={100: [], 200: []},
        created=[],
        deleted=[],
        enforced=[],
        asserted=[],
        fail_create_on=None,
    )

    def find_resource(vmid: int) -> dict:
        if vmid not in state.resources:
            raise NotFoundError("missing")
        return state.resources[vmid]

    monkeypatch.setattr(
        fw, "proxmox_service", SimpleNamespace(find_resource=find_resource)
    )

    def fake_create_rule(node: str, vmid: int, rtype: str, rule: dict) -> None:
        if state.fail_create_on is not None and state.fail_create_on(rule):
            raise ProxmoxError("create boom")
        state.created.append((vmid, rule))
        state.rules.setdefault(vmid, []).append(
            {**rule, "pos": len(state.rules.get(vmid, []))}
        )

    monkeypatch.setattr(fw, "create_rule", fake_create_rule)
    monkeypatch.setattr(
        fw,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: list(state.rules.get(vmid, [])),
    )
    monkeypatch.setattr(
        fw,
        "delete_rule_by_pos",
        lambda node, vmid, rtype, pos: state.deleted.append((vmid, pos)),
    )
    monkeypatch.setattr(
        fw,
        "enforce_block_rule_order",
        lambda node, vmid, rtype: state.enforced.append(vmid) or 0,
    )
    monkeypatch.setattr(
        fw, "_get_publishable_vm_ip", lambda vmid, session=None: state.ips.get(vmid)
    )
    monkeypatch.setattr(
        fw,
        "assert_publishable_vm_ip",
        lambda session, ip, *, vmid: state.asserted.append((vmid, ip)),
    )
    return state


def test_create_connection_input_validation(conn_env: SimpleNamespace) -> None:
    port = [PortSpec(port=80, protocol="tcp")]
    with pytest.raises(BadRequestError):
        fw.create_connection(100, 200, [])
    with pytest.raises(BadRequestError):
        fw.create_connection(100, 100, port)
    with pytest.raises(BadRequestError):
        fw.create_connection(None, None, port)
    with pytest.raises(BadRequestError):
        fw.create_connection(None, 999, port)
    with pytest.raises(BadRequestError):
        fw.create_connection(999, 200, port)


def test_create_connection_inbound_requires_session_for_gateway(
    conn_env: SimpleNamespace,
) -> None:
    with pytest.raises(BadRequestError):
        fw.create_connection(
            None, 100, [PortSpec(port=80, protocol="tcp", external_port=8080)]
        )


def test_create_connection_inbound_requires_gateway_config(
    conn_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda session: None)
    with pytest.raises(BadRequestError):
        fw.create_connection(
            None,
            100,
            [PortSpec(port=80, protocol="tcp", domain="a.example.com")],
            session=object(),
        )


def test_create_connection_inbound_requires_target_ip(
    conn_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda session: SimpleNamespace(host="gw", encrypted_private_key="k"),
    )
    conn_env.ips[100] = None
    with pytest.raises(BadRequestError):
        fw.create_connection(
            None,
            100,
            [PortSpec(port=80, protocol="tcp", external_port=8080)],
            session=object(),
        )
    assert conn_env.created == []


def test_create_connection_inbound_rollback_tolerates_delete_errors(
    conn_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn_env.fail_create_on = lambda rule: rule.get("dport") == "22"

    def failing_delete(node: str, vmid: int, rtype: str, pos: int) -> None:
        raise ProxmoxError("delete boom")

    monkeypatch.setattr(fw, "delete_rule_by_pos", failing_delete)

    with pytest.raises(ProxmoxError, match="create boom"):
        fw.create_connection(
            None,
            100,
            [PortSpec(port=80, protocol="tcp"), PortSpec(port=22, protocol="tcp")],
        )


def test_create_connection_inbound_rollback_tolerates_listing_errors(
    conn_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn_env.fail_create_on = lambda rule: rule.get("dport") == "22"

    def failing_list(node: str, vmid: int, rtype: str) -> list[dict]:
        raise ProxmoxError("list boom")

    monkeypatch.setattr(fw, "get_vm_firewall_rules", failing_list)

    with pytest.raises(ProxmoxError, match="create boom"):
        fw.create_connection(
            None,
            100,
            [PortSpec(port=80, protocol="tcp"), PortSpec(port=22, protocol="tcp")],
        )


def test_create_connection_to_gateway_restores_default_marker(
    conn_env: SimpleNamespace,
) -> None:
    fw.create_connection(
        100,
        None,
        [PortSpec(port=443, protocol="tcp"), PortSpec(port=0, protocol="icmp")],
    )

    comments = [rule["comment"] for _vmid, rule in conn_env.created]
    assert comments == [
        GATEWAY_DEFAULT,
        "SkyLab:100->gateway:443/tcp",
        "SkyLab:100->gateway:icmp",
    ]
    assert conn_env.enforced == [100]


def test_create_connection_vm_to_vm_requires_ips(conn_env: SimpleNamespace) -> None:
    port = [PortSpec(port=22, protocol="tcp")]
    conn_env.ips[100] = None
    with pytest.raises(BadRequestError):
        fw.create_connection(100, 200, port)

    conn_env.ips[100] = "10.0.0.100"
    with pytest.raises(BadRequestError):
        fw.create_connection(100, 999, port)

    conn_env.ips[200] = None
    with pytest.raises(BadRequestError):
        fw.create_connection(100, 200, port)
    assert conn_env.created == []


def test_create_connection_vm_to_vm_one_way_validates_ips(
    conn_env: SimpleNamespace,
) -> None:
    conn_env.rules[100] = [
        {"pos": 0, "type": "out", "action": "ACCEPT", "comment": GATEWAY_DEFAULT}
    ]

    fw.create_connection(
        100, 200, [PortSpec(port=22, protocol="tcp")], session=object()
    )

    assert conn_env.asserted == [(100, "10.0.0.100"), (200, "10.0.0.200")]
    assert [(vmid, rule["type"]) for vmid, rule in conn_env.created] == [
        (200, "in"),
        (100, "out"),
    ]
    out_rule = conn_env.created[1][1]
    assert out_rule["pos"] == 0
    assert out_rule["dest"] == "10.0.0.200"


def test_create_connection_vm_to_vm_bidirectional(conn_env: SimpleNamespace) -> None:
    fw.create_connection(
        100,
        200,
        [PortSpec(port=0, protocol="icmp")],
        direction="bidirectional",
    )

    assert conn_env.asserted == []
    assert [
        (vmid, rule["type"], rule["comment"]) for vmid, rule in conn_env.created
    ] == [
        (200, "in", "SkyLab:100->200:icmp"),
        (100, "out", "SkyLab:100->200:icmp"),
        (100, "in", "SkyLab:200->100:icmp"),
        (200, "out", "SkyLab:200->100:icmp"),
    ]
    assert conn_env.created[2][1]["source"] == "10.0.0.200"
    assert conn_env.created[3][1]["dest"] == "10.0.0.100"


# ─── delete_connection ──────────────────────────────────────────────────────


@pytest.fixture
def del_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        resources={
            100: {"node": "pve1", "type": "qemu"},
            200: {"node": "pve2", "type": "lxc"},
        },
        calls=[],
        fail_calls=set(),
        gateway=[],
    )

    def find_resource(vmid: int) -> dict:
        if vmid not in state.resources:
            raise NotFoundError("missing")
        return state.resources[vmid]

    monkeypatch.setattr(
        fw, "proxmox_service", SimpleNamespace(find_resource=find_resource)
    )

    def fake_delete_matching(**kwargs: Any) -> None:
        state.calls.append(kwargs)
        if len(state.calls) in state.fail_calls:
            raise RuntimeError("delete matching boom")

    monkeypatch.setattr(fw, "_delete_matching_rules", fake_delete_matching)
    monkeypatch.setattr(
        nat_service,
        "remove_nat_rules_for_vmid",
        lambda session, vmid: state.gateway.append(("nat_all", vmid)),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "remove_reverse_proxy_rules_for_vmid",
        lambda session, vmid: state.gateway.append(("proxy_all", vmid)),
    )
    monkeypatch.setattr(
        nat_service,
        "remove_nat_rules_by_internal_port",
        lambda session, vmid, port, proto: state.gateway.append(
            ("nat", vmid, port, proto)
        ),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "remove_reverse_proxy_rules_by_internal_port",
        lambda session, vmid, port: state.gateway.append(("proxy", vmid, port)),
    )
    return state


def test_delete_connection_inbound_noops(del_env: SimpleNamespace) -> None:
    fw.delete_connection(None, None)
    fw.delete_connection(None, 999)
    assert del_env.calls == []


def test_delete_connection_inbound_clears_all_gateway_rules(
    del_env: SimpleNamespace,
) -> None:
    fw.delete_connection(None, 100, session=object())
    assert del_env.gateway == [("nat_all", 100), ("proxy_all", 100)]


def test_delete_connection_to_gateway(del_env: SimpleNamespace) -> None:
    fw.delete_connection(999, None)
    assert del_env.calls == []

    fw.delete_connection(100, None)
    assert del_env.calls == [
        {
            "node": "pve1",
            "vmid": 100,
            "resource_type": "qemu",
            "source_vmid": 100,
            "target_vmid": None,
            "ports": None,
        }
    ]


def test_delete_connection_vm_to_vm_missing_resources(del_env: SimpleNamespace) -> None:
    fw.delete_connection(100, 999)
    fw.delete_connection(999, 200)
    assert del_env.calls == []


def test_delete_connection_vm_to_vm_best_effort_failures(
    del_env: SimpleNamespace,
) -> None:
    del_env.fail_calls = {2, 3, 4}

    fw.delete_connection(100, 200)

    assert len(del_env.calls) == 4


# ─── _delete_matching_rules ─────────────────────────────────────────────────


@pytest.fixture
def match_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(rules=[], deleted=[], fail_delete=False)
    monkeypatch.setattr(
        fw, "get_vm_firewall_rules", lambda node, vmid, rtype: state.rules
    )

    def fake_delete(node: str, vmid: int, rtype: str, pos: int) -> None:
        if state.fail_delete:
            raise ProxmoxError("delete boom")
        state.deleted.append(pos)

    monkeypatch.setattr(fw, "delete_rule_by_pos", fake_delete)
    return state


def _delete(
    source: int | None, target: int | None, ports: list[PortSpec] | None
) -> None:
    fw._delete_matching_rules(
        node="pve1",
        vmid=100,
        resource_type="qemu",
        source_vmid=source,
        target_vmid=target,
        ports=ports,
    )


def test_delete_matching_internet_rules(match_env: SimpleNamespace) -> None:
    match_env.rules = [
        {"pos": 0, "comment": "SkyLab:gateway->100:80/tcp"},
        {"pos": 1, "comment": "SkyLab:gateway->100:22/tcp"},
        {"pos": 2, "comment": "SkyLab:gateway->101:80/tcp"},
        {"pos": 3, "comment": "manual rule"},
        {"pos": 4, "comment": None},
        {"pos": 5},
    ]

    _delete(None, 100, [PortSpec(port=80, protocol="tcp")])

    assert match_env.deleted == [0]


def test_delete_matching_gateway_rules_without_port_filter(
    match_env: SimpleNamespace,
) -> None:
    match_env.rules = [
        {"pos": 0, "comment": GATEWAY_DEFAULT},
        {"pos": 1, "comment": "SkyLab:100->gateway:443/tcp"},
        {"pos": 2, "comment": "SkyLab:101->gateway:443/tcp"},
        {"pos": 3, "comment": "SkyLab:gateway->100:80/tcp"},
    ]

    _delete(100, None, None)

    assert match_env.deleted == [1, 0]


def test_delete_matching_vm_to_vm_rules_and_failures(
    match_env: SimpleNamespace,
) -> None:
    match_env.rules = [
        {"pos": 0, "comment": "SkyLab:100->200:22/tcp"},
        {"pos": 1, "comment": "SkyLab:200->100:22/tcp"},
        {"pos": 2, "comment": "SkyLab:100->200:icmp"},
    ]

    _delete(100, 200, None)
    assert match_env.deleted == [2, 0]

    match_env.deleted.clear()
    match_env.fail_delete = True
    _delete(100, 200, [PortSpec(port=22, protocol="tcp")])
    assert match_env.deleted == []


# ─── 連線解析與拓撲聚合 ─────────────────────────────────────────────────────


def test_get_connections_from_rules_skips_unreadable_vm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def find_resource(vmid: int) -> dict:
        if vmid == 999:
            raise NotFoundError("missing")
        return {"node": "pve1", "type": "qemu"}

    monkeypatch.setattr(
        fw, "proxmox_service", SimpleNamespace(find_resource=find_resource)
    )
    monkeypatch.setattr(
        fw,
        "get_vm_firewall_rules",
        lambda node, vmid, rtype: [{"pos": 0, "comment": "SkyLab:gateway->100:80/tcp"}],
    )

    edges = fw.get_connections_from_rules([100, 999])

    assert [(e.source_vmid, e.target_vmid) for e in edges] == [(None, 100)]


def test_edges_from_rules_handles_every_rule_kind() -> None:
    rules_by_vmid = {
        100: [
            {"comment": GATEWAY_DEFAULT},
            {"comment": GATEWAY_DEFAULT},
            {"comment": "SkyLab:100->gateway:443/tcp"},
            {"comment": "SkyLab:100->gateway:443/tcp"},
            {"comment": "SkyLab:100->gateway:icmp"},
            {"comment": "SkyLab:gateway->100:80/tcp"},
            {"comment": "SkyLab:100->200:22/tcp"},
            {"comment": "SkyLab:class-net:c1:100>200:tcp/8080"},
            {"comment": "not managed"},
            {"comment": None},
        ],
        101: [{"comment": "SkyLab:101->gateway:53/udp"}],
        200: [{"comment": GATEWAY_DEFAULT}],
    }

    edges = {
        (e.source_vmid, e.target_vmid): e for e in fw._edges_from_rules(rules_by_vmid)
    }

    assert set(edges) == {
        (100, None),
        (None, 100),
        (100, 200),
        (101, None),
        (200, None),
    }
    assert [(p.port, p.protocol) for p in edges[(100, None)].ports] == [
        (443, "tcp"),
        (0, "icmp"),
    ]
    assert [(p.port, p.protocol) for p in edges[(101, None)].ports] == [(53, "udp")]
    assert edges[(200, None)].ports == []
    assert edges[(100, 200)].topology_managed is True
    assert [(p.port, p.protocol) for p in edges[(100, 200)].ports] == [
        (22, "tcp"),
        (8080, "tcp"),
    ]


def test_enrich_edges_from_db_noop_without_inbound_edges() -> None:
    edges = [TopologyEdge(source_vmid=100, target_vmid=None, ports=[])]
    # 沒有 Internet→VM edge 時不碰 DB（session 傳 None 也不會出錯）
    fw._enrich_edges_from_db(edges, None)  # type: ignore[arg-type]


class _FakeResult:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def all(self) -> list[tuple]:
        return self.rows


class _FakeSession:
    def __init__(self, *results: list[tuple]) -> None:
        self.results = list(results)
        self.statements: list[Any] = []

    def exec(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(self.results.pop(0))


def test_describe_resource_origins_queries_classes_and_owners() -> None:
    class_id = uuid.uuid4()
    viewer = uuid.uuid4()
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    session = _FakeSession(
        [(class_id, "Networking 101")],
        [(owner_a, "Alice", "alice@example.com"), (owner_b, None, "bob@example.com")],
    )
    resources = [
        SimpleNamespace(teaching_class_id=class_id, user_id=owner_a),
        SimpleNamespace(teaching_class_id=None, user_id=owner_b),
        SimpleNamespace(teaching_class_id=None, user_id=viewer),
    ]

    class_names, owner_names = fw._describe_resource_origins(
        session=session,  # type: ignore[arg-type]
        resources=resources,
        viewer_id=viewer,
    )

    assert class_names == {class_id: "Networking 101"}
    assert owner_names == {owner_a: "Alice", owner_b: "bob@example.com"}
    assert len(session.statements) == 2


def test_probe_topology_vm_degrades_on_options_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(node: str, vmid: int, rtype: str) -> dict:
        raise RuntimeError("options boom")

    monkeypatch.setattr(fw, "get_firewall_options", boom)
    monkeypatch.setattr(
        fw, "get_vm_firewall_rules", lambda node, vmid, rtype: [{"pos": 0}]
    )

    assert fw._probe_topology_vm({"vmid": "100", "node": "pve1", "type": "qemu"}) == (
        False,
        [{"pos": 0}],
    )


def test_probe_topology_vms_empty() -> None:
    assert fw._probe_topology_vms([]) == {}


def test_get_topology_uses_saved_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_owned_classes(*, session: Any, user: Any) -> set[Any]:
        return set()

    monkeypatch.setattr(
        fw.resource_access,
        "list_reachable_resources",
        lambda *, session, user: [
            SimpleNamespace(vmid=100, user_id="u1", teaching_class_id=None),
            SimpleNamespace(vmid=101, user_id="u1", teaching_class_id=None),
            # PVE 上找不到的機器不出現在拓撲
            SimpleNamespace(vmid=102, user_id="u1", teaching_class_id=None),
        ],
    )
    monkeypatch.setattr(
        fw.resource_access,
        "list_owned_teaching_class_ids",
        no_owned_classes,
    )
    monkeypatch.setattr(
        fw.resource_access,
        "can_manage_resource",
        lambda *, resource, user, owned_class_ids: True,
    )
    monkeypatch.setattr(
        fw.resource_kind,
        "classify_many",
        lambda session, resources: {101: "quick_practice"},
    )
    monkeypatch.setattr(
        fw.layout_repo,
        "get_layout",
        lambda *, session, user_id: [
            SimpleNamespace(vmid=100, node_type="vm", position_x=11.0, position_y=22.0),
            SimpleNamespace(
                vmid=None, node_type="gateway", position_x=33.0, position_y=44.0
            ),
        ],
    )
    pve = [
        {
            "vmid": 100,
            "node": "pve1",
            "type": "qemu",
            "name": "web",
            "status": "running",
        },
        {"vmid": 101, "node": "pve1", "type": "lxc", "name": "db", "status": "stopped"},
    ]
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(
            list_all_resources=lambda: pve,
            get_ip_address=lambda node, vmid, rtype: None,
        ),
    )
    monkeypatch.setattr(
        fw.live_ip,
        "start_live_ip_probes",
        lambda resources, lookup: SimpleNamespace(collect=lambda: {100: "10.0.0.100"}),
    )
    monkeypatch.setattr(
        fw.resource_repo,
        "sync_ip_cache_many",
        lambda *, session, live_ips: {k: v or "cached" for k, v in live_ips.items()},
    )
    monkeypatch.setattr(
        fw, "get_firewall_options", lambda node, vmid, rtype: {"enable": 1}
    )
    monkeypatch.setattr(fw, "get_vm_firewall_rules", lambda node, vmid, rtype: [])
    monkeypatch.setattr(fw, "_enrich_edges_from_db", lambda edges, session: None)

    resp = fw.get_topology(user=SimpleNamespace(id="u1"), session=object())  # type: ignore[arg-type]

    by_vmid = {n.vmid: n for n in resp.nodes}
    assert set(by_vmid) == {100, 101, None}
    assert (by_vmid[100].position_x, by_vmid[100].position_y) == (11.0, 22.0)
    assert (by_vmid[101].position_x, by_vmid[101].position_y) == (100.0, 220.0)
    assert by_vmid[100].ip_address == "10.0.0.100"
    assert by_vmid[101].ip_address == "cached"
    assert by_vmid[100].can_manage is True
    assert by_vmid[101].can_manage is False
    assert (by_vmid[None].position_x, by_vmid[None].position_y) == (33.0, 44.0)


# ─── 單台 VM 對外服務 ───────────────────────────────────────────────────────


def test_list_vm_published_services_merges_rules_and_orphans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def edges_for(vmids: list[int]) -> list[TopologyEdge]:
        return [
            TopologyEdge(source_vmid=150, target_vmid=None, ports=[]),
            TopologyEdge(source_vmid=None, target_vmid=999, ports=[]),
            TopologyEdge(
                source_vmid=None,
                target_vmid=150,
                ports=[PortSpec(port=80, protocol="tcp")],
            ),
            TopologyEdge(
                source_vmid=None,
                target_vmid=150,
                ports=[PortSpec(port=80, protocol="tcp")],
            ),
        ]

    monkeypatch.setattr(fw, "get_connections_from_rules", edges_for)
    monkeypatch.setattr(fw, "_enrich_edges_from_db", lambda edges, session: None)
    monkeypatch.setattr(
        rp_repo,
        "list_rules_by_vmid",
        lambda session, vmid: [
            SimpleNamespace(
                internal_port=80, domain="dup.example.com", enable_https=True
            ),
            SimpleNamespace(
                internal_port=443, domain="web.example.com", enable_https=True
            ),
        ],
    )
    monkeypatch.setattr(
        nat_repo,
        "list_rules_by_vmid",
        lambda session, vmid: [
            SimpleNamespace(internal_port=443, protocol="tcp", external_port=8443),
            SimpleNamespace(internal_port=22, protocol="tcp", external_port=2222),
        ],
    )

    services = fw.list_vm_published_services(150, object())  # type: ignore[arg-type]

    assert [(s.port, s.mode, s.firewall_rule_present) for s in services] == [
        (22, "port_forward", False),
        (80, "firewall_only", True),
        (443, "domain", False),
    ]
    assert services[2].url == "https://web.example.com"


def test_publish_vm_service_domain_checks_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []
    created: list[Any] = []
    monkeypatch.setattr(fw, "list_vm_published_services", lambda vmid, session: [])
    monkeypatch.setattr(
        reverse_proxy_service,
        "assert_domain_available",
        lambda session, domain: checked.append(domain),
    )
    monkeypatch.setattr(
        fw,
        "create_connection",
        lambda **kwargs: created.append(kwargs),
    )

    result = fw.publish_vm_service(
        150,
        PublishedServiceCreate(
            port=80, mode="domain", domain="Web.Example.com", enable_https=False
        ),
        object(),  # type: ignore[arg-type]
    )

    assert checked == ["web.example.com"]
    assert created[0]["target_vmid"] == 150
    assert result.mode == "domain"
    assert result.url == "http://web.example.com"


def test_unpublish_vm_service_deletes_inbound_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(fw, "delete_connection", lambda **kwargs: calls.append(kwargs))
    session = object()

    fw.unpublish_vm_service(150, PublishedServiceRef(port=53, protocol="udp"), session)  # type: ignore[arg-type]

    assert calls[0]["source_vmid"] is None
    assert calls[0]["target_vmid"] == 150
    assert [(p.port, p.protocol) for p in calls[0]["ports"]] == [(53, "udp")]
    assert calls[0]["session"] is session


@pytest.fixture
def replace_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        services=[
            PublishedService(
                port=80, protocol="tcp", mode="domain", domain="old.example.com"
            ),
            PublishedService(
                port=22, protocol="tcp", mode="port_forward", external_port=2222
            ),
        ],
        rp_rules=[],
        domain_checks=[],
        port_checks=[],
        unpublished=[],
        created=[],
        create_failures=0,
    )
    monkeypatch.setattr(
        fw, "list_vm_published_services", lambda vmid, session: state.services
    )
    monkeypatch.setattr(
        rp_repo, "list_rules_by_vmid", lambda session, vmid: state.rp_rules
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "assert_domain_available",
        lambda session, domain, exclude_rule_id=None: state.domain_checks.append(
            (domain, exclude_rule_id)
        ),
    )
    monkeypatch.setattr(
        nat_service,
        "check_port_available",
        lambda port, proto, session: state.port_checks.append((port, proto)),
    )
    monkeypatch.setattr(
        fw,
        "unpublish_vm_service",
        lambda vmid, ref, session: state.unpublished.append((ref.port, ref.protocol)),
    )

    def fake_create(**kwargs: Any) -> None:
        state.created.append(kwargs["ports"][0])
        if state.create_failures > 0:
            state.create_failures -= 1
            raise ProxmoxError("create boom")

    monkeypatch.setattr(fw, "create_connection", fake_create)
    return state


def _replace(current: PublishedServiceRef, replacement: PublishedServiceCreate) -> Any:
    return fw.replace_vm_service(150, current, replacement, object())  # type: ignore[arg-type]


def test_replace_vm_service_missing_and_conflicting(
    replace_env: SimpleNamespace,
) -> None:
    with pytest.raises(NotFoundError):
        _replace(PublishedServiceRef(port=443), PublishedServiceCreate(port=443))
    with pytest.raises(BadRequestError):
        _replace(PublishedServiceRef(port=80), PublishedServiceCreate(port=22))
    assert replace_env.unpublished == []


def test_replace_vm_service_domain_excludes_own_rule(
    replace_env: SimpleNamespace,
) -> None:
    rule_id = uuid.uuid4()
    replace_env.rp_rules = [
        SimpleNamespace(id=uuid.uuid4(), internal_port=8080, domain="old.example.com"),
        SimpleNamespace(id=rule_id, internal_port=80, domain="old.example.com"),
    ]

    result = _replace(
        PublishedServiceRef(port=80),
        PublishedServiceCreate(port=80, mode="domain", domain="old.example.com"),
    )

    assert replace_env.domain_checks == [("old.example.com", rule_id)]
    assert replace_env.unpublished == [(80, "tcp")]
    assert result.url == "https://old.example.com"


def test_replace_vm_service_restore_failure_is_logged(
    replace_env: SimpleNamespace,
) -> None:
    replace_env.create_failures = 2

    with pytest.raises(ProxmoxError):
        _replace(PublishedServiceRef(port=80), PublishedServiceCreate(port=80))

    assert len(replace_env.created) == 2


# ─── 迷你拓撲 ───────────────────────────────────────────────────────────────


def test_topology_node_for_vm_with_details_degrades_on_options_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fw, "_get_vm_ip", lambda vmid, session=None: "10.0.0.5")

    def boom(node: str, vmid: int, rtype: str) -> dict:
        raise RuntimeError("options boom")

    monkeypatch.setattr(fw, "get_firewall_options", boom)

    node = fw._topology_node_for_vm(
        100,
        {"node": "pve1", "type": "lxc", "name": "web", "status": "running"},
        None,
        x=1.0,
        y=2.0,
        with_details=True,
    )

    assert node.ip_address == "10.0.0.5"
    assert node.firewall_enabled is False
    assert node.vm_type == "lxc"


def test_get_vm_topology_places_peers_and_filters_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def find_resource(vmid: int) -> dict:
        if vmid == 300:
            raise NotFoundError("missing")
        return {
            "node": "pve1",
            "type": "qemu",
            "name": f"vm{vmid}",
            "status": "running",
        }

    monkeypatch.setattr(
        fw, "proxmox_service", SimpleNamespace(find_resource=find_resource)
    )
    monkeypatch.setattr(
        fw,
        "get_connections_from_rules",
        lambda vmids: [
            TopologyEdge(source_vmid=100, target_vmid=200, ports=[]),
            TopologyEdge(source_vmid=300, target_vmid=100, ports=[]),
            TopologyEdge(source_vmid=None, target_vmid=100, ports=[]),
            TopologyEdge(source_vmid=100, target_vmid=None, ports=[]),
        ],
    )
    monkeypatch.setattr(fw, "_enrich_edges_from_db", lambda edges, session: None)
    monkeypatch.setattr(fw, "_get_vm_ip", lambda vmid, session=None: "10.0.0.100")
    monkeypatch.setattr(
        fw, "get_firewall_options", lambda node, vmid, rtype: {"enable": 1}
    )

    resp = fw.get_vm_topology(100, object())  # type: ignore[arg-type]

    assert [n.vmid for n in resp.nodes] == [200, 100, None]
    peer, center, internet = resp.nodes
    assert (peer.position_x, peer.position_y, peer.ip_address) == (0.0, 40.0, None)
    assert center.position_x == 340.0
    assert center.position_y == 40.0 + 110.0 / 2
    assert center.firewall_enabled is True
    assert internet.position_x == 680.0
    assert [(e.source_vmid, e.target_vmid) for e in resp.edges] == [
        (100, 200),
        (None, 100),
        (100, None),
    ]
