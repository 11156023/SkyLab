"""app/api/routes/cloudflare.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import cloudflare as cf
from app.models import AuditAction
from app.services.network import reverse_proxy_service

SESSION = object()
USER = SimpleNamespace(id=uuid.uuid4())


@pytest.fixture()
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cf.audit_service, "log_action", lambda **kw: calls.append(kw))
    return calls


def test_get_and_test_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cf.cloudflare_service, "get_public_config", lambda s: "public")
    monkeypatch.setattr(cf.cloudflare_service, "test_connection", lambda s: "tested")
    assert cf.get_config(session=SESSION, _=USER) == "public"
    assert cf.test_config(session=SESSION, _=USER) == "tested"


@pytest.mark.parametrize(
    ("account_id", "token", "expected"),
    [
        ("acc-1", "new-token", "account_id=acc-1, token_rotated=yes"),
        (None, None, "account_id=unset, token_rotated=no"),
    ],
)
def test_update_config(
    monkeypatch: pytest.MonkeyPatch,
    audits: list,
    account_id: str | None,
    token: str | None,
    expected: str,
) -> None:
    result = SimpleNamespace(account_id=account_id)
    monkeypatch.setattr(
        cf.cloudflare_service, "update_config", lambda session, data: result
    )
    data = SimpleNamespace(api_token=token)
    assert cf.update_config(data=data, session=SESSION, current_user=USER) is result
    assert audits[0]["action"] == AuditAction.cloudflare_config_update
    assert expected in audits[0]["details"]
    # 金鑰本身不可出現在稽核紀錄
    if token:
        assert token not in audits[0]["details"]


def test_list_and_get_zones(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _list(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "zones"

    monkeypatch.setattr(cf.cloudflare_service, "list_zones", _list)
    monkeypatch.setattr(
        cf.cloudflare_service, "get_zone", lambda session, zone_id: ("zone", zone_id)
    )
    assert (
        cf.list_zones(
            session=SESSION, _=USER, page=2, per_page=10, search="ex", status="active"
        )
        == "zones"
    )
    assert captured == {
        "session": SESSION,
        "page": 2,
        "per_page": 10,
        "search": "ex",
        "status": "active",
    }
    assert cf.get_zone(zone_id="z1", session=SESSION, _=USER) == ("zone", "z1")


def test_create_zone(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    zone = SimpleNamespace(name="example.com")
    monkeypatch.setattr(
        cf.cloudflare_service, "create_zone", lambda session, data: zone
    )
    assert cf.create_zone(data=object(), session=SESSION, current_user=USER) is zone
    assert audits[0]["action"] == AuditAction.cloudflare_zone_create
    assert "example.com" in audits[0]["details"]


def test_list_dns_records_annotates_system_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = SimpleNamespace(items=["r1", "r2"])
    captured: dict[str, Any] = {}

    def _list(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return result

    annotated: list[Any] = []
    monkeypatch.setattr(cf.cloudflare_service, "list_dns_records", _list)
    monkeypatch.setattr(
        reverse_proxy_service,
        "annotate_dns_records_with_system_rules",
        lambda session, items: annotated.append(items),
    )
    assert (
        cf.list_dns_records(
            zone_id="z1",
            session=SESSION,
            _=USER,
            page=1,
            per_page=50,
            search=None,
            record_type="A",
            proxied=False,
        )
        is result
    )
    assert captured["record_type"] == "A"
    assert captured["proxied"] is False
    assert annotated == [["r1", "r2"]]


def test_dns_record_mutations(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    record = SimpleNamespace(type="A", name="vm.example.com")
    monkeypatch.setattr(cf.cloudflare_service, "create_dns_record", lambda **kw: record)
    monkeypatch.setattr(cf.cloudflare_service, "update_dns_record", lambda **kw: record)
    deleted: list[Any] = []
    monkeypatch.setattr(
        cf.cloudflare_service,
        "delete_dns_record",
        lambda **kw: deleted.append((kw["zone_id"], kw["record_id"])),
    )

    assert (
        cf.create_dns_record(
            zone_id="z1", data=object(), session=SESSION, current_user=USER
        )
        is record
    )
    assert (
        cf.update_dns_record(
            zone_id="z1",
            record_id="r1",
            data=object(),
            session=SESSION,
            current_user=USER,
        )
        is record
    )
    result = cf.delete_dns_record(
        zone_id="z1", record_id="r1", session=SESSION, current_user=USER
    )
    assert result.message
    assert deleted == [("z1", "r1")]
    assert [a["action"] for a in audits] == [
        AuditAction.cloudflare_dns_record_create,
        AuditAction.cloudflare_dns_record_update,
        AuditAction.cloudflare_dns_record_delete,
    ]
    assert "A vm.example.com in zone z1" in audits[0]["details"]
    assert "Deleted DNS record r1 in zone z1" in audits[2]["details"]
