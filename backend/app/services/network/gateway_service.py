"""Gateway VM 管理服務."""

from __future__ import annotations

import logging
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import BadRequestError, ProxmoxError
from app.infrastructure.ssh import (
    SSHAuthenticationError,
    create_key_client,
    exec_command,
    forget_host_key,
)
from app.infrastructure.ssh import (
    generate_ed25519_keypair as _generate_ed25519_keypair,
)
from app.schemas.gateway import (
    GatewayServiceVersionInfo,
    GatewayServiceVersionsResult,
    GatewayWireGuardOverview,
)

logger = logging.getLogger(__name__)

SERVICE_CONFIG_PATHS: dict[str, str] = {
    "haproxy": "/etc/haproxy/haproxy.cfg",
    "traefik": "/etc/traefik/traefik.yml",
    "frps": "/etc/frp/frps.toml",
    "frpc": "/etc/frp/frpc.toml",
}
SERVICE_SYSTEMD_UNITS: dict[str, str] = {
    "haproxy": "haproxy",
    "traefik": "traefik",
    "frps": "frps",
    "frpc": "frpc",
    "wireguard": f"wg-quick@{settings.WIREGUARD_INTERFACE}",
}

TRAEFIK_DYNAMIC_PATH = "/etc/traefik/dynamic/SkyLab.yml"
TRAEFIK_ENV_PATH = "/etc/traefik/env/SkyLab.env"
TRAEFIK_SYSTEMD_PATH = "/etc/systemd/system/traefik.service"
_GENERIC_VERSION_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)+(?:[-+~][^\s]+)?)")
_HAPROXY_VERSION_PATTERN = re.compile(r"HAProxy version\s+([^\s]+)", re.IGNORECASE)
_TRAEFIK_VERSION_PATTERN = re.compile(r"^Version:\s*([^\s]+)", re.MULTILINE)
_SERVICE_VERSION_COMMANDS: dict[str, str] = {
    "haproxy": "haproxy -v 2>/dev/null | head -1",
    "traefik": "/usr/local/bin/traefik version 2>/dev/null",
    "wireguard": "wg --version 2>&1 | head -1",
}


def _systemd_unit(service: str) -> str:
    unit = SERVICE_SYSTEMD_UNITS.get(service)
    if unit is None:
        raise BadRequestError(t("gateway.unknownService", service=service))
    return unit


def generate_ed25519_keypair() -> tuple[str, str]:
    return _generate_ed25519_keypair()


def reset_host_key(session: object) -> str:
    """清除 Gateway VM 的 pinned SSH host key，回傳被清除的 host。

    Gateway VM 重灌或更換機器後 host key 會改變，導致 TOFU 釘選拒絕連線；
    管理員確認變更為預期後呼叫此函式，下次連線會重新記錄新的 host key。
    """
    from app.repositories import gateway_config as gw_repo  # noqa: PLC0415

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host:
        raise BadRequestError(t("gateway.gatewayIpNotConfigured"))
    forget_host_key(config.host)
    return config.host


def _make_client(host: str, ssh_port: int, ssh_user: str, private_key_pem: str):
    return create_key_client(
        host,
        ssh_port,
        ssh_user,
        private_key_pem,
        timeout=10,
    )


def _exec(client, command: str) -> tuple[int, str, str]:
    return exec_command(client, command)


def _exec_checked(client, command: str, error_message: str) -> str:
    code, out, err = _exec(client, command)
    if code != 0:
        detail = (err or out).strip() or t("gateway.noOutput")
        raise ProxmoxError(t("gateway.commandFailedWithDetail", message=error_message, detail=detail))
    return out


def _get_config(session: object) -> object:
    from app.repositories import gateway_config as gw_repo  # noqa: PLC0415

    config = gw_repo.get_gateway_config(session)  # type: ignore[arg-type]
    if config is None or not config.host or not config.encrypted_private_key:
        raise BadRequestError(t("gateway.gatewayNotConfigured"))
    return config


