"""Coverage tests for app/api/routes/reverse_proxy.py route branches."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import reverse_proxy as routes
from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.schemas.reverse_proxy import (
    ReverseProxyCertificate,
    ReverseProxyHttpServer,
    ReverseProxyRuleCreate,
    ReverseProxyRuleUpdate,
    ReverseProxyRuntimeSnapshot,
    ReverseProxyStreamServer,
)

SESSION = object()
ADMIN = SimpleNamespace(id=uuid.uuid4(), is_superuser=True)
STUDENT = SimpleNamespace(id=uuid.uuid4(), is_superuser=False)


def _rule(vmid: int = 101, domain: str = "a.example.com", port: int = 80):
    return SimpleNamespace(
        id=uuid.uuid4(),
        vmid=vmid,
        vm_ip="10.0.0.5",
        domain=domain,
        zone_id="zone-1",
        internal_port=port,
        enable_https=True,
        dns_provider="cloudflare",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _raise(exc: BaseException):
    def raiser(*args, **kwargs):
        raise exc

    return raiser


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    routes._runtime_cache.entry = None
    monkeypatch.setattr(
        routes, "can_bypass_resource_ownership", lambda user: user.is_superuser
    )
    yield
    routes._runtime_cache.entry = None


@pytest.fixture
def access_calls(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        routes,
        "check_firewall_access",
        lambda *, vmid, current_user, session: calls.append(vmid),
    )
    return calls


@pytest.fixture
def firewall(monkeypatch):
    calls: list[tuple] = []
    fake = SimpleNamespace(
        publish_vm_service=lambda vmid, spec, session: calls.append(
            ("publish", vmid, spec.port, spec.domain)
        ),
        replace_vm_service=lambda vmid, current, spec, session: calls.append(
            ("replace", vmid, current.port, spec.port)
        ),
        unpublish_vm_service=lambda vmid, ref, session: calls.append(
            ("unpublish", vmid, ref.port)
        ),
        list_vm_published_services=lambda vmid, session: [],
        _get_publishable_vm_ip=lambda vmid, session: "10.0.0.9",
    )
    monkeypatch.setattr(routes, "firewall_service", fake)
    monkeypatch.setattr(
        routes,
        "cloudflare_service",
        SimpleNamespace(
            get_zone=lambda *, session, zone_id: SimpleNamespace(name="example.com")
        ),
    )
    return fake, calls


# ---------------------------------------------------------------------------
# visibility helpers
# ---------------------------------------------------------------------------


def test_visible_rules_admin_sees_all_and_student_is_filtered(monkeypatch) -> None:
    rules = [_rule(101), _rule(202)]
    monkeypatch.setattr(routes.rp_repo, "list_rules", lambda session: rules)
    monkeypatch.setattr(
        routes.resource_access,
        "list_reachable_vmids",
        lambda *, session, user: {202},
    )

    assert routes._get_visible_rules(SESSION, ADMIN) == rules
    assert routes._get_visible_rules(SESSION, STUDENT) == [rules[1]]

    listed = routes.list_reverse_proxy_rules(SESSION, STUDENT)
    assert [rule.vmid for rule in listed] == [202]
    assert listed[0].dns_provider == "cloudflare"


def _snapshot(**kwargs) -> ReverseProxyRuntimeSnapshot:
    return ReverseProxyRuntimeSnapshot(
        version="1.27",
        active=True,
        config_valid=True,
        http_servers=[
            ReverseProxyHttpServer(name="vm101-a", vmid=101, domain="a.example.com"),
            ReverseProxyHttpServer(name="vm202-b", vmid=202, domain="b.example.com"),
        ],
        stream_servers=[
            ReverseProxyStreamServer(name="s", vmid=101, listen=2222, protocol="tcp")
        ],
        certificates=[ReverseProxyCertificate(name="cert")],
        **kwargs,
    )


def test_filter_runtime_snapshot_hides_admin_only_sections(monkeypatch) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        routes,
        "_get_visible_rules",
        lambda session, user: [_rule(202, "b.example.com")],
    )
    monkeypatch.setattr(
        routes.reverse_proxy_service,
        "build_runtime_name",
        lambda vmid, domain: f"vm{vmid}-{domain[0]}",
    )

    assert routes._filter_runtime_snapshot(snapshot, SESSION, ADMIN) is snapshot
    filtered = routes._filter_runtime_snapshot(snapshot, SESSION, STUDENT)

    assert [server.name for server in filtered.http_servers] == ["vm202-b"]
    assert filtered.stream_servers == []
    assert filtered.certificates == []
    assert filtered.version == "1.27"


# ---------------------------------------------------------------------------
# runtime snapshot cache
# ---------------------------------------------------------------------------


def _clock(monkeypatch, value: list[float]) -> None:
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: value[0]))


def test_runtime_snapshot_success_is_cached(monkeypatch) -> None:
    now = [100.0]
    _clock(monkeypatch, now)
    calls: list[int] = []
    snapshot = _snapshot()

    def fetch(*, session):
        calls.append(1)
        return snapshot

    monkeypatch.setattr(routes.nginx_runtime_service, "get_runtime_snapshot", fetch)

    assert routes._load_runtime_snapshot(SESSION) is snapshot
    now[0] = 110.0
    assert routes._load_runtime_snapshot(SESSION) is snapshot
    assert calls == [1]
    now[0] = 200.0
    routes._load_runtime_snapshot(SESSION)
    assert calls == [1, 1]


@pytest.mark.parametrize(
    "error",
    [BadRequestError("gateway not configured"), ProxmoxError("ssh failed")],
)
def test_runtime_snapshot_known_errors_keep_message(monkeypatch, error) -> None:
    _clock(monkeypatch, [0.0])
    monkeypatch.setattr(
        routes.nginx_runtime_service, "get_runtime_snapshot", _raise(error)
    )

    snapshot = routes._load_runtime_snapshot(SESSION)

    assert snapshot.runtime_error == str(error)


def test_runtime_snapshot_unexpected_error_is_generic(monkeypatch) -> None:
    _clock(monkeypatch, [0.0])
    monkeypatch.setattr(
        routes.nginx_runtime_service,
        "get_runtime_snapshot",
        _raise(RuntimeError("secret host details")),
    )

    snapshot = routes._load_runtime_snapshot(SESSION)

    assert snapshot.runtime_error
    assert "secret" not in snapshot.runtime_error


def test_get_runtime_snapshot_masks_error_for_non_admin(monkeypatch) -> None:
    failing = ReverseProxyRuntimeSnapshot(runtime_error="ssh root@10.0.0.1 failed")
    monkeypatch.setattr(routes, "_load_runtime_snapshot", lambda session: failing)
    monkeypatch.setattr(routes, "_get_visible_rules", lambda session, user: [])

    admin_view = routes.get_runtime_snapshot(SESSION, ADMIN)
    student_view = routes.get_runtime_snapshot(SESSION, STUDENT)

    assert admin_view.runtime_error == "ssh root@10.0.0.1 failed"
    assert student_view.runtime_error
    assert "10.0.0.1" not in student_view.runtime_error


# ---------------------------------------------------------------------------
# simple read routes
# ---------------------------------------------------------------------------


def test_setup_context_and_domain_availability(monkeypatch) -> None:
    monkeypatch.setattr(
        routes.reverse_proxy_service,
        "get_reverse_proxy_setup_context",
        lambda *, session: "context",
    )
    seen: list = []
    monkeypatch.setattr(
        routes.reverse_proxy_service,
        "check_domain_availability",
        lambda session, domain, *, exclude_rule_id: (
            seen.append(exclude_rule_id) or "ok"
        ),
    )
    rule_id = uuid.uuid4()

    assert routes.get_setup_context(SESSION, STUDENT) == "context"
    assert routes.get_domain_availability("a.example.com", SESSION, STUDENT) == "ok"
    assert (
        routes.get_domain_availability(
            "a.example.com", SESSION, STUDENT, exclude_rule_id=str(rule_id)
        )
        == "ok"
    )
    assert seen == [None, rule_id]

    with pytest.raises(HTTPException) as exc:
        routes.get_domain_availability(
            "a.example.com", SESSION, STUDENT, exclude_rule_id="not-a-uuid"
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def _create_body() -> ReverseProxyRuleCreate:
    return ReverseProxyRuleCreate(
        vmid=101, zone_id="zone-1", hostname_prefix="app", internal_port=8080
    )


def test_create_rule_publishes_domain_service(access_calls, firewall) -> None:
    _, calls = firewall

    result = routes.create_reverse_proxy_rule(_create_body(), SESSION, STUDENT)

    assert result.message
    assert access_calls == [101]
    assert calls == [("publish", 101, 8080, "app.example.com")]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (BadRequestError("domain taken"), 400),
        (ProxmoxError("pve down"), 502),
        (RuntimeError("boom"), 500),
    ],
)
def test_create_rule_maps_errors(access_calls, firewall, error, status) -> None:
    fake, _ = firewall
    fake.publish_vm_service = _raise(error)

    with pytest.raises(HTTPException) as exc:
        routes.create_reverse_proxy_rule(_create_body(), SESSION, STUDENT)

    assert exc.value.status_code == status


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def _update_body(vmid: int = 101, port: int = 8080) -> ReverseProxyRuleUpdate:
    return ReverseProxyRuleUpdate(
        vmid=vmid, zone_id="zone-1", hostname_prefix="app", internal_port=port
    )


@pytest.fixture
def existing(monkeypatch):
    rule = _rule(101, port=80)
    monkeypatch.setattr(routes.rp_repo, "get_rule", lambda session, rule_id: rule)
    monkeypatch.setattr(
        routes.reverse_proxy_service,
        "assert_domain_available",
        lambda *args, **kwargs: None,
    )
    return rule


def test_update_rule_rejects_invalid_id() -> None:
    with pytest.raises(HTTPException) as exc:
        routes.update_reverse_proxy_rule("nope", _update_body(), SESSION, STUDENT)
    assert exc.value.status_code == 400


def test_update_rule_missing_is_404(monkeypatch) -> None:
    monkeypatch.setattr(routes.rp_repo, "get_rule", lambda session, rule_id: None)
    with pytest.raises(HTTPException) as exc:
        routes.update_reverse_proxy_rule(
            str(uuid.uuid4()), _update_body(), SESSION, STUDENT
        )
    assert exc.value.status_code == 404


def test_update_rule_moves_to_other_vm(access_calls, firewall, existing) -> None:
    _, calls = firewall

    routes.update_reverse_proxy_rule(
        str(existing.id), _update_body(202, 8080), SESSION, STUDENT
    )

    assert access_calls == [101, 202]
    assert calls == [
        ("unpublish", 101, 80),
        ("publish", 202, 8080, "app.example.com"),
    ]


def test_update_rule_other_vm_port_already_published(
    access_calls, firewall, existing
) -> None:
    fake, calls = firewall
    fake.list_vm_published_services = lambda vmid, session: [
        SimpleNamespace(port=8080, protocol="tcp")
    ]

    with pytest.raises(HTTPException) as exc:
        routes.update_reverse_proxy_rule(
            str(existing.id), _update_body(202, 8080), SESSION, STUDENT
        )

    assert exc.value.status_code == 400
    assert calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotFoundError("zone missing"), 404),
        (ProxmoxError("pve down"), 502),
        (RuntimeError("boom"), 500),
    ],
)
def test_update_rule_maps_errors(
    access_calls, firewall, existing, error, status
) -> None:
    fake, _ = firewall
    fake.replace_vm_service = _raise(error)

    with pytest.raises(HTTPException) as exc:
        routes.update_reverse_proxy_rule(
            str(existing.id), _update_body(), SESSION, STUDENT
        )

    assert exc.value.status_code == status


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_rule_rejects_invalid_id() -> None:
    with pytest.raises(HTTPException) as exc:
        routes.delete_reverse_proxy_rule("bad", SESSION, STUDENT)
    assert exc.value.status_code == 400


def test_delete_rule_missing_is_404(monkeypatch) -> None:
    monkeypatch.setattr(routes.rp_repo, "get_rule", lambda session, rule_id: None)
    with pytest.raises(HTTPException) as exc:
        routes.delete_reverse_proxy_rule(str(uuid.uuid4()), SESSION, STUDENT)
    assert exc.value.status_code == 404


def test_delete_rule_unpublishes_and_audits(
    access_calls, firewall, existing, monkeypatch
) -> None:
    _, calls = firewall
    audits: list[dict] = []
    monkeypatch.setattr(
        routes.audit_service, "log_action", lambda **kwargs: audits.append(kwargs)
    )

    result = routes.delete_reverse_proxy_rule(str(existing.id), SESSION, STUDENT)

    assert result.message
    assert access_calls == [101]
    assert calls == [("unpublish", 101, 80)]
    assert audits[0]["vmid"] == 101
    assert audits[0]["action"] == routes.AuditAction.reverse_proxy_rule_delete


@pytest.mark.parametrize(
    ("error", "status"),
    [(ProxmoxError("pve down"), 502), (RuntimeError("boom"), 500)],
)
def test_delete_rule_maps_errors(
    access_calls, firewall, existing, error, status
) -> None:
    fake, _ = firewall
    fake.unpublish_vm_service = _raise(error)

    with pytest.raises(HTTPException) as exc:
        routes.delete_reverse_proxy_rule(str(existing.id), SESSION, STUDENT)

    assert exc.value.status_code == status


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def test_sync_rules_rebuilds_both_configs(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        routes.reverse_proxy_service,
        "sync_to_gateway",
        lambda *, session: calls.append("proxy"),
    )
    monkeypatch.setattr(
        routes.nat_service, "sync_to_gateway", lambda *, session: calls.append("nat")
    )
    monkeypatch.setattr(
        routes.audit_service,
        "log_action",
        lambda **kwargs: calls.append(kwargs["action"].value),
    )

    result = routes.sync_reverse_proxy_rules(SESSION, ADMIN)

    assert result.message
    assert calls == ["proxy", "nat", "reverse_proxy_rule_sync"]


@pytest.mark.parametrize(
    ("error", "status"),
    [(ProxmoxError("pve down"), 502), (RuntimeError("boom"), 500)],
)
def test_sync_rules_maps_errors(monkeypatch, error, status) -> None:
    monkeypatch.setattr(routes.reverse_proxy_service, "sync_to_gateway", _raise(error))

    with pytest.raises(HTTPException) as exc:
        routes.sync_reverse_proxy_rules(SESSION, ADMIN)

    assert exc.value.status_code == status
