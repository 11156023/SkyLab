"""Coverage tests for app.utils.hostname, app.utils.token and app.utils.email."""

import encodings.punycode
import logging

import jwt
import pytest

from app.core import security
from app.core.config import settings
from app.utils import email as email_utils
from app.utils import hostname as hostname_utils
from app.utils import token as token_utils

# ---------------------------------------------------------------------------
# hostname
# ---------------------------------------------------------------------------


def _boom_punycode(_text):
    raise UnicodeError("synthetic punycode failure")


def test_validate_unicode_hostname_rejects_long_ace_label() -> None:
    with pytest.raises(ValueError, match="exceeds 63 characters"):
        hostname_utils.validate_unicode_hostname("a" * 64)


def test_validate_unicode_hostname_rejects_long_unicode_label() -> None:
    with pytest.raises(ValueError, match="exceeds 63 characters"):
        hostname_utils.validate_unicode_hostname(
            "".join(chr(0x4E00 + i * 97) for i in range(40))
        )


def test_validate_unicode_hostname_wraps_punycode_error(monkeypatch) -> None:
    monkeypatch.setattr(encodings.punycode, "punycode_encode", _boom_punycode)
    with pytest.raises(ValueError, match="cannot be encoded as valid Punycode"):
        hostname_utils.validate_unicode_hostname("測試")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "cannot be empty"),
        ("-abc", "start or end with a hyphen"),
        ("ab_c", "Only Unicode letters"),
    ],
)
def test_validate_unicode_hostname_rejects_basic_errors(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        hostname_utils.validate_unicode_hostname(value)


def test_validate_unicode_hostname_accepts_valid() -> None:
    assert hostname_utils.validate_unicode_hostname("lab-測試1") == "lab-測試1"


def test_to_punycode_hostname_rejects_non_str(caplog) -> None:
    with caplog.at_level(logging.ERROR, logger=hostname_utils.logger.name):
        with pytest.raises(TypeError, match="hostname must be str"):
            hostname_utils.to_punycode_hostname(123)  # type: ignore[arg-type]
    assert "Expected str for hostname" in caplog.text


def test_to_punycode_hostname_rejects_empty_label() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hostname_utils.to_punycode_hostname("a..b")


def test_to_punycode_hostname_wraps_encode_error(monkeypatch) -> None:
    monkeypatch.setattr(encodings.punycode, "punycode_encode", _boom_punycode)
    with pytest.raises(ValueError, match="Cannot encode hostname label"):
        hostname_utils.to_punycode_hostname("測試.example")


def test_to_punycode_hostname_rejects_long_label() -> None:
    with pytest.raises(ValueError, match="exceeds 63 characters"):
        hostname_utils.to_punycode_hostname("a" * 64)


def test_to_punycode_hostname_rejects_long_total() -> None:
    hostname = ".".join(["a" * 60] * 5)
    with pytest.raises(ValueError, match="exceeds 253 characters"):
        hostname_utils.to_punycode_hostname(hostname)


def test_to_and_from_punycode_round_trip() -> None:
    encoded = hostname_utils.to_punycode_hostname("測試.example")
    assert encoded.startswith("xn--")
    assert hostname_utils.from_punycode_hostname(encoded) == "測試.example"


def test_from_punycode_hostname_keeps_undecodable_label() -> None:
    assert hostname_utils.from_punycode_hostname("xn--測.ok") == "xn--測.ok"


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------


def _encode(payload: dict) -> str:
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=security.ALGORITHM)


def test_password_reset_token_round_trip() -> None:
    token = token_utils.generate_password_reset_token("a@example.com", token_version=3)
    assert token_utils.decode_password_reset_token(token) == ("a@example.com", 3)


def test_decode_password_reset_token_rejects_invalid() -> None:
    assert token_utils.decode_password_reset_token("not-a-token") is None


def test_decode_password_reset_token_rejects_wrong_type() -> None:
    assert (
        token_utils.decode_password_reset_token(
            _encode({"sub": "a@example.com", "type": "access"})
        )
        is None
    )


def test_decode_password_reset_token_rejects_bad_version() -> None:
    assert (
        token_utils.decode_password_reset_token(
            _encode({"sub": "a@example.com", "type": "reset", "ver": "abc"})
        )
        is None
    )


# ---------------------------------------------------------------------------
# email
# ---------------------------------------------------------------------------


class _FakeMessage:
    instances: list["_FakeMessage"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.sent: list[dict] = []
        _FakeMessage.instances.append(self)

    def send(self, *, to, smtp):
        self.sent.append({"to": to, "smtp": smtp})
        return "250 OK"


@pytest.fixture
def fake_message(monkeypatch):
    _FakeMessage.instances = []
    monkeypatch.setattr(email_utils, "Message", _FakeMessage)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 2525)
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", "noreply@example.com")
    monkeypatch.setattr(settings, "EMAILS_FROM_NAME", "SkyLab")
    return _FakeMessage


def test_send_email_with_tls_and_credentials(monkeypatch, fake_message) -> None:
    monkeypatch.setattr(settings, "SMTP_TLS", True)
    monkeypatch.setattr(settings, "SMTP_SSL", False)
    monkeypatch.setattr(settings, "SMTP_USER", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")

    email_utils.send_email(email_to="to@example.com", subject="Hi", html_content="<p/>")

    [message] = fake_message.instances
    assert message.kwargs == {
        "subject": "Hi",
        "html": "<p/>",
        "mail_from": ("SkyLab", "noreply@example.com"),
    }
    assert message.sent == [
        {
            "to": "to@example.com",
            "smtp": {
                "host": "smtp.example.com",
                "port": 2525,
                "fail_silently": False,
                "tls": True,
                "user": "user",
                "password": "secret",
            },
        }
    ]


def test_send_email_with_ssl_without_credentials(monkeypatch, fake_message) -> None:
    monkeypatch.setattr(settings, "SMTP_TLS", False)
    monkeypatch.setattr(settings, "SMTP_SSL", True)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)

    email_utils.send_email(email_to="to@example.com")

    [message] = fake_message.instances
    assert message.sent[0]["smtp"] == {
        "host": "smtp.example.com",
        "port": 2525,
        "fail_silently": False,
        "ssl": True,
    }


def test_send_email_plain_smtp(monkeypatch, fake_message) -> None:
    monkeypatch.setattr(settings, "SMTP_TLS", False)
    monkeypatch.setattr(settings, "SMTP_SSL", False)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)

    email_utils.send_email(email_to="to@example.com")

    assert "tls" not in fake_message.instances[0].sent[0]["smtp"]
    assert "ssl" not in fake_message.instances[0].sent[0]["smtp"]


def test_send_email_requires_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    monkeypatch.setattr(settings, "EMAILS_FROM_EMAIL", None)
    with pytest.raises(AssertionError, match="no provided configuration"):
        email_utils.send_email(email_to="to@example.com")


def test_generate_emails_render_templates() -> None:
    reset = email_utils.generate_reset_password_email(
        "to@example.com", "user@example.com", "tok123"
    )
    assert "Password recovery" in reset.subject
    assert "tok123" in reset.html_content

    new_account = email_utils.generate_new_account_email(
        "to@example.com", "alice", "pw"
    )
    assert "New account for user alice" in new_account.subject
