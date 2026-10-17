"""Coverage tests for app.services.network.reverse_proxy_service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure import ssh as ssh_module
from app.repositories import gateway_config as gw_repo
from app.repositories import reverse_proxy as rp_repo
from app.services.network import (
    cloudflare_service,
    gateway_certificate_service,
    platform_entry_service,
    reverse_proxy_service,
)
from app.services.network import nginx_gateway_service as nginx

_ZONE_ID = "0123456789abcdef0123456789abcdef"


def _zone(zone_id: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=zone_id, name=name)


def _rule(**overrides) -> SimpleNamespace:
    values = {
        "id": uuid.uuid4(),
        "vmid": 101,
        "domain": "app.example.com",
        "zone_id": _ZONE_ID,
        "cloudflare_record_id": "rec-1",
        "enable_https": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


# ── 名稱與網域 ───────────────────────────────────────────────────────────


def test_build_runtime_name_delegates_to_nginx() -> None:
    assert reverse_proxy_service.build_runtime_name(101, "a.example.com") == (
        nginx.http_server_name(101, "a.example.com")
    )


def test_build_full_domain_rejects_bad_zone_and_long_domain() -> None:
    with pytest.raises(BadRequestError):
        reverse_proxy_service.build_full_domain(
            zone_name="bad zone", hostname_prefix="a"
        )

    long_prefix = ".".join(["a" * 60] * 5)
    with pytest.raises(BadRequestError):
        reverse_proxy_service.build_full_domain(
            zone_name="example.com", hostname_prefix=long_prefix
        )


def test_active_zones_queries_cloudflare(monkeypatch) -> None:
    seen = {}

    def list_zones(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(items=["zone"])

    monkeypatch.setattr(cloudflare_service, "list_zones", list_zones)
    assert reverse_proxy_service._active_zones("s") == ["zone"]
    assert seen == {"session": "s", "page": 1, "per_page": 100, "status": "active"}


# ── 就緒狀態 ─────────────────────────────────────────────────────────────


def test_gateway_ready_state(monkeypatch) -> None:
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: None)
    ready, reason = reverse_proxy_service._get_gateway_ready_state(object())
    assert ready is False
    assert reason

    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda _s: SimpleNamespace(host="gw", encrypted_private_key="enc"),
    )
    assert reverse_proxy_service._get_gateway_ready_state(object()) == (True, None)


def _cf_config(**overrides) -> SimpleNamespace:
    values = {
        "is_configured": True,
        "has_default_dns_target": True,
        "default_dns_target_type": "A",
        "default_dns_target_value": "203.0.113.10",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_cloudflare_ready_state_variants(monkeypatch) -> None:
    monkeypatch.setattr(
        cloudflare_service,
        "get_public_config",
        lambda _s: _cf_config(is_configured=False),
    )
    assert reverse_proxy_service._get_cloudflare_ready_state(object())[0] is False

    monkeypatch.setattr(
        cloudflare_service,
        "get_public_config",
        lambda _s: _cf_config(has_default_dns_target=False),
    )
    assert reverse_proxy_service._get_cloudflare_ready_state(object())[0] is False

    monkeypatch.setattr(
        cloudflare_service, "get_public_config", lambda _s: _cf_config()
    )

    def boom(_s):
        raise RuntimeError("cloudflare down")

    monkeypatch.setattr(reverse_proxy_service, "_active_zones", boom)
    assert reverse_proxy_service._get_cloudflare_ready_state(object()) == (
        False,
        "cloudflare down",
        [],
        "A",
        "203.0.113.10",
    )

    monkeypatch.setattr(reverse_proxy_service, "_active_zones", lambda _s: [])
    ready, reason, zones, *_ = reverse_proxy_service._get_cloudflare_ready_state(
        object()
    )
    assert ready is False
    assert reason
    assert zones == []

    monkeypatch.setattr(
        reverse_proxy_service,
        "_active_zones",
        lambda _s: [_zone(_ZONE_ID, "example.com")],
    )
    ready, reason, zones, target_type, target_value = (
        reverse_proxy_service._get_cloudflare_ready_state(object())
    )
    assert ready is True
    assert reason is None
    assert [zone.name for zone in zones] == ["example.com"]
    assert (target_type, target_value) == ("A", "203.0.113.10")


def test_ensure_reverse_proxy_ready_raises_reasons(monkeypatch) -> None:
    monkeypatch.setattr(
        reverse_proxy_service,
        "_get_gateway_ready_state",
        lambda _s: (False, "gateway missing"),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "_get_cloudflare_ready_state",
        lambda _s: (False, "cloudflare missing", [], None, None),
    )
    with pytest.raises(BadRequestError, match="gateway missing；cloudflare missing"):
        reverse_proxy_service.ensure_reverse_proxy_ready(object())


# ── nginx 同步 ───────────────────────────────────────────────────────────


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sync_env(monkeypatch):
    client = _Client()
    env = SimpleNamespace(
        client=client,
        writes=[],
        rules=[],
        platform=None,
        certificate=None,
        write_error=None,
    )
    monkeypatch.setattr(
        gw_repo,
        "get_gateway_config",
        lambda _s: SimpleNamespace(
            host="gw", ssh_port=22, ssh_user="root", encrypted_private_key="enc"
        ),
    )
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda _c: "PEM")
    monkeypatch.setattr(ssh_module, "create_key_client", lambda *a, **k: client)
    monkeypatch.setattr(nginx, "lock_config_writes", lambda _s: None)
    monkeypatch.setattr(rp_repo, "list_rules", lambda _s: env.rules)
    monkeypatch.setattr(platform_entry_service, "load_entry", lambda _s: env.platform)
    monkeypatch.setattr(
        gateway_certificate_service, "load_paths", lambda _s: env.certificate
    )
    monkeypatch.setattr(
        nginx, "build_http_config", lambda rules, cert, *, platform: "conf"
    )

    def write(_client, path, content):
        if env.write_error is not None:
            raise env.write_error
        env.writes.append((path, content))

    monkeypatch.setattr(nginx, "write_validated_config", write)
    return env


def test_sync_nginx_requires_gateway(monkeypatch) -> None:
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: None)
    with pytest.raises(ProxmoxError):
        reverse_proxy_service._sync_nginx(object())


def test_sync_nginx_writes_config_and_warns_without_certificate(sync_env) -> None:
    sync_env.rules = [_rule(enable_https=False)]
    sync_env.platform = SimpleNamespace(enable_https=True)

    reverse_proxy_service.sync_to_gateway(object())

    assert sync_env.writes == [(nginx.NGINX_HTTP_CONF_PATH, "conf")]
    assert sync_env.client.closed is True


def test_sync_nginx_reraises_known_errors(sync_env) -> None:
    sync_env.write_error = ProxmoxError("nginx -t failed")
    with pytest.raises(ProxmoxError, match="nginx -t failed"):
        reverse_proxy_service._sync_nginx(object())
    assert sync_env.client.closed is True


def test_sync_nginx_wraps_unexpected_errors(sync_env) -> None:
    sync_env.write_error = OSError("broken pipe")
    with pytest.raises(ProxmoxError):
        reverse_proxy_service._sync_nginx(object())


# ── apply_reverse_proxy_rule ─────────────────────────────────────────────


class _Session:
    def __init__(self, resource=True) -> None:
        self.rolled_back = False
        self._resource = resource

    def get(self, _model, _vmid):
        return SimpleNamespace(vmid=_vmid) if self._resource else None

    def rollback(self) -> None:
        self.rolled_back = True


@pytest.fixture
def apply_env(monkeypatch):
    env = SimpleNamespace(deleted=[], updated=[], cleaned=[])
    monkeypatch.setattr(
        reverse_proxy_service, "ensure_reverse_proxy_ready", lambda _s: None
    )
    monkeypatch.setattr(
        reverse_proxy_service, "assert_publishable_vm_ip", lambda *a, **k: None
    )
    monkeypatch.setattr(
        cloudflare_service,
        "get_zone",
        lambda *, session, zone_id: _zone(zone_id, "example.com"),
    )
    monkeypatch.setattr(
        reverse_proxy_service, "assert_domain_available", lambda *a, **k: None
    )
    monkeypatch.setattr(rp_repo, "create_rule", lambda _s, rule: rule)
    monkeypatch.setattr(
        rp_repo, "update_rule", lambda _s, rule: env.updated.append(rule)
    )
    monkeypatch.setattr(
        cloudflare_service,
        "upsert_reverse_proxy_dns_record",
        lambda **_kw: SimpleNamespace(id="rec-new"),
    )
    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", lambda _s: None)
    monkeypatch.setattr(
        reverse_proxy_service,
        "_cleanup_managed_dns_record",
        lambda _s, rule: env.cleaned.append(rule),
    )
    return env


def _apply(session, prefix: str = "app"):
    return reverse_proxy_service.apply_reverse_proxy_rule(
        session,
        vmid=101,
        vm_ip="10.10.1.10",
        zone_id=_ZONE_ID,
        hostname_prefix=prefix,
        internal_port=8080,
    )


def test_apply_rule_rejects_unknown_vmid(apply_env) -> None:
    with pytest.raises(BadRequestError):
        _apply(_Session(resource=False))


def test_apply_rule_integrity_error_rolls_back(monkeypatch, apply_env) -> None:
    def create(_s, _rule):
        raise IntegrityError("insert", {}, Exception("duplicate domain"))

    monkeypatch.setattr(rp_repo, "create_rule", create)
    session = _Session()
    with pytest.raises(BadRequestError):
        _apply(session)
    assert session.rolled_back is True


def test_apply_rule_dns_failure_and_delete_failure(monkeypatch, apply_env) -> None:
    def upsert(**_kw):
        raise RuntimeError("dns failed")

    def delete(_s, _rule):
        raise RuntimeError("db gone")

    monkeypatch.setattr(cloudflare_service, "upsert_reverse_proxy_dns_record", upsert)
    monkeypatch.setattr(rp_repo, "delete_rule", delete)
    with pytest.raises(RuntimeError, match="dns failed"):
        _apply(_Session())


def test_apply_rule_sync_failure_and_delete_failure(monkeypatch, apply_env) -> None:
    def sync(_s):
        raise ProxmoxError("gateway down")

    def delete(_s, _rule):
        raise RuntimeError("db gone")

    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", sync)
    monkeypatch.setattr(rp_repo, "delete_rule", delete)

    with pytest.raises(ProxmoxError, match="gateway down"):
        _apply(_Session())


def test_apply_rule_for_domain_resolves_zone(monkeypatch, apply_env) -> None:
    monkeypatch.setattr(
        reverse_proxy_service,
        "_active_zones",
        lambda _s: [_zone(_ZONE_ID, "example.com")],
    )
    rule = reverse_proxy_service.apply_reverse_proxy_rule_for_domain(
        _Session(),
        vmid=101,
        vm_ip="10.10.1.10",
        domain="api.example.com",
        internal_port=80,
        enable_https=False,
    )
    assert rule.domain == "api.example.com"
    assert rule.enable_https is False


# ── zone 反查 ────────────────────────────────────────────────────────────


def test_resolve_zone_rejects_invalid_domain() -> None:
    with pytest.raises(BadRequestError):
        reverse_proxy_service.resolve_zone_for_domain(object(), "bad domain")


# ── 網域可用性 ───────────────────────────────────────────────────────────


@pytest.fixture
def avail_env(monkeypatch):
    env = SimpleNamespace(
        taken=False,
        platform=False,
        records=[],
        list_error=None,
        own_rule=None,
        zones=[_zone(_ZONE_ID, "example.com")],
        zones_error=None,
    )
    monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_a, **_k: env.taken)
    monkeypatch.setattr(
        platform_entry_service, "is_platform_domain", lambda _s, _d: env.platform
    )
    monkeypatch.setattr(rp_repo, "get_rule", lambda _s, _id: env.own_rule)

    def list_records(**_kw):
        if env.list_error is not None:
            raise env.list_error
        return SimpleNamespace(items=env.records)

    def zones(_s):
        if env.zones_error is not None:
            raise env.zones_error
        return env.zones

    monkeypatch.setattr(cloudflare_service, "list_dns_records", list_records)
    monkeypatch.setattr(reverse_proxy_service, "_active_zones", zones)
    return env


def test_availability_no_zone(avail_env) -> None:
    avail_env.zones = []
    result = reverse_proxy_service.check_domain_availability(object(), "a.example.com")
    assert result.reason == "no_zone"
    assert result.available is False


def test_availability_zone_lookup_failure_is_unverified(avail_env) -> None:
    avail_env.zones_error = RuntimeError("cloudflare down")
    result = reverse_proxy_service.check_domain_availability(object(), "a.example.com")
    assert result.reason == "unverified"
    assert result.available is True


def test_availability_excludes_own_record_by_string_id(avail_env) -> None:
    avail_env.own_rule = SimpleNamespace(cloudflare_record_id="rec-own")
    avail_env.records = [SimpleNamespace(id="rec-own", name="a.example.com", type="A")]
    rule_id = uuid.uuid4()

    result = reverse_proxy_service.check_domain_availability(
        object(), "a.example.com", exclude_rule_id=str(rule_id)
    )
    assert result.available is True

    avail_env.own_rule = None
    result = reverse_proxy_service.check_domain_availability(
        object(), "a.example.com", exclude_rule_id=rule_id
    )
    assert result.reason == "external"


# ── DNS 紀錄標註 ─────────────────────────────────────────────────────────


def test_annotate_dns_records(monkeypatch) -> None:
    rule_by_id = _rule(vmid=201, domain="x.example.com", cloudflare_record_id="rec-x")
    rule_by_domain = _rule(vmid=202, domain="Y.example.com", cloudflare_record_id=None)
    monkeypatch.setattr(rp_repo, "list_rules", lambda _s: [rule_by_id, rule_by_domain])
    monkeypatch.setattr(
        platform_entry_service, "managed_dns_record_id", lambda _s: "rec-platform"
    )

    def record(rid: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=rid, name=name, managed_by_system=False, managed_vmid=None
        )

    platform = record("rec-platform", "portal.example.com")
    by_id = record("rec-x", "x.example.com")
    by_domain = record("rec-y", "y.example.com")
    foreign = record("rec-z", "z.example.com")

    reverse_proxy_service.annotate_dns_records_with_system_rules(
        object(), [platform, by_id, by_domain, foreign]
    )

    assert platform.managed_by_system is True
    assert platform.managed_vmid is None
    assert (by_id.managed_by_system, by_id.managed_vmid) == (True, 201)
    assert (by_domain.managed_by_system, by_domain.managed_vmid) == (True, 202)
    assert foreign.managed_by_system is False


# ── 清理 ─────────────────────────────────────────────────────────────────


def test_cleanup_managed_dns_record_paths(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        cloudflare_service,
        "delete_reverse_proxy_dns_record",
        lambda **kw: calls.append(kw),
    )

    reverse_proxy_service._cleanup_managed_dns_record(
        object(), _rule(cloudflare_record_id=None)
    )
    assert calls == []

    reverse_proxy_service._cleanup_managed_dns_record(object(), _rule())
    assert calls[0]["record_id"] == "rec-1"

    def fail(**_kw):
        raise RuntimeError("cloudflare down")

    monkeypatch.setattr(cloudflare_service, "delete_reverse_proxy_dns_record", fail)
    reverse_proxy_service._cleanup_managed_dns_record(object(), _rule())


@pytest.fixture
def removal_env(monkeypatch):
    env = SimpleNamespace(cleaned=[], deleted=[], syncs=0)
    monkeypatch.setattr(
        reverse_proxy_service,
        "_cleanup_managed_dns_record",
        lambda _s, rule: env.cleaned.append(rule),
    )
    monkeypatch.setattr(
        rp_repo, "delete_rule", lambda _s, rule: env.deleted.append(rule)
    )

    def sync(_s):
        env.syncs += 1

    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", sync)
    return env


def test_remove_rules_for_vmid(monkeypatch, removal_env) -> None:
    monkeypatch.setattr(rp_repo, "delete_rules_by_vmid", lambda _s, _vmid: [])
    reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(object(), 101)
    assert removal_env.syncs == 0

    rule = _rule()
    monkeypatch.setattr(rp_repo, "delete_rules_by_vmid", lambda _s, _vmid: [rule])
    reverse_proxy_service.remove_reverse_proxy_rules_for_vmid(object(), 101)
    assert removal_env.cleaned == [rule]
    assert removal_env.syncs == 1


def test_remove_rules_by_internal_port(monkeypatch, removal_env) -> None:
    monkeypatch.setattr(rp_repo, "delete_rules_by_vmid_and_port", lambda _s, _v, _p: [])
    reverse_proxy_service.remove_reverse_proxy_rules_by_internal_port(object(), 101, 80)
    assert removal_env.syncs == 0

    rule = _rule()
    monkeypatch.setattr(
        rp_repo, "delete_rules_by_vmid_and_port", lambda _s, _v, _p: [rule]
    )
    reverse_proxy_service.remove_reverse_proxy_rules_by_internal_port(object(), 101, 80)
    assert removal_env.cleaned == [rule]
    assert removal_env.syncs == 1
