"""Gateway 主機上的 nginx：SkyLab 自動管理的設定檔。

nginx 同時扛兩件事，各自對應一份 SkyLab 完整持有的設定檔：
- ``stream.conf``：Port 轉發（TCP／UDP，由 ``nat_service`` 產生）
- ``http.conf``：網域反向代理（由 ``reverse_proxy_service`` 產生）

兩份檔案都由 ``install.sh`` 寫好的 ``nginx.conf`` 以 ``include`` 載入，所以這裡
不需要像以前的 haproxy 那樣用 BEGIN/END 標記切出自動管理區段。寫入一律
「先落地、``nginx -t`` 驗證、失敗就還原」，避免壞設定讓下一次 reload 失敗。

系統不簽發 HTTPS 憑證：管理員自己準備一張（通常是萬用憑證）放在 Gateway 上，
設定裡只記路徑，平台入口與所有 VM 網域共用；還沒設定時先掛安裝時產生的
自簽憑證。``inspect_certificate`` 在存檔前檢查那張憑證。
"""

from __future__ import annotations

import logging
import re
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.i18n import t
from app.exceptions import ProxmoxError

logger = logging.getLogger(__name__)

NGINX_CONF_PATH = "/etc/nginx/nginx.conf"
NGINX_MANAGED_DIR = "/etc/nginx/skylab"
NGINX_STREAM_CONF_PATH = f"{NGINX_MANAGED_DIR}/stream.conf"
NGINX_HTTP_CONF_PATH = f"{NGINX_MANAGED_DIR}/http.conf"
NGINX_FALLBACK_CERT_PATH = f"{NGINX_MANAGED_DIR}/fallback.crt"
NGINX_FALLBACK_KEY_PATH = f"{NGINX_MANAGED_DIR}/fallback.key"
# 寫入 stream.conf／http.conf 的序列化：backend 端的 PG advisory lock（"SKYLABNG"）
# 與 Gateway 端的 flock（涵蓋其他行程或手動同步）
_NGINX_CONFIG_LOCK_ID = 0x534B594C41424E47
_GATEWAY_NGINX_LOCK = "/run/lock/skylab-nginx.lock"
_GATEWAY_NGINX_LOCK_WAIT_SECONDS = 30

_MANAGED_HEADER = (
    "# SkyLab 自動管理的設定，請勿手動修改\n"
    "# 由 SkyLab 後端透過 SSH 依資料庫規則重建，手動改動會在下次同步時被覆蓋\n"
)

# 平台入口（主系統自己的網域）在 http.conf 裡的區段標記與 upstream 名稱
PLATFORM_BEGIN_MARKER = "# BEGIN skylab-platform"
PLATFORM_END_MARKER = "# END skylab-platform"
PLATFORM_UPSTREAM_NAME = "skylab_platform"


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


@dataclass(frozen=True)
class PlatformEntry:
    """平台入口：SkyLab 主系統自己經 Gateway nginx 對外的那一個網域。"""

    domain: str
    upstream_host: str
    upstream_port: int
    enable_https: bool
    # 網域經 Cloudflare 代理（橘色雲）：連線來源是 Cloudflare，使用者 IP 在 CF-Connecting-IP
    cloudflare_proxy: bool = False

    @property
    def upstream(self) -> str:
        return f"{self.upstream_host}:{self.upstream_port}"


# Cloudflare 代理的出口網段（https://www.cloudflare.com/ips/，2026-10 版）。只有從這些
# 位址連進來的請求才採信 CF-Connecting-IP，直連 Gateway 的人沒辦法自己帶標頭冒充 IP。
# Cloudflare 增加網段時要跟著更新，否則從新網段來的使用者會被記成 Cloudflare 的位址。
CLOUDFLARE_IP_RANGES: tuple[str, ...] = (
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
)
_CLOUDFLARE_REAL_IP_HEADER = "    real_ip_header CF-Connecting-IP;"


def _cloudflare_real_ip_lines() -> list[str]:
    return [
        "    # 經 Cloudflare 代理：從 Cloudflare 來的連線改用 CF-Connecting-IP 當使用者 IP",
        *(f"    set_real_ip_from {cidr};" for cidr in CLOUDFLARE_IP_RANGES),
        _CLOUDFLARE_REAL_IP_HEADER,
    ]


