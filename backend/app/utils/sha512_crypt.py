"""SHA-512 crypt（``$6$``）雜湊：Linux ``/etc/shadow`` 認得的密碼格式。

標準庫的 ``crypt`` 模組在 Python 3.13 移除，這裡照 Ulrich Drepper 的
SHA-crypt 規格以 ``hashlib`` 實作，輸出與 glibc ``crypt(3)``、
``openssl passwd -6`` 完全相同。

輪數固定用規格預設的 5000 且不寫進輸出：PVE 建立 LXC 時只把
``$6$<salt>$<hash>`` 形式當成已雜湊的密碼，帶 ``rounds=`` 會被當成明文
再雜湊一次。
"""

import hashlib
import secrets

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ROUNDS = 5000
_SALT_LENGTH = 16
_DIGEST_SIZE = 64

# 規格定義的輸出位元組順序：每三個位元組編成四個字元，最後剩一個位元組
_BYTE_ORDER = (
    (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4),
    (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51),
    (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35),
    (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
    (62, 20, 41),
)  # fmt: skip


def _encode_24bit(b2: int, b1: int, b0: int, length: int) -> str:
    value = (b2 << 16) | (b1 << 8) | b0
    chars = []
    for _ in range(length):
        chars.append(_ITOA64[value & 0x3F])
        value >>= 6
    return "".join(chars)


def _repeat_to_length(block: bytes, length: int) -> bytes:
    return (block * (length // len(block) + 1))[:length]


def generate_salt() -> str:
    return "".join(secrets.choice(_ITOA64) for _ in range(_SALT_LENGTH))


def sha512_crypt(password: str, salt: str | None = None) -> str:
    """回傳 ``$6$<salt>$<hash>``；``salt`` 未給時隨機產生 16 個字元。"""
    salt_text = generate_salt() if salt is None else salt[:_SALT_LENGTH]
    pw = password.encode("utf-8")
    salt_bytes = salt_text.encode("ascii")

    alternate = hashlib.sha512(pw + salt_bytes + pw).digest()

    ctx = hashlib.sha512(pw + salt_bytes)
    ctx.update(_repeat_to_length(alternate, len(pw)))
    remaining = len(pw)
    while remaining > 0:
        ctx.update(alternate if remaining & 1 else pw)
        remaining >>= 1
    digest = ctx.digest()

    p_bytes = _repeat_to_length(hashlib.sha512(pw * len(pw)).digest(), len(pw))
    s_bytes = _repeat_to_length(
        hashlib.sha512(salt_bytes * (16 + digest[0])).digest(), len(salt_bytes)
    )

    for round_index in range(_ROUNDS):
        ctx = hashlib.sha512()
        ctx.update(p_bytes if round_index & 1 else digest)
        if round_index % 3:
            ctx.update(s_bytes)
        if round_index % 7:
            ctx.update(p_bytes)
        ctx.update(digest if round_index & 1 else p_bytes)
        digest = ctx.digest()

    encoded = "".join(
        _encode_24bit(digest[a], digest[b], digest[c], 4) for a, b, c in _BYTE_ORDER
    )
    encoded += _encode_24bit(0, 0, digest[_DIGEST_SIZE - 1], 2)
    return f"$6${salt_text}${encoded}"
