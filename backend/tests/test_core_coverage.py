"""Coverage for app.core request_context / config / i18n / sentry / logging / db / authorizers / metrics."""

from __future__ import annotations

import asyncio
import json
import logging
import warnings
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core import db as core_db
from app.core import i18n, metrics, request_context, sentry
from app.core.config import Settings, parse_cors, settings
from app.core.logging import ColoredConsoleFormatter, JsonFormatter

_REQUIRED: dict[str, Any] = {
    "PROJECT_NAME": "SkyLab",
    "POSTGRES_SERVER": "db",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "a-real-db-password",
    "FIRST_SUPERUSER": "admin@example.com",
    "FIRST_SUPERUSER_PASSWORD": "a-real-admin-password",
    "ENABLE_SIGNUP": False,
    "SECRET_KEY": "z" * 43,
}


# ---------------------------------------------------------------- request_context


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("ja;q=0.9", "ja"),
        ("en-US,ja;q=0.8", "en"),
        ("ja-JP", "ja"),
        ("zh-CN", "zh-TW"),
        ("fr-FR, de", "zh-TW"),
    ],
)
def test_resolve_language_base_match_and_default(header: str, expected: str) -> None:
    assert request_context.resolve_language(header) == expected


def test_extract_client_ip_skips_undecodable_header() -> None:
    headers: list[Any] = [("x-real-ip", b"1.2.3.4"), (b"x-forwarded-for", b"9.9.9.9")]
    assert request_context._extract_client_ip(headers, "10.0.0.1") == "9.9.9.9"


def test_extract_header_returns_none_on_decode_failure() -> None:
    headers: list[Any] = [(b"user-agent", "not-bytes")]
    assert request_context._extract_header(headers, b"user-agent") is None


def test_tag_sentry_scope_sets_tag_when_active(monkeypatch: pytest.MonkeyPatch) -> None:
    import sentry_sdk

    tags: dict[str, str] = {}
    scope = SimpleNamespace(set_tag=lambda k, v: tags.__setitem__(k, v))
    monkeypatch.setattr(
        sentry_sdk, "get_client", lambda: SimpleNamespace(is_active=lambda: True)
    )
    monkeypatch.setattr(sentry_sdk, "get_isolation_scope", lambda: scope)

    request_context._tag_sentry_scope("req-123")

    assert tags == {"request_id": "req-123"}


def test_tag_sentry_scope_ignores_sentry_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sentry_sdk

    def _boom() -> None:
        raise RuntimeError("sentry broken")

    monkeypatch.setattr(sentry_sdk, "get_client", _boom)
    request_context._tag_sentry_scope("req-123")


# ---------------------------------------------------------------- config


def test_parse_cors_variants() -> None:
    assert parse_cors("http://a, http://b ,") == ["http://a", "http://b"]
    assert parse_cors('["http://a"]') == '["http://a"]'
    assert parse_cors(["http://a"]) == ["http://a"]
    with pytest.raises(ValueError):
        parse_cors(5)


def test_signup_outside_local_requires_real_smtp() -> None:
    with pytest.raises(ValidationError, match="real SMTP_HOST"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            ENVIRONMENT="production",
            TURNSTILE_SITE_KEY="site",
            TURNSTILE_SECRET_KEY="secret",
            SMTP_HOST="mailcatcher",
            EMAILS_FROM_EMAIL="noreply@example.com",
            **{**_REQUIRED, "ENABLE_SIGNUP": True},
        )


def test_default_secret_warns_in_local() -> None:
    with pytest.warns(UserWarning, match="POSTGRES_PASSWORD"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            ENVIRONMENT="local",
            **{**_REQUIRED, "POSTGRES_PASSWORD": "changethis"},
        )


def test_default_secret_rejected_outside_local() -> None:
    with pytest.raises(ValidationError, match="FIRST_SUPERUSER_PASSWORD"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            ENVIRONMENT="staging",
            **{**_REQUIRED, "FIRST_SUPERUSER_PASSWORD": "changethis"},
        )


