"""Tests for app.core.security crypto + password helpers (pure unit).

No DB / no Redis. Exercises:
- Fernet encrypt/decrypt round-trip (singleton key)
- Password hashing: argon2 by default + bcrypt verify+upgrade
- JWT helpers already covered in test_security_jwt.py
"""

from __future__ import annotations

import pytest

from app.core.security import (
    decrypt_value,
    encrypt_value,
    get_password_hash,
    verify_password,
)

# ─── Fernet encrypt / decrypt ────────────────────────────────────────────────


def test_encrypt_decrypt_round_trip_ascii() -> None:
    plain = "the quick brown fox jumps over the lazy dog"
    token = encrypt_value(plain)
    assert token != plain
    assert decrypt_value(token) == plain


def test_encrypt_decrypt_round_trip_unicode() -> None:
    plain = "校園雲端 — SkyLab — 你好 🚀"
    assert decrypt_value(encrypt_value(plain)) == plain


def test_encrypt_decrypt_empty_string() -> None:
    assert decrypt_value(encrypt_value("")) == ""


def test_encrypt_produces_different_tokens_each_call() -> None:
    """Fernet uses random IVs, so two encryptions of the same plaintext differ."""
    a = encrypt_value("same-value")
    b = encrypt_value("same-value")
    assert a != b
    assert decrypt_value(a) == decrypt_value(b) == "same-value"


def test_decrypt_invalid_token_raises() -> None:
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        decrypt_value("not-a-fernet-token")


# ─── Password hashing ────────────────────────────────────────────────────────


def test_get_password_hash_uses_argon2_prefix() -> None:
    """pwdlib defaults to the first hasher (Argon2) for new hashes."""
    digest = get_password_hash("Sup3rSecret!")
    assert digest.startswith("$argon2")


def test_password_hash_is_unique_per_call_due_to_salt() -> None:
    a = get_password_hash("same-pw")
    b = get_password_hash("same-pw")
    assert a != b


def test_verify_password_correct_password_returns_true() -> None:
    digest = get_password_hash("CorrectHorseBatteryStaple")
    ok, updated = verify_password("CorrectHorseBatteryStaple", digest)
    assert ok is True
    # No upgrade needed for argon2 → updated should be None
    assert updated is None


def test_verify_password_wrong_password_returns_false() -> None:
    digest = get_password_hash("right-pw")
    ok, _updated = verify_password("wrong-pw", digest)
    assert ok is False


def test_verify_password_against_legacy_bcrypt_upgrades_to_argon2() -> None:
    """A legacy bcrypt hash should verify and signal upgrade to argon2."""
    from pwdlib.hashers.bcrypt import BcryptHasher

    legacy = BcryptHasher().hash("legacy-pw")
    assert legacy.startswith("$2")  # bcrypt prefix

    ok, upgraded = verify_password("legacy-pw", legacy)
    assert ok is True
    # Upgraded hash should be argon2 — pwdlib returns the new hash so caller can persist it
    assert upgraded is not None
    assert upgraded.startswith("$argon2")
