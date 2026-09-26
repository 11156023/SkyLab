"""TOTP（RFC 6238）純函式：產生金鑰、計算／驗證 6 位數驗證碼、組 otpauth URI。

只用標準庫（hmac / struct / base64），不依賴 pyotp；參數固定為
Google Authenticator 的預設值（SHA1、6 位數、30 秒），其他 App
（Microsoft Authenticator、Authy、1Password…）也都相容。
"""

from __future__ import annotations

import base64
import hmac
import secrets
import struct
import time
from hashlib import sha1
from urllib.parse import quote

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
# 允許前後各一個時間窗（±30 秒），吸收手機與伺服器的時鐘誤差。
TOTP_WINDOW = 1
# 20 bytes → Base32 32 字元，Google Authenticator 建議的長度。
_SECRET_BYTES = 20


def generate_secret() -> str:
    """產生新的 Base32 金鑰（無 padding、大寫）。"""
    raw = secrets.token_bytes(_SECRET_BYTES)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * (-len(normalized) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def current_step(now: float | None = None) -> int:
    """目前時間對應的計數器（time step）。"""
    ts = time.time() if now is None else now
    return int(ts // TOTP_PERIOD_SECONDS)


def hotp_code(secret: str, counter: int) -> str:
    """RFC 4226 HOTP：依計數器算出 6 位數驗證碼。"""
    key = _decode_secret(secret)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def totp_code(secret: str, now: float | None = None) -> str:
    """目前時間窗的驗證碼（測試與示範用；正式驗證走 ``verify_totp``）。"""
    return hotp_code(secret, current_step(now))


def normalize_code(code: str) -> str:
    """去掉使用者可能輸入的空白／連字號（App 常顯示成 ``123 456``）。"""
    return "".join(ch for ch in code if ch.isdigit())


def verify_totp(
    secret: str,
    code: str,
    *,
    now: float | None = None,
    window: int = TOTP_WINDOW,
    last_used_step: int | None = None,
) -> int | None:
    """驗證驗證碼；成功回傳命中的 time step，失敗回傳 None。

    ``last_used_step`` 是上次成功使用的 step：同一個或更早的 step 一律拒絕，
    避免驗證碼在 30 秒內被重放（RFC 6238 §5.2）。比對用 ``hmac.compare_digest``
    避免時序側通道。
    """
    normalized = normalize_code(code)
    if len(normalized) != TOTP_DIGITS:
        return None
    step = current_step(now)
    matched: int | None = None
    # 固定走完整個視窗（不提前 return），比對成本與輸入無關。
    for offset in range(-window, window + 1):
        candidate = step + offset
        if hmac.compare_digest(hotp_code(secret, candidate), normalized):
            if matched is None or candidate > matched:
                matched = candidate
    if matched is None:
        return None
    if last_used_step is not None and matched <= last_used_step:
        return None
    return matched


def build_otpauth_uri(secret: str, *, account: str, issuer: str) -> str:
    """組出 Google Authenticator 可掃描的 ``otpauth://totp/...`` URI。"""
    label = f"{issuer}:{account}"
    return (
        f"otpauth://totp/{quote(label, safe='')}"
        f"?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


__all__ = [
    "TOTP_DIGITS",
    "TOTP_PERIOD_SECONDS",
    "TOTP_WINDOW",
    "build_otpauth_uri",
    "current_step",
    "generate_secret",
    "hotp_code",
    "normalize_code",
    "totp_code",
    "verify_totp",
]
