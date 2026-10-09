"""SHA-512 crypt（``$6$``）雜湊：Linux ``/etc/shadow`` 認得的密碼格式。

標準庫的 ``crypt`` 模組在 Python 3.13 移除。Linux 上直接以 ctypes 呼叫系統
libcrypt（glibc／libxcrypt，正式映像與 CI 都有）的 ``crypt_r``：C 實作、
呼叫期間釋放 GIL，整班同時送申請單時雜湊不會把所有請求串成一列。
沒有 libcrypt 或它不支援 ``$6$`` 時（Windows 開發機、macOS）改用下面照
Ulrich Drepper 的 SHA-crypt 規格以 ``hashlib`` 寫的實作。兩者輸出與 glibc
``crypt(3)``、``openssl passwd -6`` 完全相同；匯入時先用規格的測試向量驗過
系統實作才採用，不是碰到錯誤才退回。

CodeQL 會把 ``hashlib`` 版本標成「對密碼使用非計算密集的雜湊」：SHA-crypt
是固定 5000 輪的 KDF，就是 crypt(3) 的標準方案，只是 CodeQL 沒有建模，
這類告警在 PR 上以 false positive 關閉即可。不要為了消掉它改用別的格式，
PVE 與 cloud-init 只收 ``$6$``。

輪數固定用規格預設的 5000 且不寫進輸出：PVE 建立 LXC 時只把
``$6$<salt>$<hash>`` 形式當成已雜湊的密碼，帶 ``rounds=`` 會被當成明文
再雜湊一次。
"""

import ctypes
import ctypes.util
import hashlib
import logging
import secrets
from collections.abc import Callable

logger = logging.getLogger(__name__)

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SALT_CHARS = frozenset(_ITOA64)
_ROUNDS = 5000
_SALT_LENGTH = 16
_DIGEST_SIZE = 64

# glibc 的 struct crypt_data 約 128 KiB、libxcrypt 的 32 KiB；給足並歸零
# （initialized = 0 是 crypt_r 的要求）。
_CRYPT_DATA_SIZE = 256 * 1024

# Ulrich Drepper 的 SHA-crypt 規格測試向量，用來驗證系統 libcrypt 支援 $6$
_REFERENCE_PASSWORD = b"Hello world!"
_REFERENCE_SALT = "saltstring"
_REFERENCE_HASH = (
    "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJu"
    "esI68u4OTLiBFdcbYEdFCoEOfaS35inz1"
)

# 規格定義的輸出位元組順序：每三個位元組編成四個字元，最後剩一個位元組
_BYTE_ORDER = (
    (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4),
    (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51),
    (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35),
    (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
    (62, 20, 41),
)  # fmt: skip

Backend = Callable[[bytes, str], str]
"""``(password_bytes, salt) -> "$6$<salt>$<hash>"``"""


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


def sha512_crypt_hashlib(pw: bytes, salt_text: str) -> str:
    """純 Python 的 SHA-crypt 規格實作；沒有系統 libcrypt 的平台用這個。"""
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


def _load_libcrypt() -> Backend | None:
    """找得到系統 libcrypt 且它算出規格向量時回傳包好的 ``crypt_r``。"""
    names: list[str] = []
    found = ctypes.util.find_library("crypt")
    if found:
        names.append(found)
    names += ["libcrypt.so.1", "libcrypt.so.2"]

    for name in dict.fromkeys(names):
        try:
            crypt_r = ctypes.CDLL(name).crypt_r
        except (OSError, AttributeError):
            continue
        crypt_r.restype = ctypes.c_char_p
        crypt_r.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]

        def backend(
            pw: bytes,
            salt_text: str,
            _crypt_r: Callable[..., bytes | None] = crypt_r,
        ) -> str:
            setting = f"$6${salt_text}$".encode("ascii")
            data = ctypes.create_string_buffer(_CRYPT_DATA_SIZE)
            out = _crypt_r(pw, setting, data)
            # glibc 失敗回 NULL，libxcrypt 回 "*0"／"*1" 失敗標記
            if out is None or not out.startswith(setting):
                raise RuntimeError(f"libcrypt crypt_r failed for setting {setting!r}")
            return out.decode("ascii")

        try:
            probe = backend(_REFERENCE_PASSWORD, _REFERENCE_SALT)
        except RuntimeError:
            logger.info("libcrypt %s does not support SHA-512 crypt", name)
            continue
        if probe != _REFERENCE_HASH:
            logger.warning(
                "libcrypt %s produced %r for the SHA-crypt reference vector",
                name,
                probe,
            )
            continue
        return backend
    return None


sha512_crypt_libcrypt: Backend | None = _load_libcrypt()
"""系統 libcrypt 的 ``crypt_r``；這個平台沒有可用的就是 ``None``。"""

ACTIVE_BACKEND_NAME = "libcrypt" if sha512_crypt_libcrypt else "hashlib"
_active: Backend = sha512_crypt_libcrypt or sha512_crypt_hashlib
logger.info("SHA-512 crypt backend: %s", ACTIVE_BACKEND_NAME)


def sha512_crypt(password: str, salt: str | None = None) -> str:
    """回傳 ``$6$<salt>$<hash>``；``salt`` 未給時隨機產生 16 個字元。"""
    salt_text = generate_salt() if salt is None else salt[:_SALT_LENGTH]
    if not salt_text or not _SALT_CHARS.issuperset(salt_text):
        raise ValueError("salt must be 1-16 characters from [./0-9A-Za-z]")
    pw = password.encode("utf-8")
    if b"\x00" in pw:
        raise ValueError("password must not contain NUL")
    return _active(pw, salt_text)
