from __future__ import annotations

from pathlib import Path

from app.services.network import gateway_service


def test_gateway_installer_uses_wireguard() -> None:
    gateway_dir = Path(__file__).resolve().parents[3] / "gateway"
    script = (gateway_dir / "install.sh").read_text(encoding="utf-8")

    assert "wireguard-tools" in script
    assert "campus-cloud-wg-firewall.service" in script
    assert not (gateway_dir / "install-wireguard.sh").exists()


def test_gateway_installer_installs_nginx_stream_and_certbot() -> None:
    """Gateway 改由 nginx 一手包辦 Port 轉發與反向代理，Traefik 不再下載。"""
    gateway_dir = Path(__file__).resolve().parents[3] / "gateway"
    script = (gateway_dir / "install.sh").read_text(encoding="utf-8")

    assert "libnginx-mod-stream" in script
    assert "python3-certbot-dns-cloudflare" in script
    assert "include /etc/nginx/skylab/http.conf;" in script
    assert "include /etc/nginx/skylab/stream.conf;" in script
    assert "renewal-hooks/deploy/skylab-nginx-reload" in script
    assert "TRAEFIK_VERSION" not in script
    assert "BEGIN_skylab_MANAGED" not in script


def test_gateway_installer_sets_up_exporters_matching_backend_ports() -> None:
    """install.sh 裝的 exporter port 要和後端 http_sd 回報給 Prometheus 的一致。"""
    gateway_dir = Path(__file__).resolve().parents[3] / "gateway"
    script = (gateway_dir / "install.sh").read_text(encoding="utf-8")
    settings = gateway_service.settings

    assert "prometheus-node-exporter prometheus-nginx-exporter" in script
    assert f"NODE_EXPORTER_PORT={settings.GATEWAY_NODE_EXPORTER_PORT}" in script
    assert f"NGINX_EXPORTER_PORT={settings.GATEWAY_NGINX_EXPORTER_PORT}" in script
    assert "include /etc/nginx/skylab/status.conf;" in script
    # stub_status 只能綁本機，exporter 只對 MONITORING_ALLOW_FROM 開放
    assert "listen 127.0.0.1:${NGINX_STATUS_PORT};" in script
    assert 'ufw allow from "$source" to any port "$port" proto tcp' in script


def test_only_nginx_config_is_exposed_for_editing() -> None:
    assert gateway_service.SERVICE_CONFIG_PATHS == {"nginx": "/etc/nginx/nginx.conf"}
    assert gateway_service._systemd_unit("nginx") == "nginx"


def test_parse_detected_service_versions() -> None:
    nginx_info = gateway_service._build_service_version_info(
        service="nginx",
        version_output="nginx version: nginx/1.26.3",
        candidate_version="1.26.3-3+deb13u1",
    )
    wireguard_info = gateway_service._build_service_version_info(
        service="wireguard",
        version_output="wireguard-tools v1.0.20210914 - https://git.zx2c4.com/wireguard-tools/",
        candidate_version=None,
    )

    assert nginx_info.current_version == "1.26.3"
    assert nginx_info.target_version == "1.26.3-3+deb13u1"
    assert nginx_info.update_available is True
    assert nginx_info.source == "apt candidate"
    assert wireguard_info.current_version == "1.0.20210914"
    assert wireguard_info.target_version is None
    assert wireguard_info.update_available is None
    assert wireguard_info.source == "detected only"


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
