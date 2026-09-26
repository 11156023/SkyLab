"""Gateway 主機上的 nginx：SkyLab 自動管理的設定檔與 Let's Encrypt 憑證。

nginx 同時扛兩件事，各自對應一份 SkyLab 完整持有的設定檔：
- ``stream.conf``：Port 轉發（TCP／UDP，由 ``nat_service`` 產生）
- ``http.conf``：網域反向代理（由 ``reverse_proxy_service`` 產生）

兩份檔案都由 ``install.sh`` 寫好的 ``nginx.conf`` 以 ``include`` 載入，所以這裡
不需要像以前的 haproxy 那樣用 BEGIN/END 標記切出自動管理區段。寫入一律
「先落地、``nginx -t`` 驗證、失敗就還原」，避免壞設定讓下一次 reload 失敗。

HTTPS 憑證改由 certbot 的 Cloudflare DNS-01 簽發（同一把 DNS API token），
同一個 zone 底下的一層子網域共用一張萬用憑證，多層子網域才逐一簽發。
"""

from __future__ import annotations

import logging
import re
import shlex
from datetime import datetime, timezone
from typing import Any

from app.core.i18n import t
from app.exceptions import BadRequestError, ProxmoxError

logger = logging.getLogger(__name__)

NGINX_CONF_PATH = "/etc/nginx/nginx.conf"
NGINX_MANAGED_DIR = "/etc/nginx/skylab"
NGINX_STREAM_CONF_PATH = f"{NGINX_MANAGED_DIR}/stream.conf"
NGINX_HTTP_CONF_PATH = f"{NGINX_MANAGED_DIR}/http.conf"
NGINX_FALLBACK_CERT_PATH = f"{NGINX_MANAGED_DIR}/fallback.crt"
NGINX_FALLBACK_KEY_PATH = f"{NGINX_MANAGED_DIR}/fallback.key"
LETSENCRYPT_LIVE_DIR = "/etc/letsencrypt/live"
CERTBOT_CLOUDFLARE_CREDENTIALS_PATH = "/etc/letsencrypt/skylab-cloudflare.ini"
# Cloudflare 的 DNS 更新通常幾秒內就查得到，certbot 預設 10 秒等待對它夠用
CERTBOT_DNS_PROPAGATION_SECONDS = 10

_MANAGED_HEADER = (
    "# SkyLab 自動管理的設定，請勿手動修改\n"
    "# 由 SkyLab 後端透過 SSH 依資料庫規則重建，手動改動會在下次同步時被覆蓋\n"
)

_CERT_NAME_PATTERN = re.compile(r"^[a-z0-9.-]{1,255}$")


# ─── 設定檔產生 ──────────────────────────────────────────────────────────────


def stream_server_name(vmid: int, external_port: int, protocol: str) -> str:
    return f"cc-{vmid}-{external_port}-{protocol}"


def build_stream_config(rules: list[Any]) -> str:
    """從 NAT 規則產生 ``stream.conf``：每條規則一個 ``server`` 區塊。

    nginx 的 stream 模組原生支援 UDP（``listen ... udp``），同一個對外 port 的
    TCP 與 UDP 可以各開一個 server，這是以前 haproxy 做不到的。
    """
    lines: list[str] = [_MANAGED_HEADER]
    for r in rules:
        name = stream_server_name(r.vmid, r.external_port, r.protocol)
        udp = " udp" if r.protocol == "udp" else ""
        lines += [
            f"# {name}",
            "server {",
            f"    listen {r.external_port}{udp};",
            f"    proxy_pass {r.vm_ip}:{r.internal_port};",
            "    proxy_connect_timeout 5s;",
            "    proxy_timeout 1m;",
            "}",
            "",
        ]
    return "\n".join(lines)


def http_server_name(vmid: int, domain: str) -> str:
    return f"cc-{vmid}-{domain.replace('.', '-')}"


