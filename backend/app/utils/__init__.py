"""
Utils 模組

此模組包含所有工具函數：
- Email 相關函數
- Token 相關函數

所有函數均從此處匯出，保持向後相容性。
"""

from .email import (
    EmailData,
    generate_new_account_email,
    generate_reset_password_email,
    generate_test_email,
    render_email_template,
    send_email,
)
from .token import (
    decode_password_reset_token,
    generate_password_reset_token,
    verify_password_reset_token,
)

__all__ = [
    # Email utilities
    "EmailData",
    "render_email_template",
    "send_email",
    "generate_test_email",
    "generate_reset_password_email",
    "generate_new_account_email",
    # Token utilities
    "decode_password_reset_token",
    "generate_password_reset_token",
    "verify_password_reset_token",
]
