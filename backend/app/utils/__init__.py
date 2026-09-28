"""
Utils 模組

這裡只重新匯出 Email 與密碼重設 Token 的工具函數（保持向後相容）；
totp、hostname、login_password、timeutil、websocket 等子模組請直接從
各自的模組 import。
"""

from .email import (
    EmailData,
    generate_new_account_email,
    generate_reset_password_email,
    render_email_template,
    send_email,
)
from .token import (
    decode_password_reset_token,
    generate_password_reset_token,
)

__all__ = [
    # Email utilities
    "EmailData",
    "render_email_template",
    "send_email",
    "generate_reset_password_email",
    "generate_new_account_email",
    # Token utilities
    "decode_password_reset_token",
    "generate_password_reset_token",
]