def _proxy_location(vm_ip: str, internal_port: int) -> list[str]:
    return [
        "    location / {",
        f"        proxy_pass http://{vm_ip}:{internal_port};",
        "        proxy_http_version 1.1;",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header X-Forwarded-Proto $scheme;",
        "        proxy_set_header Upgrade $http_upgrade;",
        "        proxy_set_header Connection $connection_upgrade;",
        "        proxy_read_timeout 300s;",
        "    }",
    ]


def build_http_config(rules: list[Any], cert_names: dict[str, str | None]) -> str:
    """從反向代理規則產生 ``http.conf``。

    ``cert_names`` 是 domain → 已簽好的憑證名稱（``/etc/letsencrypt/live/<name>``）；
    給 ``None`` 代表這個網域的憑證還沒簽下來，先用安裝時產生的自簽憑證頂著，
    讓 ``nginx -t`` 能過、站台照樣可以連（瀏覽器會警告），下次同步再補簽。
    """
    lines: list[str] = [
        _MANAGED_HEADER,
        "# WebSocket 升級：有 Upgrade 標頭時才把 Connection 設成 upgrade",
        "map $http_upgrade $connection_upgrade {",
        "    default upgrade;",
        "    ''      close;",
        "}",
        "",
    ]
    for r in rules:
        name = http_server_name(r.vmid, r.domain)
        lines += [f"# {name}", "server {", "    listen 80;", f"    server_name {r.domain};"]
        if r.enable_https:
            lines += ["    return 301 https://$host$request_uri;", "}", ""]
            cert_name = cert_names.get(r.domain)
            if cert_name:
                cert_path = f"{LETSENCRYPT_LIVE_DIR}/{cert_name}/fullchain.pem"
                key_path = f"{LETSENCRYPT_LIVE_DIR}/{cert_name}/privkey.pem"
                cert_note = f"    # 憑證：{cert_name}"
            else:
                cert_path = NGINX_FALLBACK_CERT_PATH
                key_path = NGINX_FALLBACK_KEY_PATH
                cert_note = "    # 憑證尚未簽發，暫用自簽憑證；重新同步會再嘗試簽發"
            lines += [
                f"# {name} (https)",
                "server {",
                "    listen 443 ssl;",
                f"    server_name {r.domain};",
                cert_note,
                f"    ssl_certificate {cert_path};",
                f"    ssl_certificate_key {key_path};",
                *_proxy_location(r.vm_ip, r.internal_port),
                "}",
                "",
            ]
        else:
            lines += [*_proxy_location(r.vm_ip, r.internal_port), "}", ""]
    return "\n".join(lines)


# ─── 憑證規劃 ────────────────────────────────────────────────────────────────


def plan_certificate(domain: str, zone_name: str | None) -> tuple[str, list[str]]:
    """決定某個網域該用哪張憑證：回傳 ``(憑證名稱, 要涵蓋的網域清單)``。

    萬用憑證只涵蓋一層子網域，所以 zone 本身與 ``x.zone`` 共用 ``zone`` 這張
    （``zone`` + ``*.zone``）；``a.b.zone`` 這種多層的只能單獨簽。查不到 zone
    名稱時也退回單獨簽，不會因此簽不出來。
    """
    clean = domain.strip().lower().rstrip(".")
    if zone_name:
        zone = zone_name.strip().lower().rstrip(".")
        if clean == zone or (
            clean.endswith(f".{zone}") and "." not in clean[: -(len(zone) + 1)]
        ):
            return zone, [zone, f"*.{zone}"]
    return clean, [clean]


def _assert_safe_cert_name(value: str) -> None:
    # 憑證名稱與網域會拼進 shell 指令與 nginx 設定，只放行主機名稱字元
    if not _CERT_NAME_PATTERN.fullmatch(value) or ".." in value or value.startswith("-"):
        raise BadRequestError(t("gateway.certificateNameInvalid", name=value))


# ─── 遠端寫入 ────────────────────────────────────────────────────────────────


def _exec(client: Any, command: str) -> tuple[int, str, str]:
    from app.infrastructure.ssh import exec_command

    return exec_command(client, command)


