"""機器登入密碼產生器。

所有「系統代發」的登入密碼（範本克隆未填、批次建立未填、快速練習、
重設密碼留空、申請單 API 未帶密碼）都走這裡：字元集排除易混淆的
0O1lI，長度固定 12，讓使用者能在 VNC console 徒手輸入。

`secrets.token_urlsafe` 之類的長隨機字串只適合不給人看的用途，
不要再用它當登入密碼。
"""

import secrets

# 排除易混淆字元（0O1lI）的英數字母表
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LENGTH = 12


def generate_login_password() -> str:
    return "".join(
        secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH)
    )
