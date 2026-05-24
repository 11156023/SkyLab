"""Tests for TOTP primitives.

Skips entirely if ``pyotp`` is not installed.
"""

from __future__ import annotations

import pytest

pyotp = pytest.importorskip("pyotp")

from app.core import totp  # noqa: E402


def test_generate_secret_is_base32_and_unique() -> None:
    a = totp.generate_secret()
    b = totp.generate_secret()
    assert a != b
    # base32 alphabet
    assert set(a) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=")


def test_provisioning_uri_contains_account_and_issuer() -> None:
    uri = totp.provisioning_uri(
        secret=totp.generate_secret(),
        account_name="alice@example.com",
        issuer="SkyLab",
    )
    assert uri.startswith("otpauth://totp/")
    assert "SkyLab" in uri
    assert "alice%40example.com" in uri or "alice@example.com" in uri


def test_verify_code_accepts_current_otp() -> None:
    secret = totp.generate_secret()
    code = pyotp.TOTP(secret).now()
    assert totp.verify_code(secret=secret, code=code) is True


def test_verify_code_rejects_wrong_code() -> None:
    secret = totp.generate_secret()
    assert totp.verify_code(secret=secret, code="000000") is False


def test_verify_code_rejects_invalid_format() -> None:
    secret = totp.generate_secret()
    assert totp.verify_code(secret=secret, code="abcdef") is False
    assert totp.verify_code(secret=secret, code="12345") is False
    assert totp.verify_code(secret=secret, code="") is False


def test_recovery_codes_unique_and_correct_length() -> None:
    codes = totp.generate_recovery_codes(count=10, length=10)
    assert len(codes) == 10
    assert len(set(codes)) == 10
    for c in codes:
        assert len(c) == 10
