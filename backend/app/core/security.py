import base64
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

import jwt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

password_hash = PasswordHash(
    (
        Argon2Hasher(),
        BcryptHasher(),
    )
)


_FERNET_SALT = b"SkyLab-fernet-v1"
_FERNET_ITERATIONS = 480_000


def derive_fernet(secret_key: str) -> Fernet:
    """Derive the at-rest Fernet key from a SECRET_KEY using PBKDF2.

    scripts/rotate_secret_key.py derives its old and new keys through this
    too, so the salt and iteration count live only here.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        iterations=_FERNET_ITERATIONS,
    )
    return Fernet(base64.urlsafe_b64encode(kdf.derive(secret_key.encode())))


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """The Fernet derived from the configured SECRET_KEY (cached)."""
    return derive_fernet(settings.SECRET_KEY)


def encrypt_value(plain_text: str) -> str:
    """Encrypt a string value using Fernet symmetric encryption."""
    return _get_fernet().encrypt(plain_text.encode()).decode()


def decrypt_value(encrypted_text: str) -> str:
    """Decrypt a Fernet-encrypted string value."""
    return _get_fernet().decrypt(encrypted_text.encode()).decode()


ALGORITHM = "HS256"


def _create_token(
    subject: str | Any,
    expires_delta: timedelta,
    token_version: int,
    token_type: str,
) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": token_type,
        "ver": token_version,
        "jti": uuid4().hex,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(
    subject: str | Any,
    expires_delta: timedelta,
    token_version: int = 0,
) -> str:
    return _create_token(subject, expires_delta, token_version, "access")


def create_refresh_token(
    subject: str | Any,
    expires_delta: timedelta,
    token_version: int = 0,
) -> str:
    return _create_token(subject, expires_delta, token_version, "refresh")


def verify_password(
    plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    return password_hash.verify_and_update(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)