@dataclass(frozen=True)
class CertificatePaths:
    """管理員自備的 HTTPS 憑證（fullchain）與私鑰在 Gateway 上的路徑。"""

    certificate: str
    key: str


def _ssl_certificate_lines(certificate: CertificatePaths | None) -> list[str]:
    """有設定憑證就用它，否則先掛安裝時產生的自簽憑證讓 ``nginx -t`` 能過。"""
    if certificate is not None:
        return [
            f"    ssl_certificate {certificate.certificate};",
            f"    ssl_certificate_key {certificate.key};",
        ]
    return [
        "    # 尚未設定 HTTPS 憑證，暫用自簽憑證",
        f"    ssl_certificate {NGINX_FALLBACK_CERT_PATH};",
        f"    ssl_certificate_key {NGINX_FALLBACK_KEY_PATH};",
    ]


def build_platform_servers(
    entry: PlatformEntry, certificate: CertificatePaths | None
) -> list[str]:
    """平台入口的 server 區塊（夾在 BEGIN／END 標記之間，供狀態檢查讀回）。

    和 VM 網域的差別：
    - 到主系統的連線走 upstream keepalive，整班同時操作時不必每個請求重開 TCP
    - VNC／終端機／教室的 WebSocket 是長連線，逾時放到 1 小時（VM 網域是 5 分鐘）；
      一般 API 的逾時仍由主系統自己的 nginx 決定
    - 上傳與串流回應都不在 Gateway 緩衝：大檔不落地、AI 對話逐字送出
    - ``X-Forwarded-For`` 一律覆寫成連線來源，主系統的 nginx 才能放心拿它還原
      使用者 IP（客戶端自帶的同名標頭不往後傳）
    - 經 Cloudflare 代理時，每個 server 先用 realip 把連線來源換回使用者 IP，
      上面那條規則送出去的才不會是 Cloudflare 的位址
    """
    real_ip = _cloudflare_real_ip_lines() if entry.cloudflare_proxy else []
    proxy_server = [
        "    proxy_request_buffering off;",
        "    proxy_buffering off;",
        "    location / {",
        f"        proxy_pass http://{PLATFORM_UPSTREAM_NAME};",
        "        proxy_http_version 1.1;",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $remote_addr;",
        "        proxy_set_header X-Forwarded-Proto $scheme;",
        "        proxy_set_header Upgrade $http_upgrade;",
        "        proxy_set_header Connection $skylab_platform_connection;",
        "        proxy_connect_timeout 5s;",
        "        proxy_read_timeout 3600s;",
        "        proxy_send_timeout 3600s;",
        "    }",
        "}",
    ]
    lines = [
        PLATFORM_BEGIN_MARKER,
        f"upstream {PLATFORM_UPSTREAM_NAME} {{",
        f"    server {entry.upstream};",
        "    keepalive 32;",
        "}",
        "# 一般請求送空的 Connection 才能保留到主系統的 keep-alive",
        "map $http_upgrade $skylab_platform_connection {",
        "    default upgrade;",
        "    ''      '';",
        "}",
        "server {",
        "    listen 80;",
        f"    server_name {entry.domain};",
        *real_ip,
    ]
    if entry.enable_https:
        lines += [
            "    return 301 https://$host$request_uri;",
            "}",
            "server {",
            "    listen 443 ssl;",
            f"    server_name {entry.domain};",
            *_ssl_certificate_lines(certificate),
            *real_ip,
            *proxy_server,
        ]
    else:
        lines += proxy_server
    lines += [PLATFORM_END_MARKER, ""]
    return lines


