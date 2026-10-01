"""Cloudflare Turnstile 機器人驗證（密碼／LDAP 登入與註冊）。

``settings.turnstile_enabled`` 為假（.env 沒填金鑰）時一律放行，行為與未導入前
相同。啟用後缺 token、驗證失敗或 action 不符都拒絕；連不到 Cloudflare 時
fail closed 回 503——前端小工具本身也要連得到 Cloudflare 才拿得到 token。
"""

import logging

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import AppError, BadRequestError
from app.infrastructure.cloudflare import TurnstileNetworkError, siteverify

logger = logging.getLogger(__name__)

# Cloudflare 文件：token 最長 2048 字元，超過的不必送去驗證
_MAX_TOKEN_LENGTH = 2048


def public_site_key() -> str | None:
    """登入頁用的 site key；未啟用時回 None（前端據此不顯示驗證框）。"""
    return settings.TURNSTILE_SITE_KEY if settings.turnstile_enabled else None


async def verify(token: str | None, *, action: str) -> None:
    """驗證前端送來的 Turnstile token（每個 token 只能用一次）。

    Args:
        token: 前端小工具產生的 token（``X-Turnstile-Token`` 標頭）。
        action: 預期的 action（前端 render 時帶的 ``action``），防止拿別的表單
            的 token 來用。

    Raises:
        BadRequestError: 缺 token、驗證失敗或 action 不符。
        AppError(503): 連不到 Cloudflare。
    """
    secret = settings.TURNSTILE_SECRET_KEY
    if not settings.turnstile_enabled or not secret:
        return
    token = (token or "").strip()
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        raise BadRequestError(t("auth.turnstileRequired"))

    try:
        result = await siteverify(secret=secret, token=token)
    except TurnstileNetworkError:
        logger.warning("Turnstile siteverify unavailable", exc_info=True)
        raise AppError(t("auth.turnstileUnavailable"), 503) from None

    if not result.get("success"):
        logger.info(
            "Turnstile verification rejected: %s", result.get("error-codes") or []
        )
        raise BadRequestError(t("auth.turnstileFailed"))
    # Cloudflare 的測試金鑰（本機開發用）回應不含 action，只帶
    # metadata.result_with_testing_key；正式金鑰一律比對 action
    testing_key = bool((result.get("metadata") or {}).get("result_with_testing_key"))
    if result.get("action") != action and not testing_key:
        logger.info(
            "Turnstile action mismatch: expected %s, got %s",
            action,
            result.get("action"),
        )
        raise BadRequestError(t("auth.turnstileFailed"))
