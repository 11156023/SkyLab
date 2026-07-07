"""VNC challenge-response 認證用的 DES。

VNC 的怪癖：密碼（ticket）取前 8 bytes（不足補 0），且每個 key byte
先做位元反轉，再用單 DES ECB 加密 16-byte challenge。
cryptography 套件沒有單 DES，改用 TripleDES 且 K1=K2=K3（數學上等價單 DES）。
"""

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

_CHALLENGE_LENGTH = 16


def _reverse_bits(byte: int) -> int:
    result = 0
    for i in range(8):
        result |= ((byte >> i) & 1) << (7 - i)
    return result


def vnc_auth_response(password: str, challenge: bytes) -> bytes:
    """對 16-byte challenge 計算 16-byte VNC auth response。"""
    if len(challenge) != _CHALLENGE_LENGTH:
        raise ValueError(f"VNC challenge must be {_CHALLENGE_LENGTH} bytes")
    raw_key = password.encode("latin-1")[:8].ljust(8, b"\x00")
    key = bytes(_reverse_bits(b) for b in raw_key)
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()
