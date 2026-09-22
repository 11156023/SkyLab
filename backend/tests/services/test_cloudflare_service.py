from __future__ import annotations

from typing import cast

import pytest
from sqlmodel import Session

from app.exceptions import BadRequestError
from app.models.cloudflare_config import CloudflareConfig
from app.schemas.cloudflare import CloudflareConfigUpdate, CloudflareDNSRecordCreate
from app.services.network import cloudflare_service


def test_get_public_config_returns_defaults_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())
    monkeypatch.setattr(
        cloudflare_service.config_repo,
        "get_cloudflare_config",
        lambda _: None,
    )

    result = cloudflare_service.get_public_config(session)

    assert result.is_configured is False
    assert result.has_api_token is False
    assert result.account_id is None


def test_update_config_requires_token_on_first_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())
    monkeypatch.setattr(
        cloudflare_service.config_repo,
        "get_cloudflare_config",
        lambda _: None,
    )

    with pytest.raises(BadRequestError, match="初次設定必須提供 Cloudflare API Token"):
        cloudflare_service.update_config(
            session=session,
            data=CloudflareConfigUpdate(account_id="acc_123", api_token=None),
        )


def test_list_zones_maps_cloudflare_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())

    class FakeClient:
        def list_zones(
            self,
            *,
            page: int,
            per_page: int,
            search: str | None,
            status: str | None,
        ) -> dict[str, object]:
            assert page == 1
            assert per_page == 50
            assert search == "campus"
            assert status == "active"
            return {
                "result": [
                    {
                        "id": "zone_1",
                        "name": "campus.example.com",
                        "status": "active",
                        "paused": False,
                        "name_servers": ["amy.ns.cloudflare.com", "matt.ns.cloudflare.com"],
                        "created_on": "2026-04-12T00:00:00Z",
                        "modified_on": "2026-04-12T01:00:00Z",
                    }
                ],
                "result_info": {
                    "page": 1,
                    "per_page": 50,
                    "count": 1,
                    "total_count": 1,
                    "total_pages": 1,
                },
            }

    monkeypatch.setattr(
        cloudflare_service,
        "_build_client_from_session",
        lambda _: (FakeClient(), CloudflareConfig()),
    )

    result = cloudflare_service.list_zones(
        session=session,
        page=1,
        per_page=50,
        search="campus",
        status="active",
    )

    assert result.page_info.total_count == 1
    assert result.items[0].id == "zone_1"
    assert result.items[0].name_servers == [
        "amy.ns.cloudflare.com",
        "matt.ns.cloudflare.com",
    ]


def test_build_record_payload_omits_unsupported_fields() -> None:
    payload = cloudflare_service._build_record_payload(
        CloudflareDNSRecordCreate(
            type="txt",
            name="_acme-challenge",
            content="token-value",
            ttl=120,
            proxied=True,
            comment="  verification token  ",
            priority=10,
        )
    )

    assert payload == {
        "type": "TXT",
        "name": "_acme-challenge",
        "content": "token-value",
        "ttl": 120,
        "comment": "verification token",
    }


def test_build_record_payload_keeps_supported_fields() -> None:
    payload = cloudflare_service._build_record_payload(
        CloudflareDNSRecordCreate(
            type="A",
            name="app",
            content="203.0.113.10",
            ttl=1,
            proxied=True,
            priority=None,
        )
    )

    assert payload["type"] == "A"
    assert payload["proxied"] is True


def test_update_config_persists_default_dns_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())
    captured: dict[str, str | None] = {}

    monkeypatch.setattr(
        cloudflare_service.config_repo,
        "get_cloudflare_config",
        lambda _: None,
    )

    def fake_upsert_cloudflare_config(
        _session: Session,
        *,
        account_id: str | None,
        api_token: str,
        default_dns_target_type: str | None,
        default_dns_target_value: str | None,
    ) -> CloudflareConfig:
        captured["account_id"] = account_id
        captured["api_token"] = api_token
        captured["default_dns_target_type"] = default_dns_target_type
        captured["default_dns_target_value"] = default_dns_target_value
        return CloudflareConfig(
            account_id=account_id or "",
            encrypted_api_token="encrypted",
            default_dns_target_type=default_dns_target_type or "",
            default_dns_target_value=default_dns_target_value or "",
        )

    monkeypatch.setattr(
        cloudflare_service.config_repo,
        "upsert_cloudflare_config",
        fake_upsert_cloudflare_config,
    )

    result = cloudflare_service.update_config(
        session=session,
        data=CloudflareConfigUpdate(
            account_id="acc_123",
            api_token="cf-api-token-with-sufficient-length",
            default_dns_target_type="A",
            default_dns_target_value="203.0.113.10",
        ),
    )

    assert captured == {
        "account_id": "acc_123",
        "api_token": "cf-api-token-with-sufficient-length",
        "default_dns_target_type": "A",
        "default_dns_target_value": "203.0.113.10",
    }
    assert result.has_default_dns_target is True
    assert result.default_dns_target_type == "A"
    assert result.default_dns_target_value == "203.0.113.10"


def test_update_config_requires_complete_default_dns_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = cast(Session, object())
    monkeypatch.setattr(
        cloudflare_service.config_repo,
        "get_cloudflare_config",
        lambda _: None,
    )

    with pytest.raises(BadRequestError, match="預設 DNS 指向必須同時提供類型與內容"):
        cloudflare_service.update_config(
            session=session,
            data=CloudflareConfigUpdate(
                account_id="acc_123",
                api_token="cf-api-token-with-sufficient-length",
                default_dns_target_type="A",
                default_dns_target_value=None,
            ),
        )


def test_require_identifier_rejects_non_hex_ids() -> None:
    """zone / record id 會直接拼進 Cloudflare API 路徑，格式不對先擋下來。"""
    valid = "0123456789ABCDEF0123456789abcdef"
    assert cloudflare_service._require_identifier(valid, "zone_id") == valid.lower()

    for bad in ["", "   ", "zone-1", "../zones", "0123456789abcdef", valid + "0"]:
        with pytest.raises(BadRequestError):
            cloudflare_service._require_identifier(bad, "zone_id")


def test_require_text_still_accepts_domains() -> None:
    """網域不是 id，只檢查非空白。"""
    assert (
        cloudflare_service._require_text(" App.Example.com ", "domain")
        == "App.Example.com"
    )

    with pytest.raises(BadRequestError):
        cloudflare_service._require_text("  ", "domain")
