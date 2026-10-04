"""services/network 的回歸測試：Gateway 自用 port、nginx 同步序列化、
服務編輯失敗還原、網域發布失敗回滾、UDP 撤下不誤刪網站、拓撲 port 去重、訊息 i18n。"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.infrastructure.ssh as ssh_module
from app.core.config import settings
from app.core.i18n import _catalog
from app.exceptions import BadRequestError, ProxmoxError
from app.repositories import gateway_config as gw_repo
from app.repositories import nat_rule as nat_repo
from app.repositories import reverse_proxy as rp_repo
from app.schemas.firewall import (
    PortSpec,
    PublishedServiceCreate,
    PublishedServiceRef,
    TopologyEdge,
)
from app.services.network import (
    cloudflare_service,
    gateway_service,
    ip_management_service,
    nat_service,
    reverse_proxy_service,
)
from app.services.network import firewall_service as fw
from app.services.network import nginx_gateway_service as nginx

# ─── Gateway 自己在用的 port 不可拿來當對外入口 ─────────────────────────


@pytest.fixture
def gateway_ports(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(ssh_port=2222)
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda _session: SimpleNamespace(ssh_port=state.ssh_port),
    )
    monkeypatch.setattr(nat_repo, "is_external_port_taken", lambda *_a: False)
    return state


def _session_with_get() -> Any:
    return SimpleNamespace(get=lambda *_a, **_k: None)


@pytest.mark.parametrize(
    ("port", "protocol"),
    [
        (settings.WIREGUARD_ENDPOINT_PORT, "udp"),
        (settings.GATEWAY_NODE_EXPORTER_PORT, "tcp"),
        (settings.GATEWAY_NGINX_EXPORTER_PORT, "tcp"),
        (nat_service.GATEWAY_NGINX_STATUS_PORT, "tcp"),
        (2222, "tcp"),  # Gateway 的 SSH port（來自 gateway_config）
    ],
)
def test_check_port_available_rejects_gateway_own_ports(
    gateway_ports: SimpleNamespace, port: int, protocol: str
) -> None:
    with pytest.raises(BadRequestError):
        nat_service.check_port_available(port, protocol, _session_with_get())


def test_check_port_available_allows_same_port_on_other_protocol(
    gateway_ports: SimpleNamespace,
) -> None:
    # WireGuard 只佔 UDP；TCP 的同號 port 不衝突
    nat_service.check_port_available(
        settings.WIREGUARD_ENDPOINT_PORT, "tcp", _session_with_get()
    )


def test_allocate_external_port_skips_gateway_ports(
    gateway_ports: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = settings.GATEWAY_NODE_EXPORTER_PORT
    monkeypatch.setattr(
        ip_management_service,
        "get_subnet_config",
        lambda _session: SimpleNamespace(
            forward_port_start=exporter, forward_port_end=exporter + 1
        ),
    )
    monkeypatch.setattr(nat_repo, "taken_external_ports", lambda *_a: set())

    assert nat_service.allocate_external_port(_session_with_get(), "tcp") == exporter + 1
    assert nat_service.allocate_external_port(_session_with_get(), "udp") == exporter


# ─── nginx 設定寫入的序列化 ───────────────────────────────────────────


class _FakeSftpFile:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path

    def __enter__(self) -> _FakeSftpFile:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, data: bytes) -> None:
        self._store[self._path] = data


class _FakeClient:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.closed = False

    def open_sftp(self) -> Any:
        store = self.files
        return SimpleNamespace(
            open=lambda path, _mode: _FakeSftpFile(store, path),
            close=lambda: None,
        )

    def close(self) -> None:
        self.closed = True


def test_write_validated_config_uses_unique_temp_and_gateway_flock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()

    def fake_exec(_client: Any, command: str) -> tuple[int, str, str]:
        client.commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(nginx, "_exec", fake_exec)

    nginx.write_validated_config(client, nginx.NGINX_STREAM_CONF_PATH, "a\n")
    nginx.write_validated_config(client, nginx.NGINX_STREAM_CONF_PATH, "b\n")

    tmp_paths = list(client.files)
    assert len(tmp_paths) == 2  # 兩次寫入不共用同一個暫存檔
    for command, tmp_path in zip(client.commands, tmp_paths, strict=True):
        assert command.startswith("flock -w ")
        assert "/run/lock/skylab-nginx.lock sh -c " in command
        assert f"mv -f {tmp_path} {nginx.NGINX_STREAM_CONF_PATH}" in command


def test_lock_config_writes_takes_advisory_lock_on_postgres() -> None:
    executed: list[Any] = []
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=lambda stmt, params: executed.append((str(stmt), params)),
    )

    nginx.lock_config_writes(session)

    assert len(executed) == 1
    assert "pg_advisory_xact_lock" in executed[0][0]


def test_lock_config_writes_is_noop_without_postgres() -> None:
    executed: list[Any] = []
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        execute=lambda *a: executed.append(a),
    )

    nginx.lock_config_writes(session)
    nginx.lock_config_writes(object())

    assert executed == []


def _patch_gateway_ssh(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda _session: SimpleNamespace(
            host="10.0.0.2",
            ssh_port=22,
            ssh_user="root",
            encrypted_private_key="x",
        ),
    )
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda _cfg: "pem")
    monkeypatch.setattr(
        ssh_module, "create_key_client", lambda *_a, **_k: _FakeClient()
    )
    monkeypatch.setattr(
        nginx, "lock_config_writes", lambda _session: events.append("lock")
    )
    monkeypatch.setattr(
        nginx,
        "write_validated_config",
        lambda _client, path, _content, **_k: events.append(f"write:{path}"),
    )


def test_sync_nginx_stream_reads_rules_after_taking_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_gateway_ssh(monkeypatch, events)
    monkeypatch.setattr(
        nat_repo, "list_rules", lambda _session: events.append("list") or []
    )

    nat_service._sync_nginx_stream(object())

    assert events == ["lock", "list", f"write:{nginx.NGINX_STREAM_CONF_PATH}"]


def test_sync_then_delete_builds_remaining_list_under_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_gateway_ssh(monkeypatch, events)
    monkeypatch.setattr(
        nat_repo, "list_rules", lambda _session: events.append("list") or []
    )
    monkeypatch.setattr(
        nat_repo, "delete_rules", lambda _session, _rules: events.append("delete")
    )

    nat_service._sync_then_delete(object(), [SimpleNamespace(id=1)])

    assert events.index("lock") < events.index("list")
    assert events[-1] == "delete"


def test_sync_nginx_http_rereads_rules_under_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    _patch_gateway_ssh(monkeypatch, events)
    first = [SimpleNamespace(domain="a.example.com", enable_https=False)]
    second = [*first, SimpleNamespace(domain="b.example.com", enable_https=False)]
    reads = iter([first, second])
    monkeypatch.setattr(
        rp_repo, "list_rules", lambda _session: events.append("list") or next(reads)
    )
    built: list[Any] = []
    monkeypatch.setattr(
        nginx,
        "build_http_config",
        lambda rules, cert_names, **_kwargs: built.append(list(rules)) or "",
    )

    reverse_proxy_service._sync_nginx(object())

    assert events == ["list", "lock", "list", f"write:{nginx.NGINX_HTTP_CONF_PATH}"]
    assert built == [second]


# ─── 換服務設定失敗時不可弄丟原本的服務 ─────────────────────────────


@pytest.fixture
def replace_env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(unpublished=[], created=[], fail_create=0)
    current = fw._service_from_spec(
        PortSpec(port=22, protocol="tcp", external_port=30001),
        firewall_rule_present=True,
    )
    monkeypatch.setattr(
        fw, "list_vm_published_services", lambda _vmid, _session: [current]
    )
    monkeypatch.setattr(
        fw,
        "unpublish_vm_service",
        lambda vmid, ref, session: state.unpublished.append((vmid, ref)),
    )

    def fake_create(*, source_vmid, target_vmid, ports, session=None):
        state.created.append(list(ports))
        if state.fail_create > 0:
            state.fail_create -= 1
            raise BadRequestError("create failed")

    monkeypatch.setattr(fw, "create_connection", fake_create)
    return state


def test_replace_rejects_taken_external_port_before_unpublishing(
    replace_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def taken(port: int, protocol: str, _session: Any) -> None:
        raise BadRequestError(f"{port}/{protocol} taken")

    monkeypatch.setattr(nat_service, "check_port_available", taken)

    with pytest.raises(BadRequestError, match="30002/tcp taken"):
        fw.replace_vm_service(
            150,
            PublishedServiceRef(port=22, protocol="tcp"),
            PublishedServiceCreate(
                port=22, protocol="tcp", mode="port_forward", external_port=30002
            ),
            session=object(),  # type: ignore[arg-type]
        )

    assert replace_env.unpublished == []
    assert replace_env.created == []


def test_replace_keeping_own_external_port_skips_port_check(
    replace_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def taken(*_a: Any) -> None:
        raise AssertionError("own port must not be re-checked")

    monkeypatch.setattr(nat_service, "check_port_available", taken)

    fw.replace_vm_service(
        150,
        PublishedServiceRef(port=22, protocol="tcp"),
        PublishedServiceCreate(
            port=2222, protocol="tcp", mode="port_forward", external_port=30001
        ),
        session=object(),  # type: ignore[arg-type]
    )

    assert replace_env.created == [
        [PortSpec(port=2222, protocol="tcp", external_port=30001)]
    ]


def test_replace_restores_original_service_when_publish_fails(
    replace_env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nat_service, "check_port_available", lambda *_a: None)
    replace_env.fail_create = 1

    with pytest.raises(BadRequestError, match="create failed"):
        fw.replace_vm_service(
            150,
            PublishedServiceRef(port=22, protocol="tcp"),
            PublishedServiceCreate(
                port=22, protocol="tcp", mode="port_forward", external_port=30002
            ),
            session=object(),  # type: ignore[arg-type]
        )

    assert len(replace_env.unpublished) == 1
    assert replace_env.created == [
        [PortSpec(port=22, protocol="tcp", external_port=30002)],
        [PortSpec(port=22, protocol="tcp", external_port=30001)],
    ]


# ─── 網域發布同步失敗要收回 DB 規則與 DNS 紀錄 ─────────────────────


def test_apply_reverse_proxy_rule_rolls_back_when_sync_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SimpleNamespace(get=lambda *_a, **_k: SimpleNamespace(vmid=101))
    deleted_rules: list[Any] = []
    deleted_records: list[tuple[str, str]] = []

    monkeypatch.setattr(reverse_proxy_service, "ensure_reverse_proxy_ready", lambda _s: None)
    monkeypatch.setattr(reverse_proxy_service, "assert_publishable_vm_ip", lambda *_a, **_k: None)
    monkeypatch.setattr(reverse_proxy_service, "assert_domain_available", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cloudflare_service,
        "get_zone",
        lambda *, session, zone_id: SimpleNamespace(id=zone_id, name="example.com"),
    )
    monkeypatch.setattr(
        cloudflare_service,
        "upsert_reverse_proxy_dns_record",
        lambda **_k: SimpleNamespace(id="dns_1"),
    )
    monkeypatch.setattr(
        cloudflare_service,
        "delete_reverse_proxy_dns_record",
        lambda *, session, zone_id, record_id: deleted_records.append((zone_id, record_id)),
    )
    monkeypatch.setattr(rp_repo, "create_rule", lambda _s, rule: rule)
    monkeypatch.setattr(rp_repo, "update_rule", lambda _s, rule: rule)
    monkeypatch.setattr(rp_repo, "delete_rule", lambda _s, rule: deleted_rules.append(rule))

    def failing_sync(_session: Any) -> None:
        raise ProxmoxError("Gateway unreachable")

    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", failing_sync)

    with pytest.raises(ProxmoxError, match="Gateway unreachable"):
        reverse_proxy_service.apply_reverse_proxy_rule(
            session=session,
            vmid=101,
            vm_ip="10.0.0.15",
            zone_id="zone1",
            hostname_prefix="app",
            internal_port=8080,
        )

    assert [r.domain for r in deleted_rules] == ["app.example.com"]
    assert deleted_records == [("zone1", "dns_1")]


# ─── 撤下 UDP 服務不可連帶刪掉同 port 的 TCP 網站 ───────────────────


def _fake_proxmox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fw,
        "proxmox_service",
        SimpleNamespace(find_resource=lambda vmid: {"node": "pve1", "type": "qemu"}),
    )


@pytest.mark.parametrize(("protocol", "expect_rp_removed"), [("udp", False), ("tcp", True)])
def test_delete_connection_only_removes_domain_for_tcp(
    monkeypatch: pytest.MonkeyPatch, protocol: str, expect_rp_removed: bool
) -> None:
    _fake_proxmox(monkeypatch)
    monkeypatch.setattr(fw, "_delete_matching_rules", lambda **_k: None)
    nat_removed: list[Any] = []
    rp_removed: list[Any] = []
    monkeypatch.setattr(
        nat_service,
        "remove_nat_rules_by_internal_port",
        lambda *a: nat_removed.append(a[1:]),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "remove_reverse_proxy_rules_by_internal_port",
        lambda *a: rp_removed.append(a[1:]),
    )

    fw.delete_connection(None, 150, [PortSpec(port=8080, protocol=protocol)], object())

    assert nat_removed == [(150, 8080, protocol)]
    assert bool(rp_removed) is expect_rp_removed


def test_enrich_edges_does_not_label_udp_as_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rp_repo,
        "list_rules_by_vmids",
        lambda _s, _vmids: [
            SimpleNamespace(
                vmid=150, internal_port=8080, domain="web.example.com", enable_https=True
            )
        ],
    )
    monkeypatch.setattr(
        nat_repo,
        "list_rules_by_vmids",
        lambda _s, _vmids: [
            SimpleNamespace(vmid=150, internal_port=8080, protocol="udp", external_port=30080)
        ],
    )
    edge = TopologyEdge(
        source_vmid=None,
        target_vmid=150,
        ports=[PortSpec(port=8080, protocol="tcp"), PortSpec(port=8080, protocol="udp")],
        direction="one_way",
    )

    fw._enrich_edges_from_db([edge], session=object())  # type: ignore[arg-type]

    tcp, udp = edge.ports
    assert tcp.domain == "web.example.com"
    assert udp.domain is None
    assert udp.external_port == 30080


# ─── 兩台 VM 都看得到時，同一條連線的 port 只列一次 ────────────────


def test_connections_from_rules_dedupes_ports_seen_on_both_vms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_proxmox(monkeypatch)
    comment = fw._make_connection_comment(100, 101, 22, "tcp")
    monkeypatch.setattr(
        fw,
        "get_vm_firewall_rules",
        lambda _node, _vmid, _type: [{"pos": 0, "comment": comment}],
    )

    edges = fw.get_connections_from_rules([100, 101])

    assert len(edges) == 1
    assert edges[0].ports == [PortSpec(port=22, protocol="tcp")]


# ─── Gateway 連線測試訊息走 i18n ─────────────────────────────────────


def test_gateway_test_connection_messages_are_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gateway_service, "t", lambda key, **_k: f"<{key}>")

    def auth_fail(*_a: Any) -> Any:
        raise gateway_service.SSHAuthenticationError("denied")

    monkeypatch.setattr(gateway_service, "make_client", auth_fail)
    assert gateway_service.test_connection("h", 22, "root", "pem") == (
        False,
        "<gateway.sshAuthFailed>",
    )

    def boom(*_a: Any) -> Any:
        raise OSError("timed out")

    monkeypatch.setattr(gateway_service, "make_client", boom)
    assert gateway_service.test_connection("h", 22, "root", "pem") == (
        False,
        "<gateway.connectionFailed>",
    )


# ─── 追補：network 服務用到的每個 i18n key 三語都要有翻譯 ─────────────
# 上面那個測試把 t() 換掉了，抓不到 locale 缺 key。translate() 在某語言缺 key
# 時會退回 zh-TW，zh-TW 也沒有才回傳 key 本身；經過它只能驗到 zh-TW 有 key，
# en／ja 漏譯時使用者會看到中文。所以直接查各語言的 catalog。

_NETWORK_SERVICE_DIR = Path(__file__).resolve().parents[2] / "app" / "services" / "network"
_T_CALL_KEY = re.compile(r"\bt\(\s*[\"']([A-Za-z0-9_.]+)[\"']")

# 改成 t() 的訊息與呼叫端實際帶的參數
_NETWORK_MESSAGE_PARAMS: dict[str, dict[str, object]] = {
    "gateway.connectionOk": {},
    "gateway.unexpectedEchoResponse": {"output": "xyz"},
    "gateway.sshAuthFailed": {},
    "gateway.connectionFailed": {"error": "timed out"},
    "gateway.serviceRestartDone": {"service": "nginx"},
    "gateway.serviceRestartState": {"service": "nginx", "state": "failed"},
    "gateway.serviceActionDone": {"service": "nginx", "action": "reload"},
    "gateway.versionUnavailable": {"service": "nginx"},
    "cloudflare.tokenVerified": {"status": "active"},
}


def _network_service_translation_keys() -> set[str]:
    keys: set[str] = set()
    for path in _NETWORK_SERVICE_DIR.glob("*.py"):
        keys.update(_T_CALL_KEY.findall(path.read_text(encoding="utf-8")))
    return keys


@pytest.mark.parametrize("lang", ["zh-TW", "en", "ja"])
def test_network_service_translation_keys_exist_in_every_language(lang: str) -> None:
    keys = _network_service_translation_keys()
    assert set(_NETWORK_MESSAGE_PARAMS) <= keys
    catalog = _catalog(lang)
    missing = sorted(k for k in keys if not catalog.get(k))
    assert missing == [], f"missing in {lang}: {missing}"


@pytest.mark.parametrize("lang", ["zh-TW", "en", "ja"])
@pytest.mark.parametrize("key", sorted(_NETWORK_MESSAGE_PARAMS))
def test_messages_fill_their_placeholders(key: str, lang: str) -> None:
    params = _NETWORK_MESSAGE_PARAMS[key]
    template = _catalog(lang).get(key)
    assert template, f"{key} missing in {lang}"
    # 直接 format：佔位符名稱對不上時 KeyError 會浮出來，不像 translate() 會吞掉
    message = template.format(**params)
    assert "{" not in message
    for value in params.values():
        assert str(value) in message