def _sftp_write(client: Any, path: str, content: str) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "wb") as handle:
            handle.write(content.encode("utf-8"))
    finally:
        sftp.close()


def write_validated_config(
    client: Any, path: str, content: str, *, reload: bool = True
) -> None:
    """把設定檔寫到 Gateway：先備份、換上新檔、``nginx -t``，不過就還原。

    nginx 只能整棵設定樹一起驗證，沒辦法單獨檢查一個 include 進來的檔案，
    所以是「先換上再驗」；驗證失敗會把舊檔放回去，執行中的 nginx 從頭到尾
    不受影響（只有 reload 才會重讀設定）。
    """
    tmp_path = f"{path}.SkyLab.tmp"
    prev_path = f"{path}.SkyLab.prev"
    _sftp_write(client, tmp_path, content)

    quoted = shlex.quote(path)
    quoted_tmp = shlex.quote(tmp_path)
    quoted_prev = shlex.quote(prev_path)
    reload_step = " && systemctl reload nginx 2>&1" if reload else ""
    command = (
        f"if [ -f {quoted} ]; then cp -a {quoted} {quoted_prev}; fi; "
        f"mv -f {quoted_tmp} {quoted} && "
        f"if nginx -t 2>&1; then rm -f {quoted_prev}{reload_step}; "
        f"else if [ -f {quoted_prev} ]; then mv -f {quoted_prev} {quoted}; "
        f"else rm -f {quoted}; fi; exit 1; fi"
    )
    code, out, err = _exec(client, command)
    if code != 0:
        _exec(client, f"rm -f {quoted_tmp} {quoted_prev}")
        raise ProxmoxError(
            t("gateway.nginxConfigInvalid", path=path, detail=(out + err).strip())
        )


def write_certbot_credentials(client: Any, cloudflare_api_token: str) -> None:
    """把 Cloudflare token 寫成 certbot-dns-cloudflare 的認證檔（600）。"""
    clean_token = cloudflare_api_token.strip()
    if not clean_token or "\n" in clean_token or "\r" in clean_token:
        raise BadRequestError(t("gateway.cloudflareApiTokenInvalidFormat"))

    content = (
        "# SkyLab 自動管理，供 certbot 的 Cloudflare DNS-01 驗證使用\n"
        f"dns_cloudflare_api_token = {clean_token}\n"
    )
    quoted = shlex.quote(CERTBOT_CLOUDFLARE_CREDENTIALS_PATH)
    code, out, err = _exec(
        client,
        f"mkdir -p {shlex.quote(LETSENCRYPT_LIVE_DIR)} && "
        f"touch {quoted} && chmod 600 {quoted}",
    )
    if code != 0:
        raise ProxmoxError(
            t("gateway.writeCertbotCredentialsFailed", detail=(out + err).strip())
        )
    _sftp_write(client, CERTBOT_CLOUDFLARE_CREDENTIALS_PATH, content)


def certificate_exists(client: Any, cert_name: str) -> bool:
    _assert_safe_cert_name(cert_name)
    code, _, _ = _exec(
        client,
        f"test -f {shlex.quote(f'{LETSENCRYPT_LIVE_DIR}/{cert_name}/fullchain.pem')}",
    )
    return code == 0


def issue_certificate(
    client: Any, cert_name: str, domains: list[str], *, acme_email: str
) -> bool:
    """用 certbot 的 Cloudflare DNS-01 簽一張憑證；失敗只記 log、回傳 False。

    簽發要等 DNS 傳播與 Let's Encrypt 驗證，通常十幾秒。失敗不中斷同步：
    站台會先掛自簽憑證，管理員重新同步時會再試一次。
    """
    _assert_safe_cert_name(cert_name)
    for domain in domains:
        _assert_safe_cert_name(domain.lstrip("*."))
    clean_email = acme_email.strip()
    if not clean_email:
        raise BadRequestError(t("gateway.acmeEmailRequired"))

    domain_args = " ".join(f"-d {shlex.quote(domain)}" for domain in domains)
    command = (
        "certbot certonly --non-interactive --agree-tos --keep-until-expiring "
        f"--email {shlex.quote(clean_email)} "
        "--dns-cloudflare "
        f"--dns-cloudflare-credentials {shlex.quote(CERTBOT_CLOUDFLARE_CREDENTIALS_PATH)} "
        f"--dns-cloudflare-propagation-seconds {CERTBOT_DNS_PROPAGATION_SECONDS} "
        f"--cert-name {shlex.quote(cert_name)} {domain_args} 2>&1"
    )
    code, out, err = _exec(client, command)
    if code != 0:
        logger.warning(
            "[nginx] 憑證 %s（%s）簽發失敗，先以自簽憑證頂替：%s",
            cert_name,
            ", ".join(domains),
            (out + err).strip()[-800:],
        )
        return False
    logger.info("[nginx] 憑證 %s 已簽發（%s）", cert_name, ", ".join(domains))
    return True


