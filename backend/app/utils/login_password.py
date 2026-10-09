"""機器登入密碼產生器。

所有「系統代發」的登入密碼（範本克隆未填、批次建立未填、快速練習、
重設密碼留空、申請單 API 未帶密碼）都走這裡：字元集排除易混淆的
0O1lI，長度固定 12，讓使用者能在 VNC console 徒手輸入。

`secrets.token_urlsafe` 之類的長隨機字串只適合不給人看的用途，
不要再用它當登入密碼。

產生的密碼保證大寫、小寫、數字各至少一個：Windows 預設啟用密碼複雜度，
cloudbase-init 設的密碼不到三類會被系統拒絕，機器就登不進去。規則本身
在 `windows_password_issues`。

使用者自訂的密碼不存可還原的形式：`hash_login_password` 轉成 Linux
認得的 SHA-512 crypt 雜湊，建機時直接把雜湊寫進機器（PVE 的
``cipassword``、LXC 建立的 ``password``、容器內的 ``chpasswd -e`` 都收）。
"""

import re
import secrets

from app.utils.sha512_crypt import sha512_crypt

# 排除易混淆字元（0O1lI）的英數字母表
_LOWER = "abcdefghijkmnpqrstuvwxyz"
_UPPER = "ACDEFGHJKLMNPQRSTUVWXYZ"
_DIGITS = "23456789"
PASSWORD_ALPHABET = _LOWER + _UPPER + _DIGITS
PASSWORD_LENGTH = 12

# cloudbase-init 設定檔固定的 Windows 登入帳號（申請表單也顯示這個名字）
WINDOWS_LOGIN_USERNAME = "Admin"


def generate_login_password() -> str:
    rng = secrets.SystemRandom()
    while True:
        chars = [secrets.choice(group) for group in (_LOWER, _UPPER, _DIGITS)]
        chars += [
            secrets.choice(PASSWORD_ALPHABET)
            for _ in range(PASSWORD_LENGTH - len(chars))
        ]
        rng.shuffle(chars)
        password = "".join(chars)
        if not windows_password_issues(password):
            return password


def _char_category(ch: str) -> str:
    if ch.isupper():
        return "upper"
    if ch.islower():
        return "lower"
    if ch.isdigit():
        return "digit"
    if ch.isalpha():
        # 沒有大小寫之分的字母（中日文等），Windows 另算一類
        return "letter"
    return "symbol"


def windows_password_issues(
    password: str, *, username: str = WINDOWS_LOGIN_USERNAME
) -> list[str]:
    """Windows 密碼複雜度（passfilt）不滿足的項目；空清單代表通過。

    - ``categories``：大寫、小寫、數字、符號、其他字母五類至少三類
    - ``username``：不可包含帳號名稱（不分大小寫；帳號名稱 3 字以上才檢查）

    最短長度是另一條原則，由各入口的 schema 管（至少 8 碼）。
    """
    issues: list[str] = []
    if len({_char_category(ch) for ch in password}) < 3:
        issues.append("categories")
    if len(username) >= 3 and username.lower() in password.lower():
        issues.append("username")
    return issues


# 與 PVE 判斷「這是已雜湊的密碼」的規則相容（pve-container 的最嚴格）
_LOGIN_PASSWORD_HASH_RE = re.compile(r"\$6\$[a-zA-Z0-9./]{1,16}\$[a-zA-Z0-9./]{86}")


def hash_login_password(password: str) -> str:
    """把自訂登入密碼轉成不可還原的 SHA-512 crypt 雜湊。"""
    return sha512_crypt(password)


def is_login_password_hash(value: str | None) -> bool:
    return bool(value) and _LOGIN_PASSWORD_HASH_RE.fullmatch(str(value)) is not None