def build_http_config(
    rules: list[Any],
    certificate: CertificatePaths | None,
    *,
    platform: PlatformEntry | None = None,
) -> str:
    """從反向代理規則（與平台入口）產生 ``http.conf``。

    所有 HTTPS 站台共用管理員設定的那張憑證；``certificate`` 為 ``None``（還沒設定）
    時先用安裝時產生的自簽憑證頂著，讓 ``nginx -t`` 能過、站台照樣可以連
    （瀏覽器會警告）。憑證沒涵蓋到的網域也一樣會出現瀏覽器警告。
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
    if platform is not None:
        lines += build_platform_servers(platform, certificate)
    for r in rules:
        name = http_server_name(r.vmid, r.domain)
        lines += [f"# {name}", "server {", "    listen 80;", f"    server_name {r.domain};"]
        if r.enable_https:
            lines += ["    return 301 https://$host$request_uri;", "}", ""]
            lines += [
                f"# {name} (https)",
                "server {",
                "    listen 443 ssl;",
                f"    server_name {r.domain};",
                *_ssl_certificate_lines(certificate),
                *_proxy_location(r.vm_ip, r.internal_port),
                "}",
                "",
            ]
        else:
            lines += [*_proxy_location(r.vm_ip, r.internal_port), "}", ""]
    return "\n".join(lines)


# ─── 遠端寫入 ────────────────────────────────────────────────────────────────


def _exec(
    client: Any, command: str, *, timeout: int | None = None
) -> tuple[int, str, str]:
    from app.infrastructure.ssh import exec_command

    return exec_command(client, command, timeout=timeout)


def _sftp_write(client: Any, path: str, content: str) -> None:
    sftp = client.open_sftp()
    try:
        with sftp.open(path, "wb") as handle:
            handle.write(content.encode("utf-8"))
    finally:
        sftp.close()


def lock_config_writes(session: object) -> None:
    """在呼叫端的交易裡取得「改 Gateway nginx 設定」的 advisory lock。

    stream.conf／http.conf 都是從 DB 整份重建：兩個請求同時同步時，較慢的
    那個會拿自己先前讀到的舊規則清單蓋掉別人剛寫上去的。所以「讀規則清單
    → 寫到 Gateway」這段必須序列化：先拿鎖、再讀清單。兩份檔案共用一把鎖，
    同一個流程先後改兩份時（例如刪 VM 先清網域再清轉發）不會互相等待。

    用交易層級的鎖（PgBouncer transaction pooling 下 session 鎖不安全），
    隨呼叫端的 commit／rollback 釋放；同一交易內重複取得是可重入的。
    非 PostgreSQL（單元測試的假 session）時略過。
    """
    get_bind = getattr(session, "get_bind", None)
    if get_bind is None:
        return
    bind = get_bind()
    if bind is None or getattr(bind.dialect, "name", None) != "postgresql":
        return
    from sqlalchemy import text

    session.execute(  # type: ignore[attr-defined]
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _NGINX_CONFIG_LOCK_ID},
    )


def write_validated_config(
    client: Any, path: str, content: str, *, reload: bool = True
) -> None:
    """把設定檔寫到 Gateway：先備份、換上新檔、``nginx -t``，不過就還原。

    nginx 只能整棵設定樹一起驗證，沒辦法單獨檢查一個 include 進來的檔案，
    所以是「先換上再驗」；驗證失敗會把舊檔放回去，執行中的 nginx 從頭到尾
    不受影響（只有 reload 才會重讀設定）。

    暫存檔每次用不同檔名，換檔／驗證／reload 整段在 Gateway 上以 ``flock``
    序列化：兩個寫入同時進行時不會共用同一個暫存檔（A 的 ``mv`` 裝上 B 的
    內容、B 的 ``mv`` 找不到檔案而誤判失敗），也不會在對方驗證途中換掉檔案。
    """
    token = uuid.uuid4().hex
    tmp_path = f"{path}.SkyLab.{token}.tmp"
    prev_path = f"{path}.SkyLab.{token}.prev"
    _sftp_write(client, tmp_path, content)

    quoted = shlex.quote(path)
    quoted_tmp = shlex.quote(tmp_path)
    quoted_prev = shlex.quote(prev_path)
    reload_step = " && systemctl reload nginx 2>&1" if reload else ""
    script = (
        f"if [ -f {quoted} ]; then cp -a {quoted} {quoted_prev}; fi; "
        f"mv -f {quoted_tmp} {quoted} && "
        f"if nginx -t 2>&1; then rm -f {quoted_prev}{reload_step}; "
        f"else if [ -f {quoted_prev} ]; then mv -f {quoted_prev} {quoted}; "
        f"else rm -f {quoted}; fi; exit 1; fi"
    )
    command = (
        f"flock -w {_GATEWAY_NGINX_LOCK_WAIT_SECONDS} "
        f"{shlex.quote(_GATEWAY_NGINX_LOCK)} sh -c {shlex.quote(script)}"
    )
    code, out, err = _exec(client, command)
    if code != 0:
        _exec(client, f"rm -f {quoted_tmp} {quoted_prev}")
        raise ProxmoxError(
            t("gateway.nginxConfigInvalid", path=path, detail=(out + err).strip())
        )


# ─── 管理員自備的憑證 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CertificateInspection:
    """``inspect_certificate`` 的結果；``None`` 代表前一項沒過、沒辦法判斷。"""

    cert_readable: bool
    key_readable: bool
    cert_valid: bool
    key_valid: bool
    key_matches: bool | None
    expires_at: datetime | None
    # subjectAltName 裡的 DNS 名稱（小寫，萬用字元原樣保留）
    dns_names: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        """讀得到、格式正確、私鑰配對：nginx 載得起來。"""
        return (
            self.cert_readable
            and self.key_readable
            and self.cert_valid
            and self.key_valid
            and self.key_matches is True
        )


def certificate_covers(domain: str, dns_names: tuple[str, ...] | list[str]) -> bool:
    """憑證的 DNS 名稱有沒有涵蓋這個網域；萬用字元只涵蓋一層子網域（RFC 6125）。"""
    clean = domain.strip().lower().rstrip(".")
    for raw in dns_names:
        name = raw.strip().lower().rstrip(".")
        if name == clean:
            return True
        if name.startswith("*."):
            head, _, tail = clean.partition(".")
            if head and tail == name[2:]:
                return True
    return False


def build_certificate_inspect_command(cert_path: str, key_path: str) -> str:
    """一條指令檢查憑證與私鑰：讀得到、格式正確、互相配對、到期日、涵蓋的網域。

    輸出 ``key=value`` 逐行（每個 DNS 名稱一行 ``san=``）。私鑰配對比的是兩邊
    公鑰的 DER 雜湊；呼叫端只在兩邊都解析成功時才採信，否則空輸入的雜湊會被
    誤判成相同。subjectAltName 用 ``-text`` 抓而不是 ``-ext``，舊版 openssl 也能用。
    """
    cert = shlex.quote(cert_path)
    key = shlex.quote(key_path)
    return (
        f"c={cert}; k={key}; "
        'if [ -r "$c" ]; then echo cert_readable=1; else echo cert_readable=0; fi; '
        'if [ -r "$k" ]; then echo key_readable=1; else echo key_readable=0; fi; '
        'end=$(openssl x509 -noout -enddate -in "$c" 2>/dev/null) && '
        'echo cert_valid=1 && echo "cert_end=${end#notAfter=}" || echo cert_valid=0; '
        # 有密碼的私鑰 nginx 也載不起來；給空密碼讓它直接失敗，不會停下來等輸入
        'if openssl pkey -in "$k" -passin pass: -noout 2>/dev/null; then echo key_valid=1; '
        "else echo key_valid=0; fi; "
        'cp=$(openssl x509 -noout -pubkey -in "$c" 2>/dev/null | '
        "openssl pkey -pubin -outform der 2>/dev/null | openssl dgst -sha256 2>/dev/null); "
        'kp=$(openssl pkey -in "$k" -passin pass: -pubout -outform der 2>/dev/null | '
        "openssl dgst -sha256 2>/dev/null); "
        'if [ "$cp" = "$kp" ]; then echo key_match=1; else echo key_match=0; fi; '
        'openssl x509 -noout -text -in "$c" 2>/dev/null '
        "| grep -A1 'Subject Alternative Name' | tail -n 1 | tr ',' '\\n' "
        "| sed -n 's/^[[:space:]]*DNS:\\([^[:space:]]*\\).*$/san=\\1/p'"
    )


def _parse_openssl_date(text: str) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def parse_certificate_inspection(output: str) -> CertificateInspection:
    values: dict[str, str] = {}
    dns_names: list[str] = []
    for raw_line in output.splitlines():
        key, sep, value = raw_line.strip().partition("=")
        if not sep:
            continue
        if key == "san":
            name = value.strip().lower()
            if name and name not in dns_names:
                dns_names.append(name)
        else:
            values[key] = value.strip()

    cert_valid = values.get("cert_valid") == "1"
    key_valid = values.get("key_valid") == "1"
    return CertificateInspection(
        cert_readable=values.get("cert_readable") == "1",
        key_readable=values.get("key_readable") == "1",
        cert_valid=cert_valid,
        key_valid=key_valid,
        key_matches=values.get("key_match") == "1" if cert_valid and key_valid else None,
        expires_at=_parse_openssl_date(values.get("cert_end", "")) if cert_valid else None,
        dns_names=tuple(dns_names) if cert_valid else (),
    )


_INSPECT_TIMEOUT_SECONDS = 20


def inspect_certificate(client: Any, cert_path: str, key_path: str) -> CertificateInspection:
    _, out, _ = _exec(
        client,
        build_certificate_inspect_command(cert_path, key_path),
        timeout=_INSPECT_TIMEOUT_SECONDS,
    )
    return parse_certificate_inspection(out)


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
_CERT_KEY_LINE_PATTERN = re.compile(r"^\s*ssl_certificate_key\s+([^;]+);", re.MULTILINE)


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
            # 掛的是自簽備援憑證就代表管理員還沒設定 HTTPS 憑證
            if cert_path and cert_path != NGINX_FALLBACK_CERT_PATH:
                entry["certificate"] = cert_path
                entry["certificate_ready"] = True
            else:
                entry["certificate_ready"] = False
        elif "return 301 https://" in body:
            entry["https"] = True
    return list(servers.values())


_PLATFORM_SECTION_PATTERN = re.compile(
    re.escape(PLATFORM_BEGIN_MARKER) + r"\n(?P<body>.*?)" + re.escape(PLATFORM_END_MARKER),
    re.DOTALL,
)
_UPSTREAM_SERVER_PATTERN = re.compile(r"^\s*server\s+([^;\s{]+);", re.MULTILINE)


def parse_platform_entry(content: str) -> dict[str, Any] | None:
    """從 ``http.conf`` 讀回目前套用中的平台入口；沒有那一段就回 ``None``。

    ``certificate`` 是 nginx 實際引用的憑證路徑；掛的是自簽備援憑證時為 ``None``、
    ``fallback`` 為 True。
    """
    match = _PLATFORM_SECTION_PATTERN.search(content)
    if match is None:
        return None
    body = match.group("body")
    server_name = _SERVER_NAME_PATTERN.search(body)
    upstream = _UPSTREAM_SERVER_PATTERN.search(body)
    https = "listen 443 ssl;" in body
    certificate: str | None = None
    certificate_key: str | None = None
    fallback = False
    if https:
        cert_line = _CERT_LINE_PATTERN.search(body)
        key_line = _CERT_KEY_LINE_PATTERN.search(body)
        cert_path = cert_line.group(1).strip() if cert_line else ""
        fallback = not cert_path or cert_path == NGINX_FALLBACK_CERT_PATH
        if not fallback:
            certificate = cert_path
            certificate_key = key_line.group(1).strip() if key_line else None
    return {
        "domain": server_name.group(1).strip() if server_name else "",
        "upstream": upstream.group(1).strip() if upstream else None,
        "https": https,
        "certificate": certificate,
        "certificate_key": certificate_key,
        "fallback": fallback,
        "cloudflare_proxy": _CLOUDFLARE_REAL_IP_HEADER.strip() in body,
    }


def read_http_config(client: Any) -> str:
    _, content, _ = _exec(client, f"cat {shlex.quote(NGINX_HTTP_CONF_PATH)} 2>/dev/null")
    return content


def list_certificates(client: Any) -> list[dict[str, Any]]:
    _, out, _ = _exec(client, _CERT_LISTING_COMMAND)
    return parse_certificate_listing(out)


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
        items.append({"name": name.strip(), "expires_at": _parse_openssl_date(end_text)})
    return items


def parse_version(output: str) -> str | None:
    match = _VERSION_PATTERN.search(output)
    return match.group(1) if match else None


# 憑證清單：http.conf 實際引用、不是自簽備援的憑證（名稱＝完整路徑）。
# 憑證由管理員自己續期，健康監控靠這份清單提醒快到期
_CERT_LISTING_COMMAND = (
    "for f in $(sed -n 's/^[[:space:]]*ssl_certificate[[:space:]][[:space:]]*\\([^;]*\\);.*/\\1/p' "
    f"{NGINX_HTTP_CONF_PATH} 2>/dev/null | sort -u); do "
    f'[ "$f" = {NGINX_FALLBACK_CERT_PATH} ] && continue; '
    '[ -f "$f" ] || continue; '
    'printf "%s\\t%s\\n" "$f" '
    '"$(openssl x509 -enddate -noout -in "$f" 2>/dev/null | cut -d= -f2)"; '
    "done"
)


def build_health_command(wireguard_unit: str) -> str:
    """健康探測只開一條 SSH 指令：服務狀態、nginx -t 與憑證到期日一次印完。

    輸出是 ``key=value`` 逐行，憑證行是 ``cert=<名稱>\\t<到期日>``。
    """
    return (
        'printf "nginx=%s\\n" "$(systemctl is-active nginx 2>/dev/null)"; '
        f'printf "wireguard=%s\\n" "$(systemctl is-active {shlex.quote(wireguard_unit)} 2>/dev/null)"; '
        'if nginx -t >/dev/null 2>&1; then echo "config=ok"; else echo "config=fail"; fi; '
        f"{_CERT_LISTING_COMMAND} | sed 's/^/cert=/'"
    )


def parse_health_output(output: str) -> dict[str, Any]:
    """把 ``build_health_command`` 的輸出轉成 ``health_policy.gateway_status`` 吃的結構。"""
    result: dict[str, Any] = {
        "nginx": None,
        "wireguard": None,
        "config_valid": None,
        "certificates": [],
    }
    cert_lines: list[str] = []
    for raw_line in output.splitlines():
        key, sep, value = raw_line.strip().partition("=")
        if not sep:
            continue
        if key == "nginx":
            result["nginx"] = value.strip() or "unknown"
        elif key == "wireguard":
            result["wireguard"] = value.strip() or "unknown"
        elif key == "config":
            result["config_valid"] = value.strip() == "ok"
        elif key == "cert":
            cert_lines.append(value)
    result["certificates"] = parse_certificate_listing("\n".join(cert_lines))
    return result


def probe_health(
    client: Any, *, wireguard_unit: str, timeout: int | None = 10
) -> dict[str, Any]:
    """探測 Gateway 健康；``timeout`` 限制 SSH 讀取秒數，避免卡住的主機拖住排程輪次。"""
    _, out, _ = _exec(client, build_health_command(wireguard_unit), timeout=timeout)
    return parse_health_output(out)


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
    "CLOUDFLARE_IP_RANGES",
    "NGINX_CONF_PATH",
    "NGINX_FALLBACK_CERT_PATH",
    "NGINX_FALLBACK_KEY_PATH",
    "NGINX_HTTP_CONF_PATH",
    "NGINX_MANAGED_DIR",
    "NGINX_STREAM_CONF_PATH",
    "PLATFORM_BEGIN_MARKER",
    "PLATFORM_END_MARKER",
    "PLATFORM_UPSTREAM_NAME",
    "CertificateInspection",
    "CertificatePaths",
    "PlatformEntry",
    "build_certificate_inspect_command",
    "build_health_command",
    "build_http_config",
    "build_platform_servers",
    "build_stream_config",
    "certificate_covers",
    "collect_runtime",
    "http_server_name",
    "inspect_certificate",
    "list_certificates",
    "parse_certificate_inspection",
    "parse_certificate_listing",
    "parse_health_output",
    "parse_http_servers",
    "parse_platform_entry",
    "parse_stream_servers",
    "parse_version",
    "probe_health",
    "read_http_config",
    "stream_server_name",
    "write_validated_config",
]
