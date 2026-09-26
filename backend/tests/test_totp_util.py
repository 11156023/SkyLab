"""``app.utils.totp`` 純函式測試：RFC 6238 測試向量、視窗、防重放、URI。"""

from __future__ import annotations

import base64

from app.utils import totp

# RFC 6238 附錄 B 的 SHA1 測試向量（金鑰 "12345678901234567890"，8 位數）；
# 我們固定 6 位數，所以取向量的後 6 碼比對。
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
_RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


def test_rfc6238_vectors() -> None:
    for ts, expected8 in _RFC_VECTORS:
        assert totp.totp_code(_RFC_SECRET, now=ts) == expected8[-6:]


def test_generate_secret_is_base32_without_padding() -> None:
    secret = totp.generate_secret()
    assert len(secret) == 32
    assert secret == secret.upper()
    assert "=" not in secret
    # 可以被解回 20 bytes
    assert len(base64.b32decode(secret + "=" * (-len(secret) % 8))) == 20
    assert totp.generate_secret() != secret


def test_verify_accepts_adjacent_windows_and_rejects_far() -> None:
    secret = totp.generate_secret()
    now = 1_700_000_000.0
    step = totp.current_step(now)
    # 前一個／目前／下一個 step 都接受
    assert (
        totp.verify_totp(secret, totp.hotp_code(secret, step - 1), now=now) == step - 1
    )
    assert totp.verify_totp(secret, totp.hotp_code(secret, step), now=now) == step
    assert (
        totp.verify_totp(secret, totp.hotp_code(secret, step + 1), now=now) == step + 1
    )
    # 差兩個 step 不接受
    assert totp.verify_totp(secret, totp.hotp_code(secret, step + 2), now=now) is None
    assert totp.verify_totp(secret, totp.hotp_code(secret, step - 2), now=now) is None


def test_verify_replay_protection() -> None:
    secret = totp.generate_secret()
    now = 1_700_000_000.0
    step = totp.current_step(now)
    code = totp.hotp_code(secret, step)
    assert totp.verify_totp(secret, code, now=now, last_used_step=step - 1) == step
    # 同一個 step 用過就不能再用（即使還在視窗內）
    assert totp.verify_totp(secret, code, now=now, last_used_step=step) is None
    # 更早的 step 也不行
    older = totp.hotp_code(secret, step - 1)
    assert totp.verify_totp(secret, older, now=now, last_used_step=step) is None


def test_verify_normalizes_spaces_and_rejects_bad_length() -> None:
    secret = totp.generate_secret()
    now = 1_700_000_000.0
    code = totp.totp_code(secret, now=now)
    spaced = f"{code[:3]} {code[3:]}"
    assert totp.verify_totp(secret, spaced, now=now) is not None
    assert totp.verify_totp(secret, code[:5], now=now) is None
    assert totp.verify_totp(secret, "", now=now) is None
    assert totp.verify_totp(secret, "abcdef", now=now) is None


def test_build_otpauth_uri() -> None:
    uri = totp.build_otpauth_uri("ABC234", account="user@example.com", issuer="Sky Lab")
    assert uri.startswith("otpauth://totp/Sky%20Lab%3Auser%40example.com?")
    assert "secret=ABC234" in uri
    assert "issuer=Sky%20Lab" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
    assert "algorithm=SHA1" in uri
