"""Tests for app.exceptions — pin status codes & default messages."""

from __future__ import annotations

import pytest

from app.exceptions import (
    AppError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    GatewayTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    ProvisioningError,
    ProxmoxError,
    UpstreamServiceError,
)


def test_app_error_default_status_500_and_message_preserved() -> None:
    err = AppError("boom")
    assert err.message == "boom"
    assert err.status_code == 500
    assert str(err) == "boom"


def test_app_error_custom_status_code() -> None:
    err = AppError("teapot", status_code=418)
    assert err.status_code == 418


def test_app_error_is_subclass_of_exception() -> None:
    assert issubclass(AppError, Exception)


@pytest.mark.parametrize(
    ("cls", "expected_status", "expected_default"),
    [
        (NotFoundError, 404, "Not found"),
        (AuthenticationError, 401, "Could not validate credentials"),
        (PermissionDeniedError, 403, "Permission denied"),
        (ConflictError, 409, "Conflict"),
        (BadRequestError, 400, "Bad request"),
        (ProvisioningError, 500, "Provisioning failed"),
        (ProxmoxError, 502, "Proxmox operation failed"),
        (UpstreamServiceError, 502, "Upstream service failed"),
        (GatewayTimeoutError, 504, "Upstream service timed out"),
    ],
)
def test_subclass_default_status_and_message(
    cls: type[AppError], expected_status: int, expected_default: str
) -> None:
    err = cls()
    assert err.status_code == expected_status
    assert err.message == expected_default


def test_subclass_accepts_custom_message() -> None:
    err = NotFoundError("user 42 not found")
    assert err.status_code == 404
    assert err.message == "user 42 not found"


def test_all_subclasses_inherit_app_error() -> None:
    for cls in (
        NotFoundError,
        AuthenticationError,
        PermissionDeniedError,
        ConflictError,
        BadRequestError,
        ProvisioningError,
        ProxmoxError,
        UpstreamServiceError,
        GatewayTimeoutError,
    ):
        assert issubclass(cls, AppError)


def _raise(exc: AppError) -> None:
    raise exc


def test_can_be_raised_and_caught() -> None:
    with pytest.raises(NotFoundError) as exc_info:
        _raise(NotFoundError("missing"))
    assert exc_info.value.status_code == 404


def test_can_be_caught_as_app_error_base() -> None:
    """Generic handlers should be able to catch all subclasses via AppError."""
    with pytest.raises(AppError) as exc_info:
        _raise(ConflictError("dup key"))
    assert exc_info.value.status_code == 409
