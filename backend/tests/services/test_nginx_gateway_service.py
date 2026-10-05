"""nginx_gateway_service 的純函式：設定檔產生、憑證檢查、快照解析、遠端寫入指令。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from app.exceptions import ProxmoxError
from app.services.network import nginx_gateway_service as nginx

_CERT = nginx.CertificatePaths(
    certificate="/etc/ssl/skylab/fullchain.pem", key="/etc/ssl/skylab/privkey.pem"
)

# ─── build_stream_config ─────────────────────────────────────────────────────


@dataclass
class _NatRule:
    vmid: int
    external_port: int
    internal_port: int
    protocol: str
    vm_ip: str


def test_build_stream_config_without_rules_is_header_only() -> None:
    out = nginx.build_stream_config([])
    assert "server {" not in out
    assert out.startswith("# SkyLab")


def test_build_stream_config_tcp_rule_listens_and_proxies() -> None:
    rule = _NatRule(vmid=101, external_port=30022, internal_port=22, protocol="tcp", vm_ip="10.10.0.5")
    out = nginx.build_stream_config([rule])
    assert "# cc-101-30022-tcp" in out
    assert "    listen 30022;" in out
    assert "    proxy_pass 10.10.0.5:22;" in out


def test_build_stream_config_udp_rule_uses_udp_listen() -> None:
    """haproxy 做不到的 UDP 轉發，nginx stream 用 listen ... udp 就好。"""
    rule = _NatRule(vmid=7, external_port=30053, internal_port=53, protocol="udp", vm_ip="10.10.0.9")
    out = nginx.build_stream_config([rule])
    assert "# cc-7-30053-udp" in out
    assert "    listen 30053 udp;" in out


def test_build_stream_config_same_port_tcp_and_udp_get_separate_servers() -> None:
    rules = [
        _NatRule(vmid=1, external_port=30500, internal_port=500, protocol="tcp", vm_ip="10.10.0.1"),
        _NatRule(vmid=1, external_port=30500, internal_port=500, protocol="udp", vm_ip="10.10.0.1"),
    ]
    out = nginx.build_stream_config(rules)
    assert out.count("server {") == 2
    assert "listen 30500;" in out
    assert "listen 30500 udp;" in out


# ─── build_http_config ───────────────────────────────────────────────────────


@dataclass
class _ProxyRule:
    vmid: int
    domain: str
    vm_ip: str
    internal_port: int
    enable_https: bool


def test_build_http_config_https_rule_redirects_80_and_uses_admin_certificate() -> None:
    rule = _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=8080, enable_https=True)
    out = nginx.build_http_config([rule], _CERT)

    assert "map $http_upgrade $connection_upgrade" in out
    assert "# cc-150-web-example-com\nserver {\n    listen 80;\n    server_name web.example.com;\n    return 301 https://$host$request_uri;" in out
    assert "# cc-150-web-example-com (https)" in out
    assert "    listen 443 ssl;" in out
    assert "    ssl_certificate /etc/ssl/skylab/fullchain.pem;" in out
    assert "    ssl_certificate_key /etc/ssl/skylab/privkey.pem;" in out
    assert "        proxy_pass http://10.10.0.5:8080;" in out
    assert "proxy_set_header Upgrade $http_upgrade;" in out
    assert "letsencrypt" not in out


def test_build_http_config_falls_back_to_self_signed_when_no_certificate() -> None:
    rule = _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=80, enable_https=True)
    out = nginx.build_http_config([rule], None)

    assert f"    ssl_certificate {nginx.NGINX_FALLBACK_CERT_PATH};" in out
    assert f"    ssl_certificate_key {nginx.NGINX_FALLBACK_KEY_PATH};" in out
    assert "尚未設定 HTTPS 憑證" in out


def test_build_http_config_all_https_sites_share_one_certificate() -> None:
    rules = [
        _ProxyRule(vmid=1, domain="a.example.com", vm_ip="10.10.0.1", internal_port=80, enable_https=True),
        _ProxyRule(vmid=2, domain="b.other.org", vm_ip="10.10.0.2", internal_port=80, enable_https=True),
    ]
    out = nginx.build_http_config(rules, _CERT, platform=_platform())

    assert out.count("    ssl_certificate /etc/ssl/skylab/fullchain.pem;") == 3


def test_build_http_config_http_only_rule_proxies_on_80() -> None:
    rule = _ProxyRule(vmid=3, domain="plain.example.com", vm_ip="10.10.0.3", internal_port=3000, enable_https=False)
    out = nginx.build_http_config([rule], _CERT)

    assert out.count("server {") == 1
    assert "listen 443" not in out
    assert "return 301" not in out
    assert "ssl_certificate" not in out
    assert "        proxy_pass http://10.10.0.3:3000;" in out


# ─── 平台入口 ────────────────────────────────────────────────────────────────


def _platform(enable_https: bool = True, cloudflare_proxy: bool = False) -> nginx.PlatformEntry:
    return nginx.PlatformEntry(
        domain="skylab.example.com",
        upstream_host="192.168.100.20",
        upstream_port=8082,
        enable_https=enable_https,
        cloudflare_proxy=cloudflare_proxy,
    )


def test_build_http_config_without_platform_has_no_platform_section() -> None:
    out = nginx.build_http_config([], None)
    assert nginx.PLATFORM_BEGIN_MARKER not in out
    assert nginx.parse_platform_entry(out) is None


def test_build_http_config_platform_https_block() -> None:
    out = nginx.build_http_config([], _CERT, platform=_platform())

    assert "upstream skylab_platform {\n    server 192.168.100.20:8082;\n    keepalive 32;" in out
    assert "    server_name skylab.example.com;\n    return 301 https://$host$request_uri;" in out
    assert "    ssl_certificate /etc/ssl/skylab/fullchain.pem;" in out
    assert "        proxy_pass http://skylab_platform;" in out
    # VNC／終端機的 WebSocket 是長連線，不能沿用 VM 網域的 5 分鐘逾時
    assert "        proxy_read_timeout 3600s;" in out
    # 上傳與串流都不在 Gateway 緩衝
    assert "    proxy_request_buffering off;" in out
    assert "    proxy_buffering off;" in out
    # 主系統 nginx 要拿這個標頭還原使用者 IP，所以不沿用客戶端自帶的值
    assert "        proxy_set_header X-Forwarded-For $remote_addr;" in out
    assert "$proxy_add_x_forwarded_for" not in out.split(nginx.PLATFORM_END_MARKER)[0]
    # 沒經 Cloudflare 代理就不採信 CF-Connecting-IP
    assert "real_ip_header" not in out
    assert out.count("{") == out.count("}")


def test_build_http_config_platform_behind_cloudflare_restores_client_ip() -> None:
    out = nginx.build_http_config([], _CERT, platform=_platform(cloudflare_proxy=True))
    section = out.split(nginx.PLATFORM_END_MARKER)[0]

    # 80（轉址）與 443 兩個 server 都要換回使用者 IP，只採信 Cloudflare 的網段
    assert section.count("    real_ip_header CF-Connecting-IP;") == 2
    for cidr in nginx.CLOUDFLARE_IP_RANGES:
        assert section.count(f"    set_real_ip_from {cidr};") == 2
    assert "set_real_ip_from 0.0.0.0/0" not in section
    # realip 改的是 $remote_addr，送給主系統的仍是這一個值
    assert "        proxy_set_header X-Forwarded-For $remote_addr;" in section
    assert out.count("{") == out.count("}")

    parsed = nginx.parse_platform_entry(out)
    assert parsed is not None
    assert parsed["cloudflare_proxy"] is True


def test_build_http_config_platform_http_only_proxies_on_80() -> None:
    out = nginx.build_http_config([], _CERT, platform=_platform(enable_https=False))

    assert "listen 443" not in out
    assert "return 301" not in out
    assert "        proxy_pass http://skylab_platform;" in out
    assert out.count("{") == out.count("}")


def test_build_http_config_platform_falls_back_to_self_signed() -> None:
    out = nginx.build_http_config([], None, platform=_platform())

    assert f"    ssl_certificate {nginx.NGINX_FALLBACK_CERT_PATH};" in out
    parsed = nginx.parse_platform_entry(out)
    assert parsed is not None
    assert parsed["fallback"] is True
    assert parsed["certificate"] is None


def test_parse_platform_entry_round_trips_and_leaves_vm_rules_alone() -> None:
    rule = _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=8080, enable_https=True)
    out = nginx.build_http_config([rule], _CERT, platform=_platform())

    assert nginx.parse_platform_entry(out) == {
        "domain": "skylab.example.com",
        "upstream": "192.168.100.20:8082",
        "https": True,
        "certificate": "/etc/ssl/skylab/fullchain.pem",
        "certificate_key": "/etc/ssl/skylab/privkey.pem",
        "fallback": False,
        "cloudflare_proxy": False,
    }
    # 平台入口不是 VM 規則，不能混進網域管理頁的執行期快照
    assert [item["name"] for item in nginx.parse_http_servers(out)] == ["cc-150-web-example-com"]


# ─── 憑證涵蓋範圍 ────────────────────────────────────────────────────────────


def test_certificate_covers_exact_and_single_level_wildcard() -> None:
    names = ("example.com", "*.example.com")
    assert nginx.certificate_covers("example.com", names)
    assert nginx.certificate_covers("Web.Example.com.", names)
    # 萬用字元只涵蓋一層子網域
    assert not nginx.certificate_covers("a.b.example.com", names)
    # notexample.com 不是 example.com 底下的子網域
    assert not nginx.certificate_covers("notexample.com", names)
    assert not nginx.certificate_covers("example.com", ())


# ─── 憑證檢查 ────────────────────────────────────────────────────────────────


def test_certificate_inspect_command_quotes_paths_and_lists_san() -> None:
    command = nginx.build_certificate_inspect_command(
        "/etc/ssl/skylab/fullchain.pem", "/etc/ssl/skylab/priv key.pem"
    )
    assert "c=/etc/ssl/skylab/fullchain.pem;" in command
    assert "k='/etc/ssl/skylab/priv key.pem';" in command
    assert "openssl x509 -noout -enddate" in command
    assert "Subject Alternative Name" in command
    assert "certbot" not in command


def test_parse_certificate_inspection_good_certificate() -> None:
    output = "\n".join(
        [
            "cert_readable=1",
            "key_readable=1",
            "cert_valid=1",
            "cert_end=Jan  5 12:00:00 2027 GMT",
            "key_valid=1",
            "key_match=1",
            "san=example.com",
            "san=*.Example.com",
            "san=example.com",
        ]
    )
    result = nginx.parse_certificate_inspection(output)

    assert result.usable
    assert result.key_matches is True
    assert result.expires_at == datetime(2027, 1, 5, 12, 0, 0, tzinfo=timezone.utc)
    assert result.dns_names == ("example.com", "*.example.com")


def test_parse_certificate_inspection_does_not_trust_key_match_when_parsing_failed() -> None:
    # 兩邊都解析失敗時，空輸入的雜湊相同，key_match 會印 1，不能採信
    output = "cert_readable=1\nkey_readable=1\ncert_valid=0\nkey_valid=0\nkey_match=1\n"
    result = nginx.parse_certificate_inspection(output)

    assert not result.usable
    assert result.key_matches is None
    assert result.expires_at is None
    assert result.dns_names == ()


def test_parse_certificate_inspection_key_mismatch() -> None:
    output = "cert_readable=1\nkey_readable=1\ncert_valid=1\ncert_end=Jan  5 12:00:00 2027 GMT\nkey_valid=1\nkey_match=0\n"
    result = nginx.parse_certificate_inspection(output)

    assert result.key_matches is False
    assert not result.usable


# ─── 快照解析 ────────────────────────────────────────────────────────────────


def test_parse_http_servers_round_trips_generated_config() -> None:
    rules = [
        _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=8080, enable_https=True),
        _ProxyRule(vmid=3, domain="plain.example.com", vm_ip="10.10.0.3", internal_port=3000, enable_https=False),
    ]
    content = nginx.build_http_config(rules, _CERT)

    servers = {item["name"]: item for item in nginx.parse_http_servers(content)}

    assert servers["cc-150-web-example-com"] == {
        "name": "cc-150-web-example-com",
        "vmid": 150,
        "domain": "web.example.com",
        "upstream": "http://10.10.0.5:8080",
        "https": True,
        "certificate": "/etc/ssl/skylab/fullchain.pem",
        "certificate_ready": True,
    }
    assert servers["cc-3-plain-example-com"] == {
        "name": "cc-3-plain-example-com",
        "vmid": 3,
        "domain": "plain.example.com",
        "upstream": "http://10.10.0.3:3000",
        "https": False,
        "certificate": None,
        "certificate_ready": None,
    }


def test_parse_http_servers_marks_self_signed_fallback_not_ready() -> None:
    rule = _ProxyRule(vmid=151, domain="pending.example.com", vm_ip="10.10.0.6", internal_port=80, enable_https=True)
    servers = nginx.parse_http_servers(nginx.build_http_config([rule], None))

    assert servers[0]["certificate_ready"] is False
    assert servers[0]["certificate"] is None


def test_parse_stream_servers_round_trips_generated_config() -> None:
    content = nginx.build_stream_config(
        [
            _NatRule(vmid=101, external_port=30022, internal_port=22, protocol="tcp", vm_ip="10.10.0.5"),
            _NatRule(vmid=7, external_port=30053, internal_port=53, protocol="udp", vm_ip="10.10.0.9"),
        ]
    )

    assert nginx.parse_stream_servers(content) == [
        {"name": "cc-101-30022-tcp", "vmid": 101, "listen": 30022, "protocol": "tcp", "upstream": "10.10.0.5:22"},
        {"name": "cc-7-30053-udp", "vmid": 7, "listen": 30053, "protocol": "udp", "upstream": "10.10.0.9:53"},
    ]


def test_parse_certificate_listing_and_version() -> None:
    listing = "/etc/ssl/skylab/fullchain.pem\tJan  5 12:00:00 2027 GMT\n/etc/ssl/broken.pem\t\n"
    items = nginx.parse_certificate_listing(listing)

    assert items[0] == {
        "name": "/etc/ssl/skylab/fullchain.pem",
        "expires_at": datetime(2027, 1, 5, 12, 0, 0, tzinfo=timezone.utc),
    }
    assert items[1] == {"name": "/etc/ssl/broken.pem", "expires_at": None}
    assert nginx.parse_version("nginx version: nginx/1.26.3") == "1.26.3"
    assert nginx.parse_version("bash: nginx: command not found") is None


def test_health_command_lists_certificates_referenced_by_http_conf() -> None:
    command = nginx.build_health_command("wg-quick@wg0")
    assert nginx.NGINX_HTTP_CONF_PATH in command
    # 自簽備援憑證不列入到期提醒
    assert nginx.NGINX_FALLBACK_CERT_PATH in command
    assert "letsencrypt" not in command


# ─── 遠端寫入 ────────────────────────────────────────────────────────────────


class _FakeSftpFile:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path

    def __enter__(self) -> _FakeSftpFile:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, data: bytes) -> None:
        self._store[self._path] = data


class _FakeSftp:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def open(self, path: str, mode: str) -> _FakeSftpFile:
        return _FakeSftpFile(self._store, path)

    def close(self) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []

    def open_sftp(self) -> _FakeSftp:
        return _FakeSftp(self.files)


def _patch_exec(monkeypatch: pytest.MonkeyPatch, results: list[tuple[int, str, str]], client: _FakeClient) -> None:
    def fake_exec(_client: Any, command: str) -> tuple[int, str, str]:
        client.commands.append(command)
        return results.pop(0)

    monkeypatch.setattr(nginx, "_exec", fake_exec)


def _fixed_token(monkeypatch: pytest.MonkeyPatch, token: str = "abc123") -> None:
    class _Uuid:
        hex = token

    monkeypatch.setattr(nginx.uuid, "uuid4", lambda: _Uuid())


def test_write_validated_config_validates_then_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_exec(monkeypatch, [(0, "syntax is ok", "")], client)
    _fixed_token(monkeypatch)

    nginx.write_validated_config(client, nginx.NGINX_STREAM_CONF_PATH, "server {}\n")

    assert client.files == {
        f"{nginx.NGINX_STREAM_CONF_PATH}.SkyLab.abc123.tmp": b"server {}\n"
    }
    command = client.commands[0]
    assert "nginx -t" in command
    assert "systemctl reload nginx" in command
    # 先備份、驗證失敗才還原：兩條路徑都要在同一條指令裡
    prev = f"{nginx.NGINX_STREAM_CONF_PATH}.SkyLab.abc123.prev"
    assert f"cp -a {nginx.NGINX_STREAM_CONF_PATH} {prev}" in command
    assert f"mv -f {prev} {nginx.NGINX_STREAM_CONF_PATH}" in command


def test_write_validated_config_without_reload_skips_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_exec(monkeypatch, [(0, "", "")], client)

    nginx.write_validated_config(client, nginx.NGINX_CONF_PATH, "events {}\n", reload=False)

    assert "systemctl reload nginx" not in client.commands[0]


def test_write_validated_config_raises_with_nginx_output_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_exec(monkeypatch, [(1, "nginx: [emerg] unknown directive", ""), (0, "", "")], client)

    with pytest.raises(ProxmoxError, match="unknown directive"):
        nginx.write_validated_config(client, nginx.NGINX_HTTP_CONF_PATH, "bogus\n")

    # 失敗後清掉暫存與備份
    assert client.commands[-1].startswith("rm -f ")
