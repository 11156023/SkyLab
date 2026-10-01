"""平台帳號密碼的複雜度規則（單一來源）。

只管「登入 SkyLab 的本地帳號密碼」：註冊、管理員新增／改密碼、自行變更、
重設連結、初始化精靈都走 ``ensure_password_complexity``。機器登入密碼是另一套
（``app/utils/login_password`` 與 ``services/template/password_policy``），不套這裡。

檢查放在 service 而不是 ``UserCreate`` schema：``.env`` 的 FIRST_SUPERUSER、
Google／LDAP 影子帳號的隨機密碼也經過那些 schema，不該被複雜度擋下；
登入時更不檢查，舊密碼不符合新規則的帳號照常能登入，下次改密碼才要求。

前端 ``frontend/src/utils/passwordPolicy.js`` 是同一套規則，改這裡要一起改。
"""

from __future__ import annotations

from app.core.i18n import t
from app.exceptions import BadRequestError

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

_ASCII_UPPER = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_ASCII_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_ASCII_DIGIT = frozenset("0123456789")
_ASCII_ALNUM = _ASCII_UPPER | _ASCII_LOWER | _ASCII_DIGIT


def _is_symbol(ch: str) -> bool:
    """英數與空白以外的字元都算特殊符號（含全形標點與非英文字母）。"""
    return ch not in _ASCII_ALNUM and not ch.isspace()


def password_issues(password: str) -> list[str]:
    """回傳未滿足的規則代號（順序固定）；空清單代表通過。

    代號：``length``、``uppercase``、``lowercase``、``digit``、``symbol``。
    """
    issues: list[str] = []
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        issues.append("length")
    if not any(ch in _ASCII_UPPER for ch in password):
        issues.append("uppercase")
    if not any(ch in _ASCII_LOWER for ch in password):
        issues.append("lowercase")
    if not any(ch in _ASCII_DIGIT for ch in password):
        issues.append("digit")
    if not any(_is_symbol(ch) for ch in password):
        issues.append("symbol")
    return issues


def ensure_password_complexity(password: str) -> None:
    """密碼不符合複雜度時丟 400；訊息固定列出完整規則，不回顯密碼內容。"""
    if password_issues(password):
        raise BadRequestError(t("user.passwordTooWeak", min_length=PASSWORD_MIN_LENGTH))


__all__ = [
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "ensure_password_complexity",
    "password_issues",
]
