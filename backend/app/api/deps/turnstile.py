"""Cloudflare Turnstile 機器人驗證的 FastAPI dependency。

token 走 ``X-Turnstile-Token`` 標頭：密碼登入是 OAuth2 form、LDAP 與註冊是
JSON，用標頭三條路由共用同一個 dependency，也不必動到各自的 request schema。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Header

from app.services.user import turnstile_service

TURNSTILE_HEADER = "X-Turnstile-Token"


def require_turnstile(action: str) -> Callable[..., Awaitable[None]]:
    """建立驗證 Turnstile token 的 dependency；``action`` 需與前端 render 時一致。"""

    async def _dep(
        x_turnstile_token: Annotated[str | None, Header(alias=TURNSTILE_HEADER)] = None,
    ) -> None:
        await turnstile_service.verify(x_turnstile_token, action=action)

    return _dep


__all__ = ["TURNSTILE_HEADER", "require_turnstile"]
