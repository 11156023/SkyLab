"""Coverage tests for app.services.network.gateway_certificate_service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.repositories import gateway_config as gw_repo
from app.services.network import gateway_certificate_service as svc
from app.services.network import nginx_gateway_service as nginx
from tests.services.test_gateway_certificate_service import _Harness, _update


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


def test_verify_inspection_rejects_invalid_key() -> None:
    inspection = nginx.CertificateInspection(
        cert_readable=True,
        key_readable=True,
        cert_valid=True,
        key_valid=False,
        key_matches=None,
        expires_at=None,
        dns_names=(),
    )
    with pytest.raises(BadRequestError):
        svc.verify_inspection(
            inspection,
            cert_path="/etc/ssl/a.pem",
            key_path="/etc/ssl/a.key",
            now=datetime.now(UTC),
        )


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1


def test_save_rolls_back_session_and_survives_restore_failure(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.sync_error = ProxmoxError("nginx -t failed")
    saves: list[tuple[str, str]] = []

    def save(
        _session: object, *, ssl_certificate_path: str, ssl_certificate_key_path: str
    ):
        saves.append((ssl_certificate_path, ssl_certificate_key_path))
        if len(saves) > 1:
            raise RuntimeError("db gone")
        harness.config.ssl_certificate_path = ssl_certificate_path
        harness.config.ssl_certificate_key_path = ssl_certificate_key_path
        return harness.config

    monkeypatch.setattr(gw_repo, "save_certificate_paths", save)
    session = _Session()

    with pytest.raises(ProxmoxError):
        svc.save_config(session, _update())

    assert session.rollbacks == 1
    assert len(saves) == 2