def test_production_explicit_cors_origins_allowed() -> None:
    configured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        ENVIRONMENT="production",
        BACKEND_CORS_ORIGINS="https://a.example, https://b.example",
        **_REQUIRED,
    )
    assert configured.all_cors_origins[:2] == ["https://a.example", "https://b.example"]


def test_production_wildcard_cors_rejected_by_validator() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        configured = Settings(  # type: ignore[call-arg]
            _env_file=None, ENVIRONMENT="production", **_REQUIRED
        )
    # parse_cors 與 list[AnyUrl] 型別讓 "*" 在建構時就過不了驗證；直接呼叫
    # validator 確認萬一有值溜進來仍會被擋。
    object.__setattr__(configured, "BACKEND_CORS_ORIGINS", ["*"])
    with pytest.raises(ValueError, match="Wildcard"):
        configured._validate_cors_origins()
    object.__setattr__(configured, "BACKEND_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="Wildcard"):
        configured._validate_cors_origins()


# ---------------------------------------------------------------- i18n


def test_catalog_missing_language_dir_is_empty() -> None:
    assert i18n._catalog("xx-NOPE") == {}


def test_translate_falls_back_to_key_and_survives_bad_params() -> None:
    assert i18n.translate("no.such.key", "en") == "no.such.key"
    assert i18n.translate("{missing}", "en", other=1) == "{missing}"
    assert i18n.translate("{0}", "ja", other=1) == "{0}"


# ---------------------------------------------------------------- sentry


def test_init_sentry_initializes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(sentry, "_state", {"initialized": False})
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@sentry.example/1")
    monkeypatch.setattr(settings, "SENTRY_RELEASE", "")
    monkeypatch.setattr(
        sentry.sentry_sdk, "init", lambda **kwargs: calls.append(("init", kwargs))
    )
    monkeypatch.setattr(
        sentry.sentry_sdk, "set_tag", lambda k, v: calls.append(("tag", (k, v)))
    )

    assert sentry.init_sentry("worker") is True
    assert sentry.init_sentry("worker") is True

    assert len(calls) == 2
    init_kwargs = calls[0][1]
    assert init_kwargs["dsn"] == "https://key@sentry.example/1"
    assert init_kwargs["release"] is None
    assert init_kwargs["send_default_pii"] is False
    assert calls[1] == ("tag", ("component", "worker"))


# ---------------------------------------------------------------- logging


def _record(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hi",
        args=(),
        exc_info=None,
    )
    record.stack_info = "Stack (most recent call last):\n  frame"
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_skips_payload_keys_and_includes_stack() -> None:
    record = _record(message="shadow", level="shadow", custom={"a": 1})
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hi"
    assert payload["level"] == "INFO"
    assert payload["custom"] == {"a": 1}
    assert payload["stack"].startswith("Stack")


def test_colored_formatter_includes_stack_info() -> None:
    line = ColoredConsoleFormatter().format(_record())
    assert line.endswith("Stack (most recent call last):\n  frame")


# ---------------------------------------------------------------- db


async def test_run_db_in_threadpool_reraises_when_operation_task_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _cancelled(*_args: Any, **_kwargs: Any) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(core_db, "run_in_threadpool", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await core_db.run_db_in_threadpool(lambda: None)


async def test_run_db_in_threadpool_prefers_cancellation_over_late_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_fail(*_args: Any, **_kwargs: Any) -> None:
        started.set()
        await release.wait()
        raise RuntimeError("late failure")

    monkeypatch.setattr(core_db, "run_in_threadpool", _slow_fail)
    outer = asyncio.create_task(core_db.run_db_in_threadpool(lambda: None))
    await started.wait()
    outer.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not outer.done()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.gather(outer)


# ---------------------------------------------------------------- authorizers


# ---------------------------------------------------------------- metrics


async def test_metrics_endpoint_without_prometheus_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metrics, "_AVAILABLE", False)
    request = Request(
        {"type": "http", "method": "GET", "path": "/metrics", "headers": []}
    )

    response = await metrics.metrics_endpoint(request)

    assert response.status_code == 503
    assert response.body == b"prometheus_client not installed"
