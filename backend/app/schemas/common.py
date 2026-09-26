"""通用 schemas：Token、Message 等"""

from pydantic import BaseModel, Field


class Message(BaseModel):
    """通用訊息回應"""

    message: str


class Token(BaseModel):
    """JWT 存取權杖回應"""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT Token payload"""

    sub: str | None = None
    type: str | None = None
    ver: int = 0
    jti: str | None = None
    exp: int | None = None
    # 只有 type="totp" 的挑戰 token 會帶：第一階段用的登入方式（password|google|ldap）
    method: str | None = None


class TotpChallenge(BaseModel):
    """第一階段（密碼／Google／LDAP）通過但帳號已綁定兩步驟驗證：

    不發正式 token，改回短效挑戰 token，前端拿驗證碼呼叫 ``/login/totp`` 換取。
    """

    totp_required: bool = True
    totp_token: str


class TotpLoginRequest(BaseModel):
    """第二階段：挑戰 token + Authenticator 驗證碼"""

    totp_token: str
    code: str = Field(min_length=6, max_length=16)


class NewPassword(BaseModel):
    """重設密碼請求"""

    token: str
    new_password: str = Field(min_length=8, max_length=128)
