"""機器登入密碼產生器。

所有「系統代發」的登入密碼（範本克隆未填、批次建立未填、快速練習、
重設密碼留空、申請單 API 未帶密碼）都走這裡：字元集排除易混淆的
0O1lI，長度固定 12，讓使用者能在 VNC console 徒手輸入。

`secrets.token_urlsafe` 之類的長隨機字串只適合不給人看的用途，
不要再用它當登入密碼。

使用者自訂的密碼不存可還原的形式：`hash_login_password` 轉成 Linux
認得的 SHA-512 crypt 雜湊，建機時直接把雜湊寫進機器（PVE 的
``cipassword``、LXC 建立的 ``password``、容器內的 ``chpasswd -e`` 都收）。
"""

import re
import secrets

from app.utils.sha512_crypt import sha512_crypt

# 排除易混淆字元（0O1lI）的英數字母表
PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789"
PASSWORD_LENGTH = 12


def generate_login_password() -> str:
    return "".join(
        secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH)
    )


# 與 PVE 判斷「這是已雜湊的密碼」的規則相容（pve-container 的最嚴格）
_LOGIN_PASSWORD_HASH_RE = re.compile(r"\$6\$[a-zA-Z0-9./]{1,16}\$[a-zA-Z0-9./]{86}")


def hash_login_password(password: str) -> str:
    """把自訂登入密碼轉成不可還原的 SHA-512 crypt 雜湊。"""
    return sha512_crypt(password)


def is_login_password_hash(value: str | None) -> bool:
    return bool(value) and _LOGIN_PASSWORD_HASH_RE.fullmatch(str(value)) is not None