def _get_traefik_acme_email() -> str:
        return str(settings.EMAILS_FROM_EMAIL or settings.FIRST_SUPERUSER)


def build_traefik_static_config(*, acme_email: str) -> str:
        clean_email = acme_email.strip()
        if not clean_email:
                raise BadRequestError(t("gateway.traefikAcmeEmailRequired"))

        return dedent(
                f"""\
                # Traefik 靜態設定
                # 此檔案由 SkyLab 自動維護，請勿手動修改

                entryPoints:
                    web:
                        address: ":80"
                        http:
                            redirections:
                                entryPoint:
                                    to: websecure
                                    scheme: https
                    websecure:
                        address: ":443"
                    traefik:
                        address: "127.0.0.1:8080"

                api:
                    dashboard: true
                    insecure: true

                providers:
                    file:
                        directory: /etc/traefik/dynamic
                        watch: true

                certificatesResolvers:
                    letsencrypt:
                        acme:
                            email: "{clean_email}"
                            storage: /etc/traefik/acme.json
                            dnsChallenge:
                                provider: cloudflare
                                resolvers:
                                    - "1.1.1.1:53"
                                    - "8.8.8.8:53"

                log:
                    level: INFO

                accessLog: {{}}
                """
        )


def build_traefik_env_file(cloudflare_api_token: str) -> str:
        clean_token = cloudflare_api_token.strip()
        if not clean_token or "\n" in clean_token or "\r" in clean_token:
                raise BadRequestError(t("gateway.cloudflareApiTokenInvalidFormat"))

        escaped_token = (
                clean_token.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("$", "\\$")
        )
        return dedent(
                f"""\
                # SkyLab 自動管理，供 Traefik dnsChallenge 使用
                CF_DNS_API_TOKEN="{escaped_token}"
                """
        )


def build_traefik_systemd_unit() -> str:
        return dedent(
                f"""\
                [Unit]
                Description=Traefik Reverse Proxy
                Documentation=https://doc.traefik.io/traefik/
                After=network-online.target
                Wants=network-online.target

                [Service]
                Type=simple
                User=root
                EnvironmentFile=-{TRAEFIK_ENV_PATH}
                ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/traefik.yml
                Restart=always
                RestartSec=5
                LimitNOFILE=1048576

                [Install]
                WantedBy=multi-user.target
                """
        )


def _write_remote_file(client, path: str, content: str) -> None:
        tmp_path = path + ".tmp"
        sftp = client.open_sftp()
        try:
                with sftp.open(tmp_path, "wb") as handle:
                        handle.write(content.encode("utf-8"))
        finally:
                sftp.close()

        _exec_checked(client, f"mv {tmp_path} {path}", t("gateway.writeRemoteFileFailed", path=path))


def test_connection(
    host: str,
    ssh_port: int,
    ssh_user: str,
    private_key_pem: str,
) -> tuple[bool, str]:
    client = None
    try:
        client = _make_client(host, ssh_port, ssh_user, private_key_pem)
        _, out, _ = _exec(client, "echo ok")
        if out.strip() == "ok":
            return True, "連線成功"
        return False, f"指令回應異常：{out}"
    except SSHAuthenticationError:
        return False, "SSH 認證失敗，請確認公鑰已加入 Gateway VM 的 authorized_keys"
    except Exception as exc:
        return False, f"連線失敗：{exc}"
    finally:
        if client is not None:
            client.close()


def read_service_config(session: object, service: str) -> str:
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    path = SERVICE_CONFIG_PATHS.get(service)
    if path is None:
        raise BadRequestError(t("gateway.unknownService", service=service))

    client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
    try:
        sftp = client.open_sftp()
        try:
            try:
                with sftp.open(path, "r") as handle:
                    return handle.read().decode()
            except FileNotFoundError:
                return ""
        finally:
            sftp.close()
    except Exception as exc:
        raise ProxmoxError(t("gateway.readServiceConfigFailed", service=service, error=exc))
    finally:
        client.close()


