"""2026-09-22 安全審查修復的回歸測試。

涵蓋不需要 Redis／資料庫的部分：限流與撤銷名單的 fail-closed 政策、AI 對話
長度上限、金鑰前綴長度與寫入權限、推播訂閱歸屬，以及 AI proxy 不外流上游
錯誤 body。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.ai.utils import (
    MAX_CONVERSATION_CHARS,
    MAX_CONVERSATION_MESSAGES,
    ensure_conversation_within_limits,
)
from app.core.authorizers import require_ai_api_manage
from app.core.permissions import Permission, has_permission
from app.exceptions import AppError, BadRequestError, PermissionDeniedError
from app.infrastructure.redis import rate_limiter, token_blacklist
from app.models import API_KEY_PREFIX_LENGTH, UserRole
from app.repositories import push as push_repo
from app.services.llm_gateway import ai_gateway_service


def _fatal(monkeypatch: pytest.MonkeyPatch, module, value: bool) -> None:
    monkeypatch.setattr(module, "redis_failures_are_fatal", lambda: value)


# ── 1. Redis fail-closed ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_scope_rejects_when_redis_unavailable_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, rate_limiter, True)
    with pytest.raises(AppError) as exc_info:
        await rate_limiter.check_rate_limit_by_key(
            None, key="ip:login:1.2.3.4", limit=10, window_seconds=60, scope="login"
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_other_scopes_still_pass_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, rate_limiter, True)
    allowed, info = await rate_limiter.check_rate_limit_by_key(
        None,
        key="user:vm-request-create:1",
        limit=10,
        window_seconds=60,
        scope="vm-request-create",
    )
    assert allowed is True
    assert info["disabled"] is True


@pytest.mark.asyncio
async def test_local_allows_every_scope_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, rate_limiter, False)
    allowed, _ = await rate_limiter.check_rate_limit_by_key(
        None, key="ip:login:1.2.3.4", limit=10, window_seconds=60, scope="login"
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_ai_proxy_quota_rejects_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, rate_limiter, True)
    with pytest.raises(AppError):
        await rate_limiter.check_rate_limit_sliding_window(None, "user-1", limit=20)


@pytest.mark.asyncio
async def test_revocation_check_fails_closed_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, token_blacklist, True)
    assert await token_blacklist.is_jti_revoked(None, "jti-1") is True
    assert await token_blacklist.mark_refresh_token_used(None, "jti-1", 2**31) is False


@pytest.mark.asyncio
async def test_revocation_check_fails_open_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fatal(monkeypatch, token_blacklist, False)
    assert await token_blacklist.is_jti_revoked(None, "jti-1") is False
    assert await token_blacklist.mark_refresh_token_used(None, "jti-1", 2**31) is True


def test_redis_settings_have_a_single_source() -> None:
    from app.core.config import settings as core_settings
    from app.features.ai.config import settings as ai_settings

    assert ai_settings.redis_enabled is core_settings.REDIS_ENABLED
    assert ai_settings.redis_url == core_settings.REDIS_URL


# ── 2. AI API 金鑰：寫入權限與明文外流 ──────────────────────────────────────


def test_view_all_is_not_a_write_bypass() -> None:
    teacher = SimpleNamespace(id=uuid.uuid4(), role=UserRole.teacher, is_superuser=False)
    assert has_permission(teacher, Permission.AI_API_MANAGE_ALL) is False
    with pytest.raises(PermissionDeniedError):
        require_ai_api_manage(teacher, uuid.uuid4())


def test_admin_may_manage_other_users_credentials() -> None:
    admin = SimpleNamespace(id=uuid.uuid4(), role=UserRole.admin, is_superuser=False)
    assert has_permission(admin, Permission.AI_API_MANAGE_ALL) is True
    require_ai_api_manage(admin, uuid.uuid4())


def test_credential_public_never_carries_the_plaintext_key() -> None:
    from app.schemas import AIAPICredentialPublic

    assert "api_key" not in AIAPICredentialPublic.model_fields


# ── 6. 前綴長度與碰撞 ───────────────────────────────────────────────────────


def test_generated_prefix_uses_the_longer_length() -> None:
    api_key = ai_gateway_service._generate_user_api_key()
    prefix = ai_gateway_service._credential_prefix(api_key)
    assert api_key.startswith("ccai_")
    assert len(prefix) == API_KEY_PREFIX_LENGTH


# ── 4. AI 對話長度上限 ──────────────────────────────────────────────────────


def test_conversation_message_count_is_capped() -> None:
    messages = [SimpleNamespace(content="hi")] * (MAX_CONVERSATION_MESSAGES + 1)
    with pytest.raises(BadRequestError):
        ensure_conversation_within_limits(messages)


def test_conversation_total_length_is_capped() -> None:
    messages = [SimpleNamespace(content="x" * (MAX_CONVERSATION_CHARS + 1))]
    with pytest.raises(BadRequestError):
        ensure_conversation_within_limits(messages)


def test_normal_conversation_passes() -> None:
    ensure_conversation_within_limits([SimpleNamespace(content="請幫我選範本")])


# ── 5. 上游錯誤 body 不外流 ─────────────────────────────────────────────────


def test_upstream_error_body_is_replaced_with_a_generic_message() -> None:
    import json

    import httpx
    from fastapi import Request

    from app.api.routes import ai_proxy

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ai-proxy/chat/completions",
            "headers": [],
            "query_string": b"",
        }
    )
    upstream = httpx.Response(400, text="internal alias qwen-prod @ http://10.0.0.5:4000")
    response = ai_proxy._upstream_failure(
        request=request,
        upstream=upstream,
        body=upstream.content,
        context="relay:chat/completions",
    )
    assert response.status_code == 400
    payload = json.loads(bytes(response.body))
    assert "10.0.0.5" not in payload["error"]["message"]
    assert payload["error"]["code"] == "upstream_error"


# ── 8. 推播訂閱不可被搶走 ───────────────────────────────────────────────────


class _FakeSession:
    def __init__(self) -> None:
        self.deleted: list[object] = []
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def refresh(self, obj: object) -> None:
        pass


def _existing(user_id: uuid.UUID, *, p256dh: str = "p", auth: str = "a"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        endpoint="https://push.example/x",
        p256dh=p256dh,
        auth=auth,
        user_agent=None,
        language="zh-TW",
        failure_count=3,
        last_seen_at=None,
    )


def test_matching_browser_keys_may_take_over_the_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_owner = uuid.uuid4()
    new_owner = uuid.uuid4()
    existing = _existing(previous_owner)
    monkeypatch.setattr(
        push_repo,
        "get_subscription_by_endpoint",
        lambda *, session, endpoint: existing,
    )
    session = _FakeSession()
    result = push_repo.upsert_subscription(
        session=session,  # type: ignore[arg-type]
        user_id=new_owner,
        endpoint="https://push.example/x",
        p256dh="p",
        auth="a",
        user_agent=None,
        language="zh-TW",
    )
    assert result is existing
    assert result.user_id == new_owner
    assert session.deleted == []


def test_mismatched_keys_cannot_steal_someone_elses_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = uuid.uuid4()
    attacker = uuid.uuid4()
    existing = _existing(victim)
    monkeypatch.setattr(
        push_repo,
        "get_subscription_by_endpoint",
        lambda *, session, endpoint: existing,
    )
    session = _FakeSession()
    result = push_repo.upsert_subscription(
        session=session,  # type: ignore[arg-type]
        user_id=attacker,
        endpoint="https://push.example/x",
        p256dh="other-p256dh",
        auth="other-auth",
        user_agent=None,
        language="zh-TW",
    )
    # 舊列被刪掉、另建一筆新的；受害者的訂閱不會被改歸屬
    assert existing.user_id == victim
    assert session.deleted == [existing]
    assert result is not existing
    assert result.user_id == attacker
