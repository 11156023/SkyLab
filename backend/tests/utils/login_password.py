"""登入密碼雜湊的測試 helper。"""

from app.utils.login_password import is_login_password_hash
from app.utils.sha512_crypt import sha512_crypt


def hash_matches(password: str, crypt_hash: str | None) -> bool:
    """``crypt_hash`` 是不是 ``password`` 的 SHA-512 crypt 雜湊（用它自己的 salt 重算）。"""
    if not is_login_password_hash(crypt_hash):
        return False
    assert crypt_hash is not None
    salt = crypt_hash.split("$")[2]
    return sha512_crypt(password, salt) == crypt_hash