def write_service_config(session: object, service: str, content: str) -> None:
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    path = SERVICE_CONFIG_PATHS.get(service)
    if path is None:
        raise BadRequestError(t("gateway.unknownService", service=service))

    client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
    try:
        _write_remote_file(client, path, content)
    except ProxmoxError:
        raise
    except Exception as exc:
        raise ProxmoxError(t("gateway.writeServiceConfigFailed", service=service, error=exc))
    finally:
        client.close()


def sync_traefik_dns_challenge(session: object) -> None:
    from app.repositories import cloudflare_config as cf_repo  # noqa: PLC0415
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    gateway_config = _get_config(session)
    cloudflare_config = cf_repo.get_cloudflare_config(session)  # type: ignore[arg-type]
    if cloudflare_config is None or not cloudflare_config.encrypted_api_token:
        raise BadRequestError(t("gateway.cloudflareApiTokenNotConfigured"))

    private_key_pem = get_decrypted_private_key(gateway_config)  # type: ignore[arg-type]
    client = _make_client(
        gateway_config.host,
        gateway_config.ssh_port,
        gateway_config.ssh_user,
        private_key_pem,
    )

    try:
        _exec_checked(
            client,
            "mkdir -p /etc/traefik/dynamic /etc/traefik/env && "
            "touch /etc/traefik/acme.json && chmod 600 /etc/traefik/acme.json",
            t("gateway.initTraefikDirFailed"),
        )

        _write_remote_file(
            client,
            TRAEFIK_ENV_PATH,
            build_traefik_env_file(
                cf_repo.get_decrypted_api_token(cloudflare_config)
            ),
        )
        _write_remote_file(
            client,
            SERVICE_CONFIG_PATHS["traefik"],
            build_traefik_static_config(acme_email=_get_traefik_acme_email()),
        )
        _write_remote_file(client, TRAEFIK_SYSTEMD_PATH, build_traefik_systemd_unit())

        _exec_checked(
            client,
            f"chmod 600 {TRAEFIK_ENV_PATH}",
            t("gateway.setTraefikEnvPermissionFailed"),
        )
        _exec_checked(
            client,
            "systemctl daemon-reload && systemctl restart traefik",
            t("gateway.restartTraefikFailed"),
        )
    except ProxmoxError:
        raise
    except Exception as exc:
        raise ProxmoxError(t("gateway.applyTraefikDnsChallengeFailed", error=exc))
    finally:
        client.close()


def control_service(session: object, service: str, action: str) -> tuple[bool, str]:
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    valid_actions = {"start", "stop", "restart", "reload"}
    if action not in valid_actions:
        raise BadRequestError(t("gateway.invalidAction", action=action))

    unit = _systemd_unit(service)

    client = None
    try:
        client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
        if action == "restart":
            # Some services hang on restart; do stop+start with a kill fallback
            _exec(client, f"systemctl stop {unit} 2>&1; sleep 1; "
                          f"systemctl kill -s SIGKILL {unit} 2>/dev/null; "
                          f"systemctl start {unit} 2>&1")
            code, out, err = _exec(client, f"systemctl is-active {unit} 2>&1")
            if out.strip() == "active":
                return True, f"{service} restart 完成"
            return False, f"{service} restart 後狀態: {out.strip()}"
        else:
            code, out, err = _exec(client, f"systemctl {action} {unit} 2>&1")
            output = (out + err).strip()
            return code == 0, output or f"{service} {action} 完成"
    except Exception as exc:
        return False, str(exc)
    finally:
        if client is not None:
            client.close()


def get_service_logs(session: object, service: str, lines: int = 50) -> tuple[bool, str]:
    """Read recent journalctl logs for a service on the Gateway VM."""
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    unit = _systemd_unit(service)

    client = None
    try:
        client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
        _, out, err = _exec(client, f"journalctl -u {unit} --no-pager -n {lines} 2>&1")
        return True, (out + err).strip()
    finally:
        if client is not None:
            client.close()


