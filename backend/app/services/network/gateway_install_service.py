"""Gateway 一鍵安裝：用已綁定的 SSH 金鑰把 gateway/install.sh 送上去並在背景執行。

安裝要跑好幾分鐘（apt、certbot、WireGuard），不能卡在一個 HTTP 請求裡；
所以腳本交給 Gateway 自己的 systemd（``systemd-run`` 臨時服務）執行，
日誌、開始時間與結束碼都寫在 Gateway 的 ``STATE_DIR``。狀態查詢每次重新 SSH
讀回來，後端重啟、SSH 斷線或多個 worker 行程都不影響判斷。
"""

from __future__ import annotations

import io
import ipaddress
import logging
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.i18n import t
from app.exceptions import BadRequestError, ConflictError, UpstreamServiceError
from app.schemas.gateway import (
    GatewayInstallInterface,
    GatewayInstallOptions,
    GatewayInstallStatus,
)
from app.services.network import gateway_service

logger = logging.getLogger(__name__)

STATE_DIR = "/var/lib/skylab-gateway-install"
SYSTEMD_UNIT = "skylab-gateway-install"
# SFTP 的相對路徑落在登入帳號的家目錄；非 root 帳號寫不進 STATE_DIR，
# 先放這裡再由 root 搬過去
UPLOAD_NAME = ".skylab-gateway-install.sh"
LOG_TAIL_LINES = 400
COMPONENTS = ("nginx", "wg", "certbot", "ufw")
_COMPONENT_KEYS = {"nginx": "nginx", "wg": "wireguard", "certbot": "certbot", "ufw": "ufw"}

_LOG_MARKER = "---SKYLAB-LOG---"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def install_script_path() -> Path:
    """repo 根目錄（容器內是 /app）底下的 gateway/install.sh。"""
    return Path(__file__).resolve().parents[4] / "gateway" / "install.sh"


def load_install_script() -> bytes:
    path = install_script_path()
    if not path.is_file():
        raise BadRequestError(t("gateway.installScriptMissing"))
    return normalize_script(path.read_bytes())


def normalize_script(data: bytes) -> bytes:
    """去掉 UTF-8 BOM 與 CRLF：Windows 上 checkout 的檔案直接丟給 bash 會整支壞掉。"""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.replace(b"\r\n", b"\n")


# ─── 遠端指令組裝（純函式，方便測試） ─────────────────────────────────────────


def _as_root(inner: str, shell_args: str = "") -> str:
    """以 root 執行 inner：本身是 root 直接跑，否則試免密碼 sudo，兩者皆否印 ROOT=0。

    外層先印出 SSH 來源 IP（sudo 會清掉 SSH_CLIENT），拿來當監控放行的預設值。
    shell_args 由外層 shell 展開後成為 inner 的 $1…，引號由呼叫端負責。
    """
    run = f"bash -c {shlex.quote(inner)} skylab-install {shell_args}".rstrip()
    return (
        'echo "CLIENT=${SSH_CLIENT%% *}"; '
        f'if [ "$(id -u)" = 0 ]; then echo ROOT=1; {run}; '
        f"elif sudo -n true 2>/dev/null; then echo ROOT=1; sudo -n {run}; "
        "else echo ROOT=0; fi"
    )


def status_script() -> str:
    """以 root 執行的狀態查詢：key=value 列，最後接安裝日誌尾端。"""
    components = " ".join(COMPONENTS)
    return f"""
dir={STATE_DIR}
if systemctl is-active --quiet {SYSTEMD_UNIT}.service; then echo RUNNING=1; else echo RUNNING=0; fi
[ -f "$dir/exit_code" ] && echo "EXIT=$(head -c 16 "$dir/exit_code")"
[ -f "$dir/exit_code" ] && echo "FINISHED=$(date -u -r "$dir/exit_code" +%Y-%m-%dT%H:%M:%SZ)"
[ -f "$dir/started_at" ] && echo "STARTED=$(head -c 64 "$dir/started_at")"
[ -f "$dir/install.log" ] && echo HAS_LOG=1
[ -r /etc/os-release ] && (. /etc/os-release; echo "OS=${{PRETTY_NAME:-}}")
ip -o link show 2>/dev/null | awk -F': ' '{{print "LINK=" $2}}'
ip -o -4 addr show 2>/dev/null | awk '{{print "ADDR=" $2 " " $4}}'
echo "DEFAULT_IFACE=$(ip route show default 2>/dev/null | awk '{{print $5; exit}}')"
for c in {components}; do
  if command -v "$c" >/dev/null 2>&1; then echo "COMPONENT=$c 1"; else echo "COMPONENT=$c 0"; fi
done
echo {_LOG_MARKER}
[ -f "$dir/install.log" ] && tail -n {LOG_TAIL_LINES} "$dir/install.log"
exit 0
"""


