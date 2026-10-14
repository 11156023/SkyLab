from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.network import gateway_service


def test_gateway_installer_uses_wireguard() -> None:
    gateway_dir = Path(__file__).resolve().parents[3] / "gateway"
    script = (gateway_dir / "install.sh").read_text(encoding="utf-8")

    assert "wireguard-tools" in script
    assert "skylab-wg-firewall.service" in script
    assert not (gateway_dir / "install-wireguard.sh").exists()


def test_gateway_installer_installs_nginx_stream_without_certbot() -> None:
    """Gateway 由 nginx 一手包辦 Port 轉發與反向代理；憑證由管理員自備，不裝 certbot。"""
    gateway_dir = Path(__file__).resolve().parents[3] / "gateway"
    script = (gateway_dir / "install.sh").read_text(encoding="utf-8")

    assert "libnginx-mod-stream" in script
    assert "include /etc/nginx/skylab/http.conf;" in script
    assert "include /etc/nginx/skylab/stream.conf;" in script
    # 還沒設定憑證時 HTTPS 站台要有自簽憑證可掛
    assert "fallback.crt" in script
    assert "install -d -m 750 /etc/ssl/skylab" in script
    assert "python3-certbot-dns-cloudflare" not in script
    assert "certbot.timer" not in script
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
    # nginx -v 只印上游版本；是否有更新要比 apt 的 Installed 與 Candidate 完整版本
    nginx_info = gateway_service._build_service_version_info(
        service="nginx",
        version_output="nginx version: nginx/1.26.3",
        installed_version="1.26.3-3+deb13u1",
        candidate_version="1.26.3-3+deb13u1",
    )
    wireguard_info = gateway_service._build_service_version_info(
        service="wireguard",
        version_output="wireguard-tools v1.0.20210914 - https://git.zx2c4.com/wireguard-tools/",
        candidate_version=None,
    )

    assert nginx_info.current_version == "1.26.3-3+deb13u1"
    assert nginx_info.target_version == "1.26.3-3+deb13u1"
    assert nginx_info.update_available is False
    assert nginx_info.source == "apt candidate"
    assert wireguard_info.current_version == "1.0.20210914"
    assert wireguard_info.target_version is None
    assert wireguard_info.update_available is None
    assert wireguard_info.source == "detected only"


def test_debian_security_update_counts_as_update() -> None:
    info = gateway_service._build_service_version_info(
        service="nginx",
        version_output="nginx version: nginx/1.26.3",
        installed_version="1.26.3-3+deb13u1",
        candidate_version="1.26.3-3+deb13u2",
    )

    assert info.current_version == "1.26.3-3+deb13u1"
    assert info.target_version == "1.26.3-3+deb13u2"
    assert info.update_available is True


def test_unknown_apt_install_state_does_not_guess_an_update() -> None:
    # 不是 apt 裝的（或查不到）時，不拿 nginx -v 的版本去比 apt 的 Candidate
    info = gateway_service._build_service_version_info(
        service="nginx",
        version_output="nginx version: nginx/1.26.3",
        installed_version=None,
        candidate_version="1.26.3-3+deb13u1",
    )

    assert info.current_version == "1.26.3"
    assert info.target_version == "1.26.3-3+deb13u1"
    assert info.update_available is None


def test_parse_apt_policy_reads_installed_and_candidate() -> None:
    output = "\n".join(
        [
            "nginx:",
            "  Installed: 1.26.3-3+deb13u1",
            "  Candidate: 1.26.3-3+deb13u2",
            "  Version table:",
            "     1.26.3-3+deb13u2 500",
            "        500 http://security.debian.org/debian-security trixie-security/main amd64 Packages",
            " *** 1.26.3-3+deb13u1 100",
            "        100 /var/lib/dpkg/status",
        ]
    )

    assert gateway_service._parse_apt_policy(output) == (
        "1.26.3-3+deb13u1",
        "1.26.3-3+deb13u2",
    )
    assert gateway_service._parse_apt_policy(
        "nginx:\n  Installed: (none)\n  Candidate: 1:1.26.3-3\n"
    ) == (None, "1:1.26.3-3")
    assert gateway_service._parse_apt_policy("") == (None, None)


def test_service_versions_compare_full_apt_versions(monkeypatch) -> None:
    commands: list[str] = []

    def fake_exec(_client, command: str, **_kwargs) -> tuple[int, str, str]:
        commands.append(command)
        if command.startswith("nginx -v"):
            return 0, "nginx version: nginx/1.26.3\n", ""
        if "apt-cache policy" in command:
            return (
                0,
                "nginx:\n  Installed: 1.26.3-3+deb13u1\n  Candidate: 1.26.3-3+deb13u2\n",
                "",
            )
        return 0, "wireguard-tools v1.0.20210914\n", ""

    @contextmanager
    def fake_client(_session):
        yield object()

    monkeypatch.setattr(gateway_service, "gateway_client", fake_client)
    monkeypatch.setattr(gateway_service, "_exec", fake_exec)

    result = gateway_service.get_service_versions(session=object())

    nginx = next(item for item in result.items if item.service == "nginx")
    assert nginx.update_available is True
    assert nginx.current_version == "1.26.3-3+deb13u1"
    assert any(command.startswith("LC_ALL=C apt-cache policy") for command in commands)


# ── Gateway 上的 SSH 指令都有總時限 ─────────────────────────────────────


def test_gateway_commands_default_to_bounded_timeout(monkeypatch) -> None:
    seen: list[float | None] = []

    def fake_exec_command(_client, _command: str, *, timeout=None):
        seen.append(timeout)
        return 0, "ok\n", ""

    monkeypatch.setattr(gateway_service, "exec_command", fake_exec_command)

    gateway_service._exec(object(), "true")
    gateway_service.exec_checked(object(), "true", "failed")

    assert seen == [gateway_service.GATEWAY_EXEC_TIMEOUT_SECONDS] * 2
    # 要容得下 WireGuard 兩層 flock（各 15 秒）再加上腳本本身
    assert gateway_service.GATEWAY_EXEC_TIMEOUT_SECONDS >= 60


@pytest.mark.parametrize("action", ["restart", "start", "stop", "reload"])
def test_service_control_allows_slow_systemd_units(monkeypatch, action: str) -> None:
    seen: list[tuple[str, float | None]] = []

    def fake_exec_command(_client, command: str, *, timeout=None):
        seen.append((command, timeout))
        return 0, "active\n", ""

    class _Client:
        def close(self) -> None:
            return None

    config = SimpleNamespace(host="gw", ssh_port=22, ssh_user="root")
    monkeypatch.setattr(gateway_service, "exec_command", fake_exec_command)
    monkeypatch.setattr(
        gateway_service, "_get_credentials", lambda _session: (config, "key")
    )
    monkeypatch.setattr(gateway_service, "make_client", lambda *a, **k: _Client())

    ok, _ = gateway_service.control_service(object(), "nginx", action)

    assert ok is True
    # restart 是一條「stop; sleep; kill; start」指令，其餘動作就是 systemctl <action>
    marker = "systemctl stop" if action == "restart" else f"systemctl {action}"
    control = [timeout for command, timeout in seen if marker in command]
    assert control == [gateway_service.GATEWAY_SERVICE_CONTROL_TIMEOUT_SECONDS]
    # systemd 預設啟停各最多等 90 秒，restart 先停再啟
    assert gateway_service.GATEWAY_SERVICE_CONTROL_TIMEOUT_SECONDS > 2 * 90


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