def get_service_status(session: object, service: str) -> tuple[bool, str]:
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]

    unit = _systemd_unit(service)

    client = None
    try:
        client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
        code, _, _ = _exec(client, f"systemctl is-active {unit}")
        _, status_out, _ = _exec(
            client,
            f"systemctl show {unit} --no-page "
            f"-p ActiveState,SubState,MainPID 2>&1 | head -5",
        )
        return code == 0, status_out.strip()
    except Exception as exc:
        return False, str(exc)
    finally:
        if client is not None:
            client.close()


def _parse_wireguard_dump(
    output: str, *, now_timestamp: int | None = None
) -> dict[str, int | bool | None]:
    """Parse `wg show <interface> dump` without returning any key material."""
    lines = [line.split("\t") for line in output.splitlines() if line.strip()]
    if not lines:
        return {
            "listen_port": None,
            "live_peers": 0,
            "recent_handshakes": 0,
            "transfer_rx_bytes": 0,
            "transfer_tx_bytes": 0,
            "inspection_available": False,
        }

    now_value = (
        now_timestamp
        if now_timestamp is not None
        else int(datetime.now(timezone.utc).timestamp())
    )

    def integer(value: str | None) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    interface = lines[0]
    peers = lines[1:]
    handshakes = [integer(peer[4] if len(peer) > 4 else None) for peer in peers]
    return {
        # `wg show <interface> dump` interface row:
        # private-key, public-key, listen-port, fwmark.
        "listen_port": integer(interface[2] if len(interface) > 2 else None) or None,
        "live_peers": len(peers),
        "recent_handshakes": sum(
            1
            for handshake in handshakes
            if handshake > 0 and 0 <= now_value - handshake <= 180
        ),
        "transfer_rx_bytes": sum(
            integer(peer[5] if len(peer) > 5 else None) for peer in peers
        ),
        "transfer_tx_bytes": sum(
            integer(peer[6] if len(peer) > 6 else None) for peer in peers
        ),
        "inspection_available": True,
    }


def get_wireguard_overview(session: object) -> GatewayWireGuardOverview:
    """Return a secret-free WireGuard control-plane and runtime summary."""
    from app.repositories import wireguard_peer as peer_repo  # noqa: PLC0415
    from app.repositories.gateway_config import (  # noqa: PLC0415
        get_decrypted_private_key,
    )

    now = datetime.now(timezone.utc)
    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
    interface = settings.WIREGUARD_INTERFACE
    unit = _systemd_unit("wireguard")
    active_sessions = peer_repo.list_active_unexpired(  # type: ignore[arg-type]
        session=session, now=now
    )
    expired_sessions = peer_repo.list_expired_active(  # type: ignore[arg-type]
        session=session, now=now
    )

    metrics = _parse_wireguard_dump("")
    client = None
    try:
        client = _make_client(
            config.host, config.ssh_port, config.ssh_user, private_key_pem
        )
        code, out, _err = _exec(
            client, f"wg show {shlex.quote(interface)} dump 2>/dev/null"
        )
        if code == 0:
            metrics = _parse_wireguard_dump(out)
    except Exception as exc:
        logger.warning("Unable to inspect WireGuard runtime on Gateway VM: %s", exc)
    finally:
        if client is not None:
            client.close()

    endpoint_host = settings.WIREGUARD_ENDPOINT_HOST.strip() or config.host
    if ":" in endpoint_host and not endpoint_host.startswith("["):
        endpoint_host = f"[{endpoint_host}]"
    return GatewayWireGuardOverview(
        mode=settings.DESKTOP_TUNNEL_MODE,
        interface=interface,
        systemd_unit=unit,
        endpoint=f"{endpoint_host}:{settings.WIREGUARD_ENDPOINT_PORT}",
        client_subnet=settings.WIREGUARD_CLIENT_SUBNET,
        vm_subnet=settings.WIREGUARD_VM_SUBNET,
        session_ttl_seconds=settings.WIREGUARD_SESSION_TTL_SECONDS,
        reconcile_enabled=settings.WIREGUARD_RECONCILE_ENABLED,
        authorized_sessions=len(active_sessions),
        expired_sessions=len(expired_sessions),
        inspected_at=now,
        **metrics,
    )


