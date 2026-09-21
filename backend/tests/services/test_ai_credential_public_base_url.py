"""Quick Start 顯示的 AI API 位址必須跟著目前設定走。

credential.base_url 是核發當下的快照。2026-09-21 發現共用資料庫裡的舊金鑰
帶著 localhost:5000、localhost:18200、別台部署的 IP 等過期位址，使用者照著
Quick Start 去接 n8n 永遠連不上；管理員把 ``AI_API_PUBLIC_BASE_URL`` 改對也沒用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.security import encrypt_value
from app.models import AIAPICredential
from app.services.llm_gateway import ai_gateway_service


def _credential(base_url: str) -> AIAPICredential:
    return AIAPICredential(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        base_url=base_url,
        api_key_encrypted=encrypt_value("ccai_test_key"),
        api_key_prefix="ccai_tes",
        api_key_name="unit",
        created_at=datetime.now(timezone.utc),
    )


def test_stale_snapshot_is_replaced_by_the_current_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_gateway_service.ai_api_settings,
        "ai_api_public_base_url",
        " https://cloud.example.edu ",
    )

    public = ai_gateway_service._to_credential_public(
        _credential("http://localhost:5000")
    )

    assert public.base_url == "https://cloud.example.edu"


def test_snapshot_is_the_fallback_when_the_setting_is_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ai_gateway_service.ai_api_settings, "ai_api_public_base_url", "  "
    )

    public = ai_gateway_service._to_credential_public(
        _credential("https://old.example.edu")
    )

    assert public.base_url == "https://old.example.edu"