def ensure_certificates(
    client: Any, plans: dict[str, list[str]], *, acme_email: str
) -> set[str]:
    """確認每張規劃中的憑證都在；缺的就簽。回傳目前可用的憑證名稱。"""
    ready: set[str] = set()
    for cert_name, domains in plans.items():
        if certificate_exists(client, cert_name) or issue_certificate(
            client, cert_name, domains, acme_email=acme_email
        ):
            ready.add(cert_name)
    return ready


def get_acme_email() -> str:
    from app.core.config import settings

    return str(settings.EMAILS_FROM_EMAIL or settings.FIRST_SUPERUSER)


def renew_certificates(client: Any) -> None:
    """跑一次 ``certbot renew``；到期前 30 天內的憑證會換新並 reload nginx。"""
    code, out, err = _exec(client, "certbot renew --non-interactive --quiet 2>&1")
    if code != 0:
        raise ProxmoxError(
            t("gateway.certificateRenewFailed", detail=(out + err).strip()[-800:])
        )


# ─── 執行期快照 ──────────────────────────────────────────────────────────────

_VERSION_PATTERN = re.compile(r"nginx/([0-9][^\s]*)")
_HTTP_SERVER_PATTERN = re.compile(
    r"^# (cc-(\d+)-[a-z0-9-]+)( \(https\))?\nserver \{\n(?P<body>(?:    .*\n)+?)\}",
    re.MULTILINE,
)
_STREAM_SERVER_PATTERN = re.compile(
    r"^# (cc-(\d+)-(\d+)-([a-z0-9-]+))\nserver \{\n(?P<body>(?:    .*\n)+?)\}",
    re.MULTILINE,
)
_SERVER_NAME_PATTERN = re.compile(r"^\s*server_name\s+([^;]+);", re.MULTILINE)
_PROXY_PASS_PATTERN = re.compile(r"^\s*proxy_pass\s+([^;]+);", re.MULTILINE)
_CERT_LINE_PATTERN = re.compile(r"^\s*ssl_certificate\s+([^;]+);", re.MULTILINE)


def parse_http_servers(content: str) -> list[dict[str, Any]]:
    """從自動產生的 ``http.conf`` 讀回每個網域的狀態（只給快照用）。

    只認得 ``build_http_config`` 自己寫出來的格式；80 的轉址區塊與 443 的
    代理區塊會合併成一筆。
    """
    servers: dict[str, dict[str, Any]] = {}
    for match in _HTTP_SERVER_PATTERN.finditer(content):
        name, vmid, https_suffix = match.group(1), int(match.group(2)), match.group(3)
        body = match.group("body")
        entry = servers.setdefault(
            name,
            {
                "name": name,
                "vmid": vmid,
                "domain": "",
                "upstream": None,
                "https": False,
                "certificate": None,
                "certificate_ready": None,
            },
        )
        server_name = _SERVER_NAME_PATTERN.search(body)
        if server_name:
            entry["domain"] = server_name.group(1).strip()
        proxy_pass = _PROXY_PASS_PATTERN.search(body)
        if proxy_pass:
            entry["upstream"] = proxy_pass.group(1).strip()
        if https_suffix:
            entry["https"] = True
            cert_line = _CERT_LINE_PATTERN.search(body)
            cert_path = cert_line.group(1).strip() if cert_line else ""
            if cert_path.startswith(f"{LETSENCRYPT_LIVE_DIR}/"):
                entry["certificate"] = cert_path[len(LETSENCRYPT_LIVE_DIR) + 1 :].split("/")[0]
                entry["certificate_ready"] = True
            else:
                entry["certificate_ready"] = False
        elif "return 301 https://" in body:
            entry["https"] = True
    return list(servers.values())