def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    return normalized or None


def _extract_current_version(service: str, version_output: str) -> str | None:
    output = version_output.strip()
    if not output:
        return None

    if service == "haproxy":
        match = _HAPROXY_VERSION_PATTERN.search(output)
        return _normalize_version(match.group(1)) if match else None

    if service == "traefik":
        match = _TRAEFIK_VERSION_PATTERN.search(output)
        if match:
            return _normalize_version(match.group(1))

    match = _GENERIC_VERSION_PATTERN.search(output)
    return _normalize_version(match.group(1)) if match else None


def _load_install_script_targets() -> dict[str, str]:
    script_path = Path(__file__).resolve().parents[4] / "gateway" / "install.sh"
    try:
        content = script_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("無法讀取 Gateway 安裝腳本版本資訊: %s", exc)
        return {}

    targets: dict[str, str] = {}
    traefik_match = re.search(r'^TRAEFIK_VERSION="([^"]+)"', content, re.MULTILINE)
    frp_match = re.search(r'^FRP_VERSION="([^"]+)"', content, re.MULTILINE)
    if traefik_match:
        targets["traefik"] = traefik_match.group(1)
    if frp_match:
        targets["frps"] = frp_match.group(1)
        targets["frpc"] = frp_match.group(1)
    return targets


def _build_service_version_info(
    *,
    service: str,
    version_output: str,
    install_targets: dict[str, str],
    candidate_version: str | None,
) -> GatewayServiceVersionInfo:
    current_version = _extract_current_version(service, version_output)
    target_version = install_targets.get(service)
    source = "SkyLab install script"

    if service == "haproxy":
        target_version = _normalize_version(candidate_version)
        source = "apt candidate"
    elif target_version is None:
        source = "detected only"

    normalized_current = _normalize_version(current_version)
    normalized_target = _normalize_version(target_version)
    update_available = None
    if normalized_current and normalized_target:
        update_available = normalized_current != normalized_target

    return GatewayServiceVersionInfo(
        service=service,
        current_version=normalized_current,
        target_version=normalized_target,
        update_available=update_available,
        source=source,
    )


def _get_haproxy_candidate_version(client) -> str | None:
    code, out, err = _exec(
        client,
        "apt-cache policy haproxy 2>/dev/null | sed -n 's/^  Candidate: //p' | head -1",
    )
    if code != 0:
        return None
    candidate = (out or err).strip()
    if not candidate or candidate == "(none)":
        return None
    return _normalize_version(candidate)


def get_service_versions(session: object) -> GatewayServiceVersionsResult:
    from app.repositories.gateway_config import (
        get_decrypted_private_key,  # noqa: PLC0415
    )

    config = _get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
    install_targets = _load_install_script_targets()

    client = _make_client(config.host, config.ssh_port, config.ssh_user, private_key_pem)
    try:
        haproxy_candidate_version = _get_haproxy_candidate_version(client)
        items: list[GatewayServiceVersionInfo] = []
        for service, command in _SERVICE_VERSION_COMMANDS.items():
            code, out, err = _exec(client, command)
            version_output = (out or err).strip() or (out + err).strip()
            info = _build_service_version_info(
                service=service,
                version_output=version_output,
                install_targets=install_targets,
                candidate_version=haproxy_candidate_version if service == "haproxy" else None,
            )
            if code != 0 and info.current_version is None:
                info.detection_error = version_output or f"無法取得 {service} 版本"
            items.append(info)

        return GatewayServiceVersionsResult(
            items=items,
            checked_at=datetime.now(timezone.utc),
        )
    finally:
        client.close()
