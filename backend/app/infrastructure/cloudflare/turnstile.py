"""Cloudflare Turnstile siteverify：向 Cloudflare 驗證前端小工具拿到的 token。

只負責對外 HTTP 呼叫；是否啟用、action 是否相符等判斷由
``services/user/turnstile_service`` 負責。
"""

from typing import Any

import httpx

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_TIMEOUT_SECONDS = 5.0


class TurnstileNetworkError(Exception):
    """連不到 Cloudflare（網路錯誤、逾時）或 siteverify 回非 200。"""


async def siteverify(*, secret: str, token: str) -> dict[str, Any]:
    """呼叫 siteverify，回傳其 JSON（``success``、``error-codes``、``action`` 等）。

    不帶 ``remoteip``：部署在 NAT／Cloudflare Tunnel 後面時後端看到的來源 IP
    不一定是瀏覽器的 IP，帶錯反而會讓合法的 token 被拒。

    Raises:
        TurnstileNetworkError: 網路錯誤或 Cloudflare 回非 200。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(
                SITEVERIFY_URL, data={"secret": secret, "response": token}
            )
    except httpx.RequestError as exc:
        raise TurnstileNetworkError(str(exc)) from exc
    if r.status_code != 200:
        raise TurnstileNetworkError(f"siteverify returned HTTP {r.status_code}")
    data: dict[str, Any] = r.json()
    return data
