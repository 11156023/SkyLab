"""B2 整理（去重／搬移）後的路由行為回歸測試。

直接呼叫路由函式並 monkeypatch service／PVE，確認整理前後的回應與錯誤碼不變。
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.api.routes import firewall as firewall_route
from app.api.routes import gateway as gateway_route
from app.api.routes import jobs as jobs_route
from app.api.routes import proxmox_config as proxmox_config_route
from app.exceptions import BadRequestError, NotFoundError
from app.schemas.firewall import FirewallRuleUpdate
from app.schemas.jobs import JobKind, JobStatus
from app.schemas.proxmox_config import ProxmoxConfigPublic, ProxmoxConfigUpdate
from app.services.user import audit_service


@pytest.fixture(scope="session")
def _seed_first_superuser() -> None:
    """純單元測試，不需要測試資料庫。"""


@pytest.fixture(autouse=True)
def _silence_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit_service, "log_action", lambda **_kw: None)


def _self_signed_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "b2-test-ca")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.PEM).decode()


# ─── 防火牆規則：受管規則判斷與位置查找 ───────────────────────────────────────


_RESOURCE_INFO = {"node": "pve1", "type": "qemu"}


@pytest.fixture()
def firewall_rules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "rules": [
            {"pos": 0, "type": "in", "action": "ACCEPT", "comment": "SkyLab:1->2:22/tcp"},
            {"pos": 1, "type": "in", "action": "ACCEPT", "comment": "my rule"},
            {"pos": 2, "type": "in", "action": "DROP"},
        ],
        "updated": [],
        "deleted": [],
    }
    monkeypatch.setattr(
        firewall_route, "require_resource_management", lambda **_kw: None
    )
    monkeypatch.setattr(
        firewall_route.firewall_service,
        "get_vm_firewall_rules",
        lambda *_a: list(state["rules"]),
    )
    monkeypatch.setattr(
        firewall_route.firewall_service,
        "update_rule",
        lambda _n, _v, _t, pos, rule: state["updated"].append((pos, rule)),
    )
    monkeypatch.setattr(
        firewall_route.firewall_service,
        "delete_rule_by_pos",
        lambda _n, _v, _t, pos: state["deleted"].append(pos),
    )
    return state


def _user() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


def test_list_rules_flags_only_skylab_comments(firewall_rules: dict[str, Any]) -> None:
    rules = firewall_route.list_rules(100, _RESOURCE_INFO)
    assert [r.is_managed for r in rules] == [True, False, False]


def test_list_rules_returns_empty_when_service_returns_nothing(
    firewall_rules: dict[str, Any],
) -> None:
    firewall_rules["rules"] = []
    assert firewall_route.list_rules(100, _RESOURCE_INFO) == []


def test_update_and_delete_reject_managed_rule(firewall_rules: dict[str, Any]) -> None:
    pos = 0
    with pytest.raises(HTTPException) as update_exc:
        firewall_route.update_rule(
            100, pos, FirewallRuleUpdate(action="DROP"), None, _user(), _RESOURCE_INFO
        )
    with pytest.raises(HTTPException) as delete_exc:
        firewall_route.delete_rule(100, pos, None, _user(), _RESOURCE_INFO)
    assert update_exc.value.status_code == 400
    assert delete_exc.value.status_code == 400
    assert update_exc.value.detail != delete_exc.value.detail
    assert firewall_rules["updated"] == []
    assert firewall_rules["deleted"] == []


def test_update_and_delete_missing_position_is_not_found(
    firewall_rules: dict[str, Any],
) -> None:
    with pytest.raises(NotFoundError):
        firewall_route.update_rule(
            100, 9, FirewallRuleUpdate(action="DROP"), None, _user(), _RESOURCE_INFO
        )
    with pytest.raises(NotFoundError):
        firewall_route.delete_rule(100, 9, None, _user(), _RESOURCE_INFO)


def test_update_and_delete_unmanaged_rule(firewall_rules: dict[str, Any]) -> None:
    firewall_route.update_rule(
        100, 1, FirewallRuleUpdate(action="DROP"), None, _user(), _RESOURCE_INFO
    )
    firewall_route.delete_rule(100, 2, None, _user(), _RESOURCE_INFO)
    assert firewall_rules["updated"] == [(1, {"action": "DROP"})]
    assert firewall_rules["deleted"] == [2]


def test_get_options_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        firewall_route.firewall_service, "get_firewall_options", lambda *_a: {}
    )
    opts = firewall_route.get_options(100, _RESOURCE_INFO)
    assert (opts.enable, opts.policy_in, opts.policy_out) == (False, "DROP", "ACCEPT")


def test_endpoint_access_checks_each_given_vmid_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[int] = []
    monkeypatch.setattr(
        firewall_route,
        "check_firewall_access",
        lambda *, vmid, current_user, session: checked.append(vmid),
    )
    firewall_route._check_endpoint_access(None, _user(), None, 7)
    firewall_route._check_endpoint_access(None, _user(), 5, None)
    firewall_route._check_endpoint_access(None, _user(), 5, 7)
    firewall_route._check_endpoint_access(None, _user(), None, None)
    assert checked == [7, 5, 5, 7]


# ─── Jobs：CSV → 枚舉 ────────────────────────────────────────────────────────


def test_parse_enum_csv_keeps_none_vs_empty_semantics() -> None:
    assert jobs_route._parse_enum_csv(None, JobKind) is None
    assert jobs_route._parse_enum_csv(" , ", JobKind) is None
    assert jobs_route._parse_enum_csv("bogus", JobKind) == []
    assert jobs_route._parse_enum_csv("template, bogus,batch_provision", JobKind) == [
        JobKind.template,
        JobKind.batch_provision,
    ]
    assert jobs_route._parse_enum_csv("failed", JobStatus) == [JobStatus.failed]


def test_kinds_description_lists_every_job_kind() -> None:
    description = jobs_route._enum_values(JobKind)
    for kind in JobKind:
        assert kind.value in description


# ─── Gateway：公開設定組裝 ────────────────────────────────────────────────────


def test_gateway_to_public_requires_host_and_key() -> None:
    base = {"host": "10.0.0.1", "ssh_port": 22, "ssh_user": "root", "public_key": "ssh-ed25519 AAA"}
    configured = gateway_route._to_public(
        SimpleNamespace(**base, encrypted_private_key="enc")
    )
    missing_key = gateway_route._to_public(
        SimpleNamespace(**base, encrypted_private_key=None)
    )
    assert configured.is_configured is True
    assert missing_key.is_configured is False
    assert configured.host == "10.0.0.1"


# ─── PVE 設定：CA 憑證、指紋、預設值、節點探測、閾值 ─────────────────────────


def test_validate_ca_cert_pem() -> None:
    proxmox_config_route._validate_ca_cert_pem(None)
    proxmox_config_route._validate_ca_cert_pem("")
    proxmox_config_route._validate_ca_cert_pem(_self_signed_pem())
    with pytest.raises(BadRequestError):
        proxmox_config_route._validate_ca_cert_pem("not a certificate")


def test_parse_cert_fingerprint_matches_cert_fingerprint() -> None:
    pem = _self_signed_pem()
    result = proxmox_config_route.parse_cert(_user(), pem)
    assert result.valid is True
    assert result.fingerprint == proxmox_config_route._cert_fingerprint(pem)
    assert result.subject == "CN=b2-test-ca"


def test_parse_cert_invalid() -> None:
    result = proxmox_config_route.parse_cert(_user(), "garbage")
    assert result.valid is False


def test_get_config_fallback_uses_schema_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        proxmox_config_route.proxmox_config_repo,
        "get_proxmox_config",
        lambda _session: None,
    )
    result = proxmox_config_route.get_proxmox_config(None, _user())
    assert isinstance(result, ProxmoxConfigPublic)
    assert result.is_configured is False
    assert result.has_ca_cert is False
    assert result.ca_fingerprint is None
    assert result.updated_at is None
    assert (result.iso_storage, result.data_storage) == ("local", "local-lvm")
    assert (result.api_timeout, result.task_check_interval) == (30, 2)
    # 放置參數與 scheduled_boot_* 一樣來自 schema 預設
    assert result.cpu_overcommit_ratio == 2.0
    assert result.placement_loadavg_max_per_core == 1.5
    assert result.placement_memory_peak_high_share == 0.85
    assert result.gateway_ip is None


class _FakeProxmoxAPI:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self, host: str, **kwargs: Any) -> None:
        type(self).calls.append((host, kwargs))
        self.nodes = SimpleNamespace(get=lambda: [{"node": "pve1"}, {"node": "pve2"}])


def test_probe_node_names_only_passes_port_when_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import proxmoxer

    _FakeProxmoxAPI.calls = []
    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", _FakeProxmoxAPI)
    common = {"user": "root@pam", "password": "pw", "verify_ssl": False, "timeout": 5}
    assert proxmox_config_route._probe_node_names("h1", **common) == ["pve1", "pve2"]
    proxmox_config_route._probe_node_names("h2", port=8007, **common)
    assert "port" not in _FakeProxmoxAPI.calls[0][1]
    assert _FakeProxmoxAPI.calls[1][1]["port"] == 8007


def test_connection_test_endpoints_use_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import proxmoxer

    _FakeProxmoxAPI.calls = []
    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", _FakeProxmoxAPI)
    monkeypatch.setattr(
        proxmox_config_route, "resolve_verify", lambda _h, verify, _ca: verify
    )
    conn = SimpleNamespace(
        host="pve.example", port=8006, user="root@pam", verify_ssl=False,
        ca_cert=None, api_timeout=10,
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_connection_repo, "get_connection", lambda *_a: conn
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_connection_repo,
        "get_decrypted_password",
        lambda _c: "pw",
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_config_repo, "get_proxmox_config", lambda _s: conn
    )
    monkeypatch.setattr(
        proxmox_config_route.proxmox_config_repo,
        "get_decrypted_password",
        lambda _c: "pw",
    )
    by_id = proxmox_config_route.test_connection_by_id(1, None, _user())
    legacy = proxmox_config_route.test_proxmox_connection(None, _user())
    assert by_id.success is True and legacy.success is True
    assert "pve1, pve2" in by_id.message
    assert _FakeProxmoxAPI.calls[0][1]["port"] == 8006
    assert "port" not in _FakeProxmoxAPI.calls[1][1]


def test_preview_rejects_inverted_thresholds_with_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[Any] = []
    monkeypatch.setattr(
        proxmox_config_route, "fetch_cluster_nodes", lambda **kw: fetched.append(kw)
    )
    config_in = ProxmoxConfigUpdate(
        host="pve.example",
        user="root@pam",
        password="pw",
        placement_loadavg_warn_per_core=1.0,
        placement_loadavg_max_per_core=0.5,
    )
    with pytest.raises(BadRequestError):
        proxmox_config_route.preview_cluster(None, _user(), config_in)
    assert fetched == []


def test_preview_maps_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        proxmox_config_route.proxmox_config_repo, "get_proxmox_config", lambda _s: None
    )
    monkeypatch.setattr(
        proxmox_config_route,
        "fetch_cluster_nodes",
        lambda **_kw: [
            {"name": "pve1", "host": "10.0.0.1", "is_primary": True},
            {"name": "pve2", "host": "10.0.0.2", "port": 8007},
        ],
    )
    result = proxmox_config_route.preview_cluster(
        None,
        _user(),
        ProxmoxConfigUpdate(host="pve.example", user="root@pam", password="pw"),
    )
    assert result.success is True and result.is_cluster is True
    assert [(n.name, n.port, n.is_primary) for n in result.nodes] == [
        ("pve1", 8006, True),
        ("pve2", 8007, False),
    ]
