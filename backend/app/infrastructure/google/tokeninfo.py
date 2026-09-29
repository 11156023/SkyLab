"""Google OAuth tokeninfo 端點：驗證 ID token 並取回其宣告內容。

只負責對外 HTTP 呼叫；aud／email_verified／帳號等登入政策由
``services/user/auth_service`` 判斷。
"""

from typing import Any

import httpx

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_TIMEOUT_SECONDS = 5.0


class GoogleTokenInfoError(Exception):
    """tokeninfo 驗證失敗的共同基底。"""


class GoogleTokenInfoNetworkError(GoogleTokenInfoError):
    """連不到 Google（網路錯誤、逾時）。"""


class GoogleTokenInfoRejected(GoogleTokenInfoError):
    """Google 回非 200：ID token 無效或已過期。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"tokeninfo returned HTTP {status_code}")
        self.status_code = status_code


async def fetch_id_token_info(id_token: str) -> dict[str, Any]:
    """向 Google tokeninfo 驗證 ID token，回傳其 JSON 內容。

    Raises:
        GoogleTokenInfoNetworkError: 網路錯誤。
        GoogleTokenInfoRejected: Google 回非 200。
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(TOKENINFO_URL, params={"id_token": id_token})
    except httpx.RequestError as exc:
        raise GoogleTokenInfoNetworkError(str(exc)) from exc
    if r.status_code != 200:
        raise GoogleTokenInfoRejected(r.status_code)
    data: dict[str, Any] = r.json()
    return data