def build_status_command() -> str:
    return _as_root(status_script())


def build_env(options: GatewayInstallOptions) -> dict[str, str]:
    """install.sh 讀的環境變數。WireGuard 介面與子網固定跟後端設定走。"""
    client_subnet = ipaddress.ip_network(settings.WIREGUARD_CLIENT_SUBNET, strict=False)
    # 伺服器端取客戶端子網的第一個位址（預設 10.250.0.1/16），與 install.sh 預設一致
    wg_address = f"{next(client_subnet.hosts())}/{client_subnet.prefixlen}"
    return {
        "HOME": "/root",
        "FORWARD_PORT_RANGE": f"{options.forward_port_start}:{options.forward_port_end}",
        "MONITORING_ALLOW_FROM": ",".join(options.monitoring_allow_from),
        "WG_INTERFACE": settings.WIREGUARD_INTERFACE,
        "WG_ADDRESS": wg_address,
        "WG_CLIENT_SUBNET": str(client_subnet),
        "WG_VM_SUBNET": str(ipaddress.ip_network(settings.WIREGUARD_VM_SUBNET, strict=False)),
        "WG_VM_INTERFACE": options.vm_interface,
        "WG_SNAT_ADDRESS": options.snat_address,
        "WG_INGRESS_INTERFACE": options.ingress_interface,
        "WG_LISTEN_PORT": str(options.listen_port),
    }


def start_script(env: dict[str, str]) -> str:
    """以 root 執行的啟動流程；$1 是上傳到登入帳號家目錄的腳本。"""
    setenv = " ".join(shlex.quote(f"--setenv={key}={value}") for key, value in env.items())
    return f"""
set -eu
dir={STATE_DIR}
src="$1"
if systemctl is-active --quiet {SYSTEMD_UNIT}.service; then
  rm -f "$src"; echo SKYLAB_INSTALL_BUSY; exit 0
fi
command -v systemd-run >/dev/null 2>&1 || {{ rm -f "$src"; echo SKYLAB_INSTALL_NO_SYSTEMD; exit 0; }}
systemctl reset-failed {SYSTEMD_UNIT}.service >/dev/null 2>&1 || true
install -d -m 700 "$dir"
install -m 700 "$src" "$dir/install.sh"
rm -f "$src" "$dir/exit_code"
: > "$dir/install.log"
date -u +%Y-%m-%dT%H:%M:%SZ > "$dir/started_at"
systemd-run --unit={SYSTEMD_UNIT} --collect --quiet --working-directory="$dir" {setenv} \\
  /bin/bash -c 'bash ./install.sh > install.log 2>&1; echo $? > exit_code'
echo SKYLAB_INSTALL_STARTED
"""


def build_start_command(env: dict[str, str]) -> str:
    # 上傳檔在登入帳號的家目錄：由外層 shell 展開 $HOME，交給 root 端當 $1
    return _as_root(start_script(env), f'"$HOME/{UPLOAD_NAME}"')


