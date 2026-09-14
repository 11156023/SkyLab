"""網域可用性：本系統紀錄、Cloudflare 既有紀錄（非本系統建立）都算衝突；查不到 Cloudflare 只警告。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.services.network import reverse_proxy_service as rps


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(
        taken=False, records=[], own_rule=None, fail_cloudflare=False
    )

    import app.repositories.reverse_proxy as rp_repo
    import app.services.network.cloudflare_service as cf

    monkeypatch.setattr(
        rp_repo,
        "is_domain_taken",
        lambda session, domain, exclude_rule_id=None: state.taken,
    )
    monkeypatch.setattr(rp_repo, "get_rule", lambda session, rule_id: state.own_rule)
    monkeypatch.setattr(
        rps, "resolve_zone_for_domain", lambda session, domain: ("zone-1", "app")
    )

    def list_dns_records(**kwargs):
        if state.fail_cloudflare:
            raise RuntimeError("cloudflare down")
        return SimpleNamespace(items=state.records)

    monkeypatch.setattr(cf, "list_dns_records", list_dns_records)
    return state


def _record(rid: str, name: str, rtype: str = "A") -> SimpleNamespace:
    return SimpleNamespace(id=rid, name=name, type=rtype)


def test_system_rule_conflict(env: SimpleNamespace) -> None:
    env.taken = True
    result = rps.check_domain_availability(None, "app.example.com")
    assert result.available is False
    assert result.reason == "system"


def test_external_record_conflict(env: SimpleNamespace) -> None:
    env.records = [_record("r1", "app.example.com", "CNAME")]
    result = rps.check_domain_availability(None, "APP.example.com.")
    assert result.domain == "app.example.com"
    assert result.available is False
    assert result.reason == "external"
    with pytest.raises(BadRequestError):
        rps.assert_domain_available(None, "app.example.com")


def test_txt_record_does_not_conflict(env: SimpleNamespace) -> None:
    env.records = [_record("r1", "app.example.com", "TXT")]
    result = rps.check_domain_availability(None, "app.example.com")
    assert result.available is True
    assert result.reason is None


def test_own_record_excluded_on_update(env: SimpleNamespace) -> None:
    own_id = uuid.uuid4()
    env.own_rule = SimpleNamespace(id=own_id, cloudflare_record_id="r1")
    env.records = [_record("r1", "app.example.com", "A")]
    result = rps.check_domain_availability(
        None, "app.example.com", exclude_rule_id=own_id
    )
    assert result.available is True


def test_cloudflare_unreachable_is_unverified(env: SimpleNamespace) -> None:
    env.fail_cloudflare = True
    result = rps.check_domain_availability(None, "app.example.com")
    assert result.available is True
    assert result.reason == "unverified"


def test_invalid_hostname(env: SimpleNamespace) -> None:
    result = rps.check_domain_availability(None, "bad host!.example.com")
    assert result.available is False
    assert result.reason == "invalid"
