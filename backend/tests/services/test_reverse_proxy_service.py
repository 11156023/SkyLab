from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from sqlmodel import Session

from app.exceptions import BadRequestError
from app.repositories import reverse_proxy as rp_repo
from app.services.network import cloudflare_service, reverse_proxy_service


def test_build_full_domain_appends_zone_suffix() -> None:
    assert (
        reverse_proxy_service.build_full_domain(
            zone_name="example.com",
            hostname_prefix="app.portal",
        )
        == "app.portal.example.com"
    )
    assert (
        reverse_proxy_service.build_full_domain(
            zone_name="example.com",
            hostname_prefix="",
        )
        == "example.com"
    )


def test_build_full_domain_rejects_invalid_prefix() -> None:
    with pytest.raises(BadRequestError, match="子網域格式不正確"):
        reverse_proxy_service.build_full_domain(
            zone_name="example.com",
            hostname_prefix="bad prefix",
        )


def test_apply_reverse_proxy_rule_creates_cloudflare_dns_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(vmid=101)))
    created_rule: dict[str, object] = {}

    monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(reverse_proxy_service, "_sync_traefik", lambda _session: None)
    monkeypatch.setattr(
        reverse_proxy_service,
        "ensure_reverse_proxy_ready",
        lambda _session: None,
    )
    monkeypatch.setattr(
        cloudflare_service,
        "get_zone",
        lambda *, session, zone_id: SimpleNamespace(id=zone_id, name="example.com"),
    )
    monkeypatch.setattr(
        cloudflare_service,
        "upsert_reverse_proxy_dns_record",
        lambda *, session, zone_id, domain, vmid, existing_zone_id=None, existing_record_id=None: SimpleNamespace(
            id="dns_123",
            type="CNAME",
            zone_id=zone_id,
            name=domain,
        ),
    )

    def fake_create_rule(_session: Session, rule) -> object:
        created_rule["domain"] = rule.domain
        created_rule["zone_id"] = rule.zone_id
        created_rule["cloudflare_record_id"] = rule.cloudflare_record_id
        created_rule["dns_provider"] = rule.dns_provider
        return rule

    monkeypatch.setattr(rp_repo, "create_rule", fake_create_rule)

    reverse_proxy_service.apply_reverse_proxy_rule(
        session=session,
        vmid=101,
        vm_ip="10.0.0.15",
        zone_id="zone_1",
        hostname_prefix="app",
        internal_port=8080,
        enable_https=True,
    )

    assert created_rule == {
        "domain": "app.example.com",
        "zone_id": "zone_1",
        "cloudflare_record_id": "dns_123",
        "dns_provider": "cloudflare",
    }


def test_get_reverse_proxy_setup_context_reports_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())

    monkeypatch.setattr(
        reverse_proxy_service,
        "_get_gateway_ready_state",
        lambda _session: (False, "Gateway VM 尚未設定"),
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "_get_cloudflare_ready_state",
        lambda _session: (False, "Cloudflare 預設 DNS 指向尚未設定", []),
    )

    context = reverse_proxy_service.get_reverse_proxy_setup_context(session)

    assert context.enabled is False
    assert context.gateway_ready is False
    assert context.cloudflare_ready is False
    assert context.zones == []
    assert "Gateway VM 尚未設定" in context.reasons
    assert "Cloudflare 預設 DNS 指向尚未設定" in context.reasons