# ─── 輸出解析 ──────────────────────────────────────────────────────────────────


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def parse_status_output(output: str) -> dict[str, object]:
    head, _, log = output.partition(_LOG_MARKER)
    fields: dict[str, str] = {}
    links: list[str] = []
    addresses: dict[str, list[str]] = {}
    components: dict[str, bool] = {}

    for line in head.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key == "LINK":
            # veth 會長成 eth0@if5；lo 不是可選的介面
            name = value.strip().split("@", 1)[0]
            if name and name != "lo" and name not in links:
                links.append(name)
        elif key == "ADDR":
            name, _, cidr = value.strip().partition(" ")
            name = name.split("@", 1)[0]
            if name and name != "lo" and cidr:
                addresses.setdefault(name, []).append(cidr)
        elif key == "COMPONENT":
            name, _, flag = value.strip().partition(" ")
            if name in _COMPONENT_KEYS:
                components[_COMPONENT_KEYS[name]] = flag == "1"
        else:
            fields[key] = value.strip()

    for name in addresses:
        if name not in links:
            links.append(name)

    running = fields.get("RUNNING") == "1"
    exit_code: int | None = None
    if fields.get("EXIT", "").lstrip("-").isdigit():
        exit_code = int(fields["EXIT"])

    if running:
        state = "running"
    elif exit_code is not None:
        state = "succeeded" if exit_code == 0 else "failed"
    elif fields.get("HAS_LOG") == "1":
        # 有日誌卻沒有結束碼也不在跑：Gateway 重開機或服務被砍掉
        state = "interrupted"
    else:
        state = "idle"

    return {
        "root_access": fields.get("ROOT") == "1",
        "client_ip": fields.get("CLIENT") or None,
        "state": state,
        "exit_code": None if running else exit_code,
        "started_at": _parse_datetime(fields.get("STARTED")),
        "finished_at": None if running else _parse_datetime(fields.get("FINISHED")),
        "os_name": fields.get("OS") or None,
        "default_interface": fields.get("DEFAULT_IFACE") or None,
        "interfaces": [
            GatewayInstallInterface(name=name, addresses=addresses.get(name, []))
            for name in links
        ],
        "components": components,
        "log": strip_ansi(log.lstrip("\n")),
    }


def suggest_install_options(
    *,
    interfaces: list[GatewayInstallInterface],
    default_interface: str | None,
    client_ip: str | None,
    gateway_vm_ip: str | None,
    forward_port_range: tuple[int, int] | None,
) -> GatewayInstallOptions:
    """依 Gateway 實際的網卡與 IP 管理的子網設定推出表單預設值。

    VM 內網介面＝持有 Gateway VM IP（或位在 WireGuard VM 子網內位址）的那張卡，
    對外介面＝預設路由走的那張卡。
    """
    vm_subnet = ipaddress.ip_network(settings.WIREGUARD_VM_SUBNET, strict=False)
    vm_interface: str | None = None
    snat_address: str | None = None

    def _host(cidr: str) -> ipaddress.IPv4Address | None:
        try:
            return ipaddress.IPv4Interface(cidr).ip
        except ValueError:
            return None

    candidates: list[tuple[str, ipaddress.IPv4Address]] = [
        (iface.name, host)
        for iface in interfaces
        for host in (_host(cidr) for cidr in iface.addresses)
        if host is not None
    ]
    for name, host in candidates:
        if gateway_vm_ip and str(host) == gateway_vm_ip.strip():
            vm_interface, snat_address = name, str(host)
            break
    if vm_interface is None:
        for name, host in candidates:
            if host in vm_subnet:
                vm_interface, snat_address = name, str(host)
                break

    names = [iface.name for iface in interfaces]
    ingress = default_interface if default_interface in names else None
    if ingress is None or ingress == vm_interface:
        ingress = next((name for name in names if name != vm_interface), None)

    start, end = forward_port_range or (30000, 39999)
    sources: list[str] = []
    if client_ip:
        try:
            sources.append(str(ipaddress.ip_address(client_ip)))
        except ValueError:
            pass

    # 預設值只是建議，不在這裡跑驗證（子網設定怪異時也要能回狀態）
    return GatewayInstallOptions.model_construct(
        ingress_interface=ingress or "eth0",
        vm_interface=vm_interface or ("eth1" if ingress != "eth1" else "eth0"),
        snat_address=snat_address or gateway_vm_ip or "10.10.0.2",
        listen_port=settings.WIREGUARD_ENDPOINT_PORT,
        forward_port_start=start,
        forward_port_end=end,
        monitoring_allow_from=sources,
    )


# ─── 對外入口 ──────────────────────────────────────────────────────────────────


