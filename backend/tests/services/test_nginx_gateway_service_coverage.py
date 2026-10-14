"""Coverage tests for app.services.network.nginx_gateway_service (SSH readers)."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.network import nginx_gateway_service as nginx


@dataclass
class _NatRule:
    vmid: int
    external_port: int
    internal_port: int
    protocol: str
    vm_ip: str


def test_parse_certificate_inspection_skips_lines_without_values() -> None:
    result = nginx.parse_certificate_inspection(
        "openssl noise\ncert_readable=1\ncert_valid=1\nkey_valid=1\nkey_match=1\n"
        "cert_end=Jan  1 00:00:00 2030 GMT\nsan=Example.EDU\n"
    )
    assert result.cert_readable is True
    assert result.key_matches is True
    assert result.dns_names == ("example.edu",)
    assert result.expires_at is not None and result.expires_at.year == 2030


def test_parse_certificate_listing_skips_blank_lines() -> None:
    items = nginx.parse_certificate_listing(
        "\n/etc/ssl/a.pem\tJan  1 00:00:00 2030 GMT\n   \n"
    )
    assert [item["name"] for item in items] == ["/etc/ssl/a.pem"]


def test_list_certificates_runs_listing_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    def fake_exec(client: Any, command: str, *, timeout: int | None = None):
        commands.append(command)
        return 0, "/etc/ssl/b.pem\tFeb  2 00:00:00 2031 GMT\n", ""

    monkeypatch.setattr(nginx, "_exec", fake_exec)

    items = nginx.list_certificates(object())

    assert items[0]["name"] == "/etc/ssl/b.pem"
    assert commands == [nginx._CERT_LISTING_COMMAND]


def test_collect_runtime_reads_everything_over_one_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_conf = nginx.build_stream_config(
        [
            _NatRule(
                vmid=101,
                external_port=30022,
                internal_port=22,
                protocol="tcp",
                vm_ip="10.0.0.5",
            )
        ]
    )

    def fake_exec(client: Any, command: str, *, timeout: int | None = None):
        if command.startswith("nginx -v"):
            return 0, "", "nginx version: nginx/1.26.3\n"
        if command.startswith("systemctl is-active"):
            return 0, "active\n", ""
        if command.startswith("nginx -t"):
            return 0, "", ""
        if command.startswith("cat ") and nginx.NGINX_STREAM_CONF_PATH in command:
            return 0, stream_conf, ""
        if command.startswith("cat "):
            return 0, "", ""
        return 0, "/etc/ssl/c.pem\tMar  3 00:00:00 2032 GMT\n", ""

    monkeypatch.setattr(nginx, "_exec", fake_exec)

    runtime = nginx.collect_runtime(object())

    assert runtime["version"] == "1.26.3"
    assert runtime["active"] is True
    assert runtime["config_valid"] is True
    assert runtime["http_servers"] == []
    assert runtime["stream_servers"][0]["vmid"] == 101
    assert runtime["stream_servers"][0]["upstream"] == "10.0.0.5:22"
    assert runtime["certificates"][0]["name"] == "/etc/ssl/c.pem"


def test_lock_config_writes_only_locks_on_postgresql() -> None:
    executed: list[Any] = []

    class _Session:
        def __init__(self, dialect: str | None) -> None:
            self.dialect = dialect

        def get_bind(self):
            if self.dialect is None:
                return None
            return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

        def execute(self, statement: Any, params: dict) -> None:
            executed.append(params)

    nginx.lock_config_writes(object())
    nginx.lock_config_writes(_Session(None))
    nginx.lock_config_writes(_Session("sqlite"))
    assert executed == []

    nginx.lock_config_writes(_Session("postgresql"))
    assert executed == [{"lock_id": nginx._NGINX_CONFIG_LOCK_ID}]


def test_probe_health_parses_status_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[int | None] = []

    def fake_exec(client: Any, command: str, *, timeout: int | None = None):
        timeouts.append(timeout)
        return (
            0,
            "nginx=active\nwireguard=\nconfig=ok\nnoise\n"
            "cert=/etc/ssl/a.pem\tJan  1 00:00:00 2030 GMT\n",
            "",
        )

    monkeypatch.setattr(nginx, "_exec", fake_exec)

    health = nginx.probe_health(object(), wireguard_unit="wg-quick@wg0", timeout=7)

    assert timeouts == [7]
    assert health["nginx"] == "active"
    assert health["wireguard"] == "unknown"
    assert health["config_valid"] is True
    assert health["certificates"][0]["name"] == "/etc/ssl/a.pem"
