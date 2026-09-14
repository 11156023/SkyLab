from __future__ import annotations

import pytest

from app.exceptions import BadRequestError
from app.services.network import gateway_service


def test_build_traefik_static_config_uses_dns_challenge() -> None:
    config = gateway_service.build_traefik_static_config(
        acme_email="ops@example.com"
    )

    assert "dnsChallenge:" in config
    assert "provider: cloudflare" in config
    assert "httpChallenge" not in config
    assert 'address: "127.0.0.1:8080"' in config
    assert "dashboard: true" in config


def test_build_traefik_env_file_rejects_multiline_token() -> None:
    with pytest.raises(BadRequestError, match="Cloudflare API Token 格式不正確"):
        gateway_service.build_traefik_env_file("line1\nline2")


def test_build_traefik_env_file_quotes_cloudflare_token() -> None:
    env_file = gateway_service.build_traefik_env_file('cf-token"with$chars')

    assert 'CF_DNS_API_TOKEN="cf-token\\"with\\$chars"' in env_file


def test_build_traefik_systemd_unit_loads_environment_file() -> None:
    unit = gateway_service.build_traefik_systemd_unit()

    assert f"EnvironmentFile=-{gateway_service.TRAEFIK_ENV_PATH}" in unit
    assert "ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml" in unit


def test_parse_detected_service_versions() -> None:
    install_targets = {
        "traefik": "3.3.4",
        "frps": "0.62.0",
        "frpc": "0.62.0",
    }

    traefik_info = gateway_service._build_service_version_info(
        service="traefik",
        version_output="Version:      3.3.4\nCodename:     ramequin",
        install_targets=install_targets,
        candidate_version=None,
    )
    haproxy_info = gateway_service._build_service_version_info(
        service="haproxy",
        version_output="HAProxy version 2.6.12-1+deb12u2 2024/10/01 - https://haproxy.org/",
        install_targets=install_targets,
        candidate_version="2.8.10-1~deb12u1",
    )

    assert traefik_info.current_version == "3.3.4"
    assert traefik_info.target_version == "3.3.4"
    assert traefik_info.update_available is False
    assert haproxy_info.current_version == "2.6.12-1+deb12u2"
    assert haproxy_info.target_version == "2.8.10-1~deb12u1"
    assert haproxy_info.update_available is True


def test_wireguard_uses_wg_quick_systemd_unit_without_exposing_raw_config() -> None:
    assert gateway_service._systemd_unit("wireguard") == (
        f"wg-quick@{gateway_service.settings.WIREGUARD_INTERFACE}"
    )
    assert "wireguard" not in gateway_service.SERVICE_CONFIG_PATHS


def test_parse_wireguard_dump_returns_aggregate_metrics_without_keys() -> None:
    now = 1_700_000_000
    dump = "\n".join(
        [
            "SERVER_PRIVATE\tSERVER_PUBLIC\t51821\toff",
            (
                "PEER_ONE\tPRESHARED_ONE\t198.51.100.1:51820\t10.210.0.2/32"
                f"\t{now - 100}\t1024\t2048\t25"
            ),
            (
                "PEER_TWO\tPRESHARED_TWO\t198.51.100.2:51820\t10.210.0.3/32"
                f"\t{now - 600}\t4096\t8192\t25"
            ),
        ]
    )

    metrics = gateway_service._parse_wireguard_dump(dump, now_timestamp=now)

    assert metrics == {
        "listen_port": 51821,
        "live_peers": 2,
        "recent_handshakes": 1,
        "transfer_rx_bytes": 5120,
        "transfer_tx_bytes": 10240,
        "inspection_available": True,
    }
    rendered = repr(metrics)
    assert "SERVER_PRIVATE" not in rendered
    assert "PEER_ONE" not in rendered
    assert "PRESHARED_ONE" not in rendered


def test_parse_wireguard_dump_marks_unavailable_runtime() -> None:
    assert gateway_service._parse_wireguard_dump("") == {
        "listen_port": None,
        "live_peers": 0,
        "recent_handshakes": 0,
        "transfer_rx_bytes": 0,
        "transfer_tx_bytes": 0,
        "inspection_available": False,
    }
