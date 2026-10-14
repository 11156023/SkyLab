"""Coverage tests for app.services.network.cloudflare_service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, NotFoundError, UpstreamServiceError
from app.schemas.cloudflare import (
    CloudflareConfigUpdate,
    CloudflareDNSRecordCreate,
    CloudflareDNSRecordUpdate,
    CloudflareZoneCreate,
)
from app.services.network import cloudflare_service as cf

ZONE = "0123456789abcdef0123456789abcdef"
RECORD = "fedcba9876543210fedcba9876543210"
OTHER = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
TOKEN = "t" * 40


def _config(**overrides) -> SimpleNamespace:
    values = {
        "account_id": "acc-1",
        "encrypted_api_token": "enc",
        "default_dns_target_type": "A",
        "default_dns_target_value": "203.0.113.10",
        "updated_at": None,
        "last_verified_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeClient:
    """記錄呼叫的假 Cloudflare API client。"""

    instances: list[_FakeClient] = []
    records: list[dict] = []
    delete_error: Exception | None = None

    def __init__(self, *, api_token: str) -> None:
        self.api_token = api_token
        self.calls: list[tuple] = []
        _FakeClient.instances.append(self)

    def verify_token(self) -> dict:
        self.calls.append(("verify",))
        return {"status": "active"}

    def get_zone(self, zone_id: str) -> dict:
        self.calls.append(("get_zone", zone_id))
        return {"id": zone_id, "name": "example.com", "status": "active"}

    def create_zone(self, *, name: str, account_id: str, jump_start: bool) -> dict:
        self.calls.append(("create_zone", name, account_id, jump_start))
        return {"id": ZONE, "name": name, "status": "pending"}

    def list_dns_records(self, **kwargs) -> dict:
        self.calls.append(("list_dns_records", kwargs))
        return {"result": list(_FakeClient.records)}

    def create_dns_record(self, *, zone_id: str, record: dict) -> dict:
        self.calls.append(("create", zone_id, record))
        return {"id": RECORD, **record}

    def update_dns_record(self, *, zone_id: str, record_id: str, record: dict) -> dict:
        self.calls.append(("update", zone_id, record_id, record))
        return {"id": record_id, **record}

    def delete_dns_record(self, *, zone_id: str, record_id: str) -> None:
        self.calls.append(("delete", zone_id, record_id))
        if _FakeClient.delete_error is not None:
            raise _FakeClient.delete_error


@pytest.fixture
def client_env(monkeypatch):
    _FakeClient.instances = []
    _FakeClient.records = []
    _FakeClient.delete_error = None
    env = SimpleNamespace(config=_config(), verified=[])
    monkeypatch.setattr(cf, "CloudflareAPIClient", _FakeClient)
    monkeypatch.setattr(cf.config_repo, "get_cloudflare_config", lambda _s: env.config)
    monkeypatch.setattr(cf.config_repo, "get_decrypted_api_token", lambda _c: TOKEN)
    monkeypatch.setattr(
        cf.config_repo,
        "mark_cloudflare_config_verified",
        lambda _s, config: env.verified.append(config),
    )
    return env


def _client() -> _FakeClient:
    return _FakeClient.instances[-1]


def _all_calls() -> list[tuple]:
    return [call for client in _FakeClient.instances for call in client.calls]


# ── 公開設定 ─────────────────────────────────────────────────────────────


def test_get_public_config(client_env) -> None:
    public = cf.get_public_config(object())
    assert public.is_configured is True
    assert public.default_dns_target_type == "A"

    client_env.config = None
    assert cf.get_public_config(object()).is_configured is False


# ── 連線 / zone ──────────────────────────────────────────────────────────


def test_list_zones_with_result_info(client_env, monkeypatch) -> None:
    class _ZoneClient(_FakeClient):
        def list_zones(self, **kwargs) -> dict:
            self.calls.append(("list_zones", kwargs))
            return {
                "result": [
                    {
                        "id": ZONE,
                        "name": "example.com",
                        "name_servers": ["a.ns", 3],
                        "original_name_servers": "not-a-list",
                        "development_mode": 0,
                    }
                ],
                "result_info": {
                    "page": 2,
                    "per_page": 10,
                    "total_count": 11,
                    "total_pages": 2,
                },
            }

    monkeypatch.setattr(cf, "CloudflareAPIClient", _ZoneClient)
    result = cf.list_zones(session=object(), search=" ", status="active")

    assert result.items[0].name_servers == ["a.ns"]
    assert result.items[0].original_name_servers == []
    assert result.items[0].development_mode == 0
    assert (result.page_info.page, result.page_info.count) == (2, 1)
    assert _client().calls[0][1] == {
        "page": 1,
        "per_page": 50,
        "search": None,
        "status": "active",
    }


def test_build_client_requires_token(client_env) -> None:
    client_env.config = _config(encrypted_api_token="")
    with pytest.raises(BadRequestError):
        cf.get_zone(session=object(), zone_id=ZONE)

    client_env.config = None
    with pytest.raises(BadRequestError):
        cf.get_zone(session=object(), zone_id=ZONE)


def test_connection_marks_config_verified(client_env) -> None:
    result = cf.test_connection(object())
    assert result.success is True
    assert result.token_status == "active"
    assert client_env.verified == [client_env.config]
    assert _client().api_token == TOKEN


def test_get_zone_normalizes_identifier(client_env) -> None:
    zone = cf.get_zone(session=object(), zone_id=f"  {ZONE.upper()} ")
    assert zone.id == ZONE
    assert _client().calls == [("get_zone", ZONE)]


def test_create_zone_uses_config_account(client_env) -> None:
    zone = cf.create_zone(
        session=object(), data=CloudflareZoneCreate(name=" new.example.com ")
    )
    assert zone.name == "new.example.com"
    assert _client().calls == [("create_zone", "new.example.com", "acc-1", False)]

    cf.create_zone(
        session=object(),
        data=CloudflareZoneCreate(
            name="x.example.com", account_id="acc-2", jump_start=True
        ),
    )
    assert _client().calls == [("create_zone", "x.example.com", "acc-2", True)]


def test_create_zone_requires_account(client_env) -> None:
    client_env.config = _config(account_id=None)
    with pytest.raises(BadRequestError):
        cf.create_zone(
            session=object(), data=CloudflareZoneCreate(name="a.example.com")
        )


# ── DNS 紀錄 CRUD ────────────────────────────────────────────────────────


def test_list_dns_records_maps_payload(client_env) -> None:
    _FakeClient.records = [
        {
            "id": RECORD,
            "type": "a",
            "name": "a.example.com",
            "content": "1.2.3.4",
            "proxied": True,
            "created_on": "not-a-date",
        },
        "garbage",
    ]
    result = cf.list_dns_records(
        session=object(), zone_id=ZONE, record_type=" cname ", search=" a "
    )
    assert [item.id for item in result.items] == [RECORD]
    assert result.items[0].zone_id == ZONE
    assert result.items[0].created_on is None
    assert result.page_info.count == 2
    assert result.page_info.per_page == 2
    kwargs = _client().calls[0][1]
    assert kwargs["record_type"] == "CNAME"
    assert kwargs["search"] == "a"


def test_create_dns_record(client_env) -> None:
    record = cf.create_dns_record(
        session=object(),
        zone_id=ZONE,
        data=CloudflareDNSRecordCreate(
            type="mx", name="example.com", content="mail.example.com", priority=10
        ),
    )
    assert record.id == RECORD
    payload = _client().calls[0][2]
    assert payload["priority"] == 10


def test_update_dns_record(client_env) -> None:
    record = cf.update_dns_record(
        session=object(),
        zone_id=ZONE,
        record_id=RECORD,
        data=CloudflareDNSRecordUpdate(
            type="A", name="a.example.com", content="1.2.3.4", comment="note"
        ),
    )
    assert record.comment == "note"


def test_delete_dns_record_and_reverse_proxy_delete(client_env) -> None:
    cf.delete_dns_record(session=object(), zone_id=ZONE, record_id=RECORD)
    assert _client().calls == [("delete", ZONE, RECORD)]

    _FakeClient.delete_error = NotFoundError("gone")
    cf.delete_reverse_proxy_dns_record(session=object(), zone_id=ZONE, record_id=RECORD)

    _FakeClient.delete_error = None
    cf.delete_reverse_proxy_dns_record(session=object(), zone_id=ZONE, record_id=RECORD)
    assert _client().calls == [("delete", ZONE, RECORD)]


def test_find_dns_record(client_env) -> None:
    _FakeClient.records = [
        {"id": RECORD, "type": "A", "name": "a.example.com", "content": "1.2.3.4"}
    ]
    found = cf.find_dns_record(
        session=object(), zone_id=ZONE, domain=" A.Example.com ", record_id=RECORD
    )
    assert found is not None and found.id == RECORD
    assert (
        cf.find_dns_record(
            session=object(), zone_id=ZONE, domain="a.example.com", record_id=OTHER
        )
        is None
    )


# ── 預設 DNS 目標 / upsert ───────────────────────────────────────────────


def test_get_default_dns_target(client_env) -> None:
    assert cf.get_default_dns_target(object()) == ("A", "203.0.113.10")

    client_env.config = None
    with pytest.raises(BadRequestError):
        cf.get_default_dns_target(object())

    client_env.config = _config(default_dns_target_value=" ")
    with pytest.raises(BadRequestError):
        cf.get_default_dns_target(object())


def test_upsert_reverse_proxy_record_creates_then_updates(client_env) -> None:
    created = cf.upsert_reverse_proxy_dns_record(
        session=object(), zone_id=ZONE, domain="App.Example.com", vmid=101
    )
    assert created.id == RECORD
    creates = [call for call in _all_calls() if call[0] == "create"]
    assert creates[0][2]["comment"] == "SkyLab reverse proxy vmid=101"
    assert creates[0][2]["name"] == "app.example.com"

    _FakeClient.instances = []
    _FakeClient.records = [
        {"id": OTHER, "type": "A", "name": "app.example.com", "content": "1.1.1.1"}
    ]
    updated = cf.upsert_reverse_proxy_dns_record(
        session=object(), zone_id=ZONE, domain="app.example.com", vmid=101
    )
    assert updated.id == OTHER
    assert sorted(call[0] for call in _all_calls()) == ["list_dns_records", "update"]


def test_upsert_platform_record_rejects_foreign_type(client_env) -> None:
    _FakeClient.records = [
        {"id": OTHER, "type": "AAAA", "name": "portal.example.com", "content": "::1"}
    ]
    with pytest.raises(BadRequestError):
        cf.upsert_platform_dns_record(
            session=object(), zone_id=ZONE, domain="portal.example.com", proxied=False
        )


def test_upsert_platform_record_replaces_own_stale_type(client_env) -> None:
    _FakeClient.records = [
        {
            "id": OTHER,
            "type": "CNAME",
            "name": "portal.example.com",
            "content": "old.example.com",
        }
    ]
    cf.upsert_platform_dns_record(
        session=object(),
        zone_id=ZONE,
        domain="portal.example.com",
        proxied=True,
        managed_record_id=OTHER,
    )
    kinds = [call[0] for call in _all_calls() if call[0] != "list_dns_records"]
    assert kinds == ["delete", "create"]


# ── 設定更新 ─────────────────────────────────────────────────────────────


@pytest.fixture
def update_env(monkeypatch):
    env = SimpleNamespace(existing=None, saved={})
    monkeypatch.setattr(
        cf.config_repo, "get_cloudflare_config", lambda _s: env.existing
    )
    monkeypatch.setattr(
        cf.config_repo, "get_decrypted_api_token", lambda _c: "old-token"
    )

    def upsert(_session, **kwargs):
        env.saved = kwargs
        return _config(
            account_id=kwargs["account_id"],
            default_dns_target_type=kwargs["default_dns_target_type"],
            default_dns_target_value=kwargs["default_dns_target_value"],
        )

    monkeypatch.setattr(cf.config_repo, "upsert_cloudflare_config", upsert)
    return env


def test_update_config_keeps_existing_token_and_target(update_env) -> None:
    update_env.existing = _config(
        default_dns_target_type="cname", default_dns_target_value="gw.example.com"
    )
    result = cf.update_config(session=object(), data=CloudflareConfigUpdate())
    assert update_env.saved["api_token"] == "old-token"
    assert update_env.saved["default_dns_target_type"] == "CNAME"
    assert result.is_configured is True
    assert result.has_default_dns_target is True


def test_update_config_requires_token_initially(update_env) -> None:
    with pytest.raises(BadRequestError):
        cf.update_config(session=object(), data=CloudflareConfigUpdate())


def test_update_config_requires_complete_target(update_env) -> None:
    with pytest.raises(BadRequestError):
        cf.update_config(
            session=object(),
            data=CloudflareConfigUpdate(api_token=TOKEN, default_dns_target_type="A"),
        )


def test_identifier_and_text_validation() -> None:
    with pytest.raises(BadRequestError):
        cf._require_identifier("   ", "zone_id")
    with pytest.raises(BadRequestError):
        cf._require_identifier("../zones", "zone_id")
    with pytest.raises(BadRequestError):
        cf._require_text("  ", "domain")


def test_update_config_without_existing_target(update_env) -> None:
    cf.update_config(session=object(), data=CloudflareConfigUpdate(api_token=TOKEN))
    assert update_env.saved["default_dns_target_type"] is None
    assert update_env.saved["default_dns_target_value"] is None


def test_update_config_clears_target_when_both_blank(update_env) -> None:
    cf.update_config(
        session=object(),
        data=CloudflareConfigUpdate(
            api_token=TOKEN, default_dns_target_type=" ", default_dns_target_value=""
        ),
    )
    assert update_env.saved["default_dns_target_type"] is None


@pytest.mark.parametrize(
    ("target_type", "target_value"),
    [("TXT", "hello"), ("A", "not-an-ip"), ("CNAME", "bad host")],
)
def test_update_config_rejects_bad_targets(
    update_env, target_type, target_value
) -> None:
    with pytest.raises(BadRequestError):
        cf.update_config(
            session=object(),
            data=CloudflareConfigUpdate(
                api_token=TOKEN,
                default_dns_target_type=target_type,
                default_dns_target_value=target_value,
            ),
        )


def test_update_config_normalizes_valid_targets(update_env) -> None:
    cf.update_config(
        session=object(),
        data=CloudflareConfigUpdate(
            api_token=TOKEN,
            default_dns_target_type="a",
            default_dns_target_value=" 203.0.113.5 ",
        ),
    )
    assert update_env.saved["default_dns_target_value"] == "203.0.113.5"

    cf.update_config(
        session=object(),
        data=CloudflareConfigUpdate(
            api_token=TOKEN,
            default_dns_target_type="cname",
            default_dns_target_value="GW.Example.com.",
        ),
    )
    assert update_env.saved["default_dns_target_value"] == "gw.example.com"


# ── payload 與轉換 helper ────────────────────────────────────────────────


def test_extract_result_list_rejects_non_list() -> None:
    with pytest.raises(UpstreamServiceError):
        cf._extract_result_list({"result": {"id": "x"}})


def test_page_info_without_result_info() -> None:
    info = cf._to_page_info({"result": None})
    assert (info.page, info.per_page, info.count, info.total_pages) == (1, 50, 0, 1)


@pytest.mark.parametrize(
    ("field", "value"),
    [("type", "   "), ("name", "   "), ("content", "   ")],
)
def test_build_record_payload_requires_fields(field: str, value: str) -> None:
    data = {"type": "A", "name": "a.example.com", "content": "1.2.3.4", field: value}
    with pytest.raises(BadRequestError):
        cf._build_record_payload(CloudflareDNSRecordCreate(**data))


def test_build_record_payload_priority_and_comment_clear() -> None:
    payload = cf._build_record_payload(
        CloudflareDNSRecordUpdate(
            type="srv", name="_sip.example.com", content="x", priority=5, comment=""
        ),
        is_update=True,
    )
    assert payload["priority"] == 5
    assert payload["comment"] == ""


def test_datetime_or_none_variants() -> None:
    assert cf._datetime_or_none("") is None
    assert cf._datetime_or_none("garbage") is None
    assert cf._datetime_or_none("2026-01-01T00:00:00Z").year == 2026