def _connect(session: object) -> Any:
    from app.repositories.gateway_config import get_decrypted_private_key

    config = gateway_service._get_config(session)
    private_key_pem = get_decrypted_private_key(config)  # type: ignore[arg-type]
    try:
        return gateway_service.make_client(
            config.host,  # type: ignore[attr-defined]
            config.ssh_port,  # type: ignore[attr-defined]
            config.ssh_user,  # type: ignore[attr-defined]
            private_key_pem,
        )
    except Exception as exc:
        raise UpstreamServiceError(t("gateway.installConnectFailed", error=exc)) from exc


def _read_status(session: object, client: Any) -> GatewayInstallStatus:
    from app.services.network import ip_management_service

    code, out, err = gateway_service._exec(client, build_status_command())
    if code != 0 and "ROOT=" not in out:
        detail = (err or out).strip() or t("gateway.noOutput")
        raise UpstreamServiceError(t("gateway.installStatusFailed", error=detail))
    parsed = parse_status_output(out)

    subnet = ip_management_service.get_subnet_config(session)  # type: ignore[arg-type]
    defaults = suggest_install_options(
        interfaces=parsed["interfaces"],  # type: ignore[arg-type]
        default_interface=parsed["default_interface"],  # type: ignore[arg-type]
        client_ip=parsed["client_ip"],  # type: ignore[arg-type]
        gateway_vm_ip=subnet.gateway_vm_ip if subnet else None,
        forward_port_range=ip_management_service.get_forward_port_range(subnet),
    )
    return GatewayInstallStatus(
        state=parsed["state"],  # type: ignore[arg-type]
        root_access=bool(parsed["root_access"]),
        exit_code=parsed["exit_code"],  # type: ignore[arg-type]
        started_at=parsed["started_at"],  # type: ignore[arg-type]
        finished_at=parsed["finished_at"],  # type: ignore[arg-type]
        os_name=parsed["os_name"],  # type: ignore[arg-type]
        interfaces=parsed["interfaces"],  # type: ignore[arg-type]
        components=parsed["components"],  # type: ignore[arg-type]
        log=str(parsed["log"]),
        defaults=defaults,
        wireguard_interface=settings.WIREGUARD_INTERFACE,
        wireguard_client_subnet=settings.WIREGUARD_CLIENT_SUBNET,
        wireguard_vm_subnet=settings.WIREGUARD_VM_SUBNET,
    )


def get_install_status(session: object) -> GatewayInstallStatus:
    client = _connect(session)
    try:
        return _read_status(session, client)
    finally:
        client.close()


def start_install(session: object, options: GatewayInstallOptions) -> GatewayInstallStatus:
    """上傳 install.sh 並以 systemd-run 在背景執行，回傳啟動後的狀態。"""
    script = load_install_script()
    env = build_env(options)
    client = _connect(session)
    try:
        sftp = client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(script), UPLOAD_NAME)
            sftp.chmod(UPLOAD_NAME, 0o600)
        finally:
            sftp.close()

        code, out, err = gateway_service._exec(client, build_start_command(env))
        if "ROOT=0" in out:
            gateway_service._exec(client, f"rm -f {shlex.quote(UPLOAD_NAME)}")
            raise BadRequestError(t("gateway.installRootRequired"))
        if "SKYLAB_INSTALL_BUSY" in out:
            raise ConflictError(t("gateway.installAlreadyRunning"))
        if "SKYLAB_INSTALL_NO_SYSTEMD" in out:
            raise BadRequestError(t("gateway.installSystemdMissing"))
        if code != 0 or "SKYLAB_INSTALL_STARTED" not in out:
            detail = (err or out).strip() or t("gateway.noOutput")
            raise UpstreamServiceError(t("gateway.installStartFailed", error=detail))

        logger.info("Gateway install started with env keys %s", sorted(env))
        return _read_status(session, client)
    except (BadRequestError, ConflictError, UpstreamServiceError):
        raise
    except Exception as exc:
        logger.exception("Gateway install failed to start")
        raise UpstreamServiceError(t("gateway.installStartFailed", error=exc)) from exc
    finally:
        client.close()