def parse_stream_servers(content: str) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for match in _STREAM_SERVER_PATTERN.finditer(content):
        body = match.group("body")
        proxy_pass = _PROXY_PASS_PATTERN.search(body)
        servers.append(
            {
                "name": match.group(1),
                "vmid": int(match.group(2)),
                "listen": int(match.group(3)),
                "protocol": match.group(4),
                "upstream": proxy_pass.group(1).strip() if proxy_pass else None,
            }
        )
    return servers


def parse_certificate_listing(output: str) -> list[dict[str, Any]]:
    """解析 ``<名稱>\\t<openssl 到期日>`` 的逐行輸出。"""
    items: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, _, end_text = line.partition("\t")
        expires_at: datetime | None = None
        try:
            expires_at = datetime.strptime(end_text.strip(), "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            expires_at = None
        items.append({"name": name.strip(), "expires_at": expires_at})
    return items


def parse_version(output: str) -> str | None:
    match = _VERSION_PATTERN.search(output)
    return match.group(1) if match else None


_CERT_LISTING_COMMAND = (
    f"for f in {LETSENCRYPT_LIVE_DIR}/*/fullchain.pem; do "
    '[ -f "$f" ] || continue; '
    'printf "%s\\t%s\\n" "$(basename "$(dirname "$f")")" '
    '"$(openssl x509 -enddate -noout -in "$f" 2>/dev/null | cut -d= -f2)"; '
    "done"
)


def collect_runtime(client: Any) -> dict[str, Any]:
    """一條 SSH 連線抓齊快照需要的東西：版本、狀態、設定是否合法、兩份設定檔、憑證。"""
    _, version_out, version_err = _exec(client, "nginx -v 2>&1")
    _, active_out, _ = _exec(client, "systemctl is-active nginx 2>&1")
    test_code, _, _ = _exec(client, "nginx -t >/dev/null 2>&1")
    _, http_conf, _ = _exec(client, f"cat {shlex.quote(NGINX_HTTP_CONF_PATH)} 2>/dev/null")
    _, stream_conf, _ = _exec(
        client, f"cat {shlex.quote(NGINX_STREAM_CONF_PATH)} 2>/dev/null"
    )
    _, cert_out, _ = _exec(client, _CERT_LISTING_COMMAND)
    return {
        "version": parse_version(version_out + version_err),
        "active": active_out.strip() == "active",
        "config_valid": test_code == 0,
        "http_servers": parse_http_servers(http_conf),
        "stream_servers": parse_stream_servers(stream_conf),
        "certificates": parse_certificate_listing(cert_out),
    }


__all__ = [
    "CERTBOT_CLOUDFLARE_CREDENTIALS_PATH",
    "LETSENCRYPT_LIVE_DIR",
    "NGINX_CONF_PATH",
    "NGINX_FALLBACK_CERT_PATH",
    "NGINX_FALLBACK_KEY_PATH",
    "NGINX_HTTP_CONF_PATH",
    "NGINX_MANAGED_DIR",
    "NGINX_STREAM_CONF_PATH",
    "build_http_config",
    "build_stream_config",
    "certificate_exists",
    "collect_runtime",
    "ensure_certificates",
    "get_acme_email",
    "http_server_name",
    "issue_certificate",
    "parse_certificate_listing",
    "parse_http_servers",
    "parse_stream_servers",
    "parse_version",
    "plan_certificate",
    "renew_certificates",
    "stream_server_name",
    "write_certbot_credentials",
    "write_validated_config",
]
