"""編輯 DNS 紀錄時清空註解要真的送到 Cloudflare（PATCH 省略欄位＝保留舊值）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.cloudflare import CloudflareDNSRecordCreate, CloudflareDNSRecordUpdate
from app.services.network import cloudflare_service

_ZONE_ID = "0123456789abcdef0123456789abcdef"
_RECORD_ID = "fedcba9876543210fedcba9876543210"


def _update(**overrides: Any) -> CloudflareDNSRecordUpdate:
    fields: dict[str, Any] = {"type": "A", "name": "a", "content": "1.1.1.1"}
    fields.update(overrides)
    return CloudflareDNSRecordUpdate(**fields)


@pytest.mark.parametrize("comment", ["", "   ", None])
def test_update_payload_clears_comment_when_explicitly_blank(comment: str | None) -> None:
    payload = cloudflare_service._build_record_payload(
        _update(comment=comment), is_update=True
    )

    assert payload["comment"] == ""


def test_update_payload_keeps_non_blank_comment() -> None:
    payload = cloudflare_service._build_record_payload(
        _update(comment="  owner: lab  "), is_update=True
    )

    assert payload["comment"] == "owner: lab"


def test_update_payload_omits_comment_when_not_sent() -> None:
    payload = cloudflare_service._build_record_payload(_update(), is_update=True)

    assert "comment" not in payload


def test_non_update_payload_omits_blank_comment() -> None:
    payload = cloudflare_service._build_record_payload(_update(comment=""))

    assert "comment" not in payload


def test_create_payload_omits_blank_comment() -> None:
    payload = cloudflare_service._build_record_payload(
        CloudflareDNSRecordCreate(type="A", name="a", content="1.1.1.1", comment="")
    )

    assert "comment" not in payload


def test_update_dns_record_sends_comment_clear_to_cloudflare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _FakeClient:
        def update_dns_record(
            self, *, zone_id: str, record_id: str, record: dict[str, object]
        ) -> dict[str, Any]:
            captured.update(zone_id=zone_id, record_id=record_id, record=record)
            return {"id": record_id, "type": "A", "name": "a", "content": "1.1.1.1"}

    monkeypatch.setattr(
        cloudflare_service,
        "_build_client_from_session",
        lambda _session: (_FakeClient(), object()),
    )

    result = cloudflare_service.update_dns_record(
        session=object(),  # type: ignore[arg-type]
        zone_id=_ZONE_ID,
        record_id=_RECORD_ID,
        data=_update(comment=""),
    )

    assert captured["record"]["comment"] == ""
    assert result.comment is None
