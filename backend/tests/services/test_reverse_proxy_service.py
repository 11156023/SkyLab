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


_ZONE_ID = "0123456789abcdef0123456789abcdef"


def _patch_apply_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dns_records: list | None = None,
) -> None:
    """把 apply_reverse_proxy_rule 會碰到的外部依賴都換成假的。"""
    monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(reverse_proxy_service, "_sync_nginx", lambda _session: None)
    monkeypatch.setattr(
        reverse_proxy_service,
        "ensure_reverse_proxy_ready",
        lambda _session: None,
    )
    monkeypatch.setattr(
        reverse_proxy_service,
        "assert_publishable_vm_ip",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cloudflare_service,
        "get_zone",
        lambda *, session, zone_id: SimpleNamespace(id=zone_id, name="example.com"),
    )
    monkeypatch.setattr(
        cloudflare_service,
        "list_dns_records",
        lambda **_kwargs: SimpleNamespace(items=dns_records or []),
    )


def test_apply_reverse_proxy_rule_creates_cloudflare_dns_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(vmid=101)))
    created: list = []
    updated: list = []

    _patch_apply_dependencies(monkeypatch)
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
    monkeypatch.setattr(
        rp_repo, "create_rule", lambda _session, rule: created.append(rule) or rule
    )
    monkeypatch.setattr(
        rp_repo, "update_rule", lambda _session, rule: updated.append(rule) or rule
    )

    reverse_proxy_service.apply_reverse_proxy_rule(
        session=session,
        vmid=101,
        vm_ip="10.0.0.15",
        zone_id=_ZONE_ID,
        hostname_prefix="app",
        internal_port=8080,
        enable_https=True,
    )

    rule = created[0]
    # 先寫 DB 佔住網域（此時還沒有 record id），DNS 建好才補回去
    assert updated == [rule]
    assert rule.domain == "app.example.com"
    assert rule.zone_id == _ZONE_ID
    assert rule.cloudflare_record_id == "dns_123"
    assert rule.dns_provider == "cloudflare"


def test_apply_reverse_proxy_rule_rolls_back_db_row_when_dns_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DNS 建不起來時，剛佔位的 DB 規則要收回，網域才不會被卡住。"""
    session = cast(Session, SimpleNamespace(get=lambda *_args, **_kwargs: SimpleNamespace(vmid=101)))
    created: list = []
    deleted: list = []

    _patch_apply_dependencies(monkeypatch)

    def failing_upsert(**_kwargs):
        raise RuntimeError("cloudflare down")

    monkeypatch.setattr(
        cloudflare_service, "upsert_reverse_proxy_dns_record", failing_upsert
    )
    monkeypatch.setattr(
        rp_repo, "create_rule", lambda _session, rule: created.append(rule) or rule
    )
    monkeypatch.setattr(
        rp_repo, "delete_rule", lambda _session, rule: deleted.append(rule)
    )

    with pytest.raises(RuntimeError):
        reverse_proxy_service.apply_reverse_proxy_rule(
            session=session,
            vmid=101,
            vm_ip="10.0.0.15",
            zone_id=_ZONE_ID,
            hostname_prefix="app",
            internal_port=8080,
            enable_https=True,
        )

    assert deleted == created


def test_assert_domain_available_rejects_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """查不到 Cloudflare 時不能放行建立規則（可能覆蓋別人既有的紀錄）。"""
    monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_args, **_kwargs: False)

    def failing_list(**_kwargs):
        raise RuntimeError("cloudflare down")

    monkeypatch.setattr(cloudflare_service, "list_dns_records", failing_list)

    session = cast(Session, object())
    # 表單即時提示仍回 unverified（available=True），不擋使用者打字
    result = reverse_proxy_service.check_domain_availability(
        session, "app.example.com", zone_id=_ZONE_ID
    )
    assert result.available is True
    assert result.reason == "unverified"

    with pytest.raises(BadRequestError):
        reverse_proxy_service.assert_domain_available(
            session, "app.example.com", zone_id=_ZONE_ID
        )


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
