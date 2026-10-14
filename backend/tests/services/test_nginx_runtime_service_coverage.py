"""Coverage tests for app.services.network.nginx_runtime_service."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.services.network import gateway_service, nginx_runtime_service
from app.services.network import nginx_gateway_service as nginx


def test_runtime_snapshot_maps_collected_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object()
    sessions: list[object] = []

    @contextmanager
    def fake_client(session: object):
        sessions.append(session)
        yield client

    expires = datetime(2030, 1, 1, tzinfo=UTC)

    def fake_collect(c: object) -> dict:
        assert c is client
        return {
            "version": "1.26.3",
            "active": True,
            "config_valid": True,
            "http_servers": [
                {
                    "name": "cc-101-web-example-edu",
                    "vmid": 101,
                    "domain": "web.example.edu",
                    "upstream": "10.0.0.5:80",
                    "https": True,
                    "certificate": "/etc/ssl/a.pem",
                    "certificate_ready": True,
                }
            ],
            "stream_servers": [
                {
                    "name": "cc-101-30022-tcp",
                    "vmid": 101,
                    "listen": 30022,
                    "protocol": "tcp",
                    "upstream": "10.0.0.5:22",
                }
            ],
            "certificates": [{"name": "/etc/ssl/a.pem", "expires_at": expires}],
        }

    monkeypatch.setattr(gateway_service, "gateway_client", fake_client)
    monkeypatch.setattr(nginx, "collect_runtime", fake_collect)
    session = object()

    snapshot = nginx_runtime_service.get_runtime_snapshot(session=session)

    assert sessions == [session]
    assert snapshot.version == "1.26.3"
    assert snapshot.active is True
    assert snapshot.http_servers[0].domain == "web.example.edu"
    assert snapshot.stream_servers[0].listen == 30022
    assert snapshot.certificates[0].expires_at == expires
