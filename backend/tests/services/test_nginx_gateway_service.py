"""nginx_gateway_service 的純函式：設定檔產生、憑證規劃、快照解析、遠端寫入指令。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.services.network import nginx_gateway_service as nginx

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


def test_build_http_config_https_rule_redirects_80_and_uses_letsencrypt_cert() -> None:
    rule = _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=8080, enable_https=True)
    out = nginx.build_http_config([rule], {"web.example.com": "example.com"})

    assert "map $http_upgrade $connection_upgrade" in out
    assert "# cc-150-web-example-com\nserver {\n    listen 80;\n    server_name web.example.com;\n    return 301 https://$host$request_uri;" in out
    assert "# cc-150-web-example-com (https)" in out
    assert "    listen 443 ssl;" in out
    assert "    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;" in out
    assert "    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;" in out
    assert "        proxy_pass http://10.10.0.5:8080;" in out
    assert "proxy_set_header Upgrade $http_upgrade;" in out


def test_build_http_config_falls_back_to_self_signed_when_cert_missing() -> None:
    rule = _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=80, enable_https=True)
    out = nginx.build_http_config([rule], {"web.example.com": None})

    assert f"    ssl_certificate {nginx.NGINX_FALLBACK_CERT_PATH};" in out
    assert f"    ssl_certificate_key {nginx.NGINX_FALLBACK_KEY_PATH};" in out
    assert "憑證尚未簽發" in out
    assert "/etc/letsencrypt/live" not in out


def test_build_http_config_http_only_rule_proxies_on_80() -> None:
    rule = _ProxyRule(vmid=3, domain="plain.example.com", vm_ip="10.10.0.3", internal_port=3000, enable_https=False)
    out = nginx.build_http_config([rule], {})

    assert out.count("server {") == 1
    assert "listen 443" not in out
    assert "return 301" not in out
    assert "        proxy_pass http://10.10.0.3:3000;" in out


# ─── plan_certificate ────────────────────────────────────────────────────────


def test_plan_certificate_single_label_shares_zone_wildcard() -> None:
    assert nginx.plan_certificate("web.example.com", "example.com") == (
        "example.com",
        ["example.com", "*.example.com"],
    )
    assert nginx.plan_certificate("example.com", "example.com") == (
        "example.com",
        ["example.com", "*.example.com"],
    )


def test_plan_certificate_multi_label_or_unknown_zone_issues_per_domain() -> None:
    assert nginx.plan_certificate("a.b.example.com", "example.com") == (
        "a.b.example.com",
        ["a.b.example.com"],
    )
    assert nginx.plan_certificate("web.example.com", None) == (
        "web.example.com",
        ["web.example.com"],
    )


def test_plan_certificate_does_not_match_unrelated_suffix() -> None:
    # notexample.com 不是 example.com 底下的子網域
    assert nginx.plan_certificate("notexample.com", "example.com") == (
        "notexample.com",
        ["notexample.com"],
    )


# ─── 快照解析 ────────────────────────────────────────────────────────────────


def test_parse_http_servers_round_trips_generated_config() -> None:
    rules = [
        _ProxyRule(vmid=150, domain="web.example.com", vm_ip="10.10.0.5", internal_port=8080, enable_https=True),
        _ProxyRule(vmid=151, domain="pending.example.com", vm_ip="10.10.0.6", internal_port=80, enable_https=True),
        _ProxyRule(vmid=3, domain="plain.example.com", vm_ip="10.10.0.3", internal_port=3000, enable_https=False),
    ]
    content = nginx.build_http_config(
        rules, {"web.example.com": "example.com", "pending.example.com": None}
    )

    servers = {item["name"]: item for item in nginx.parse_http_servers(content)}

    assert servers["cc-150-web-example-com"] == {
        "name": "cc-150-web-example-com",
        "vmid": 150,
        "domain": "web.example.com",
        "upstream": "http://10.10.0.5:8080",
        "https": True,
        "certificate": "example.com",
        "certificate_ready": True,
    }
    assert servers["cc-151-pending-example-com"]["certificate_ready"] is False
    assert servers["cc-151-pending-example-com"]["certificate"] is None
    assert servers["cc-3-plain-example-com"] == {
        "name": "cc-3-plain-example-com",
        "vmid": 3,
        "domain": "plain.example.com",
        "upstream": "http://10.10.0.3:3000",
        "https": False,
        "certificate": None,
        "certificate_ready": None,
    }


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
    listing = "example.com\tJan  5 12:00:00 2027 GMT\nbroken.example.com\t\n"
    items = nginx.parse_certificate_listing(listing)

    assert items[0] == {
        "name": "example.com",
        "expires_at": datetime(2027, 1, 5, 12, 0, 0, tzinfo=timezone.utc),
    }
    assert items[1] == {"name": "broken.example.com", "expires_at": None}
    assert nginx.parse_version("nginx version: nginx/1.26.3") == "1.26.3"
    assert nginx.parse_version("bash: nginx: command not found") is None


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


def test_write_validated_config_validates_then_reloads(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _patch_exec(monkeypatch, [(0, "syntax is ok", "")], client)

    nginx.write_validated_config(client, nginx.NGINX_STREAM_CONF_PATH, "server {}\n")

    assert client.files == {f"{nginx.NGINX_STREAM_CONF_PATH}.SkyLab.tmp": b"server {}\n"}
    command = client.commands[0]
    assert "nginx -t" in command
    assert "systemctl reload nginx" in command
    # 先備份、驗證失敗才還原：兩條路徑都要在同一條指令裡
    assert f"cp -a {nginx.NGINX_STREAM_CONF_PATH} {nginx.NGINX_STREAM_CONF_PATH}.SkyLab.prev" in command
    assert f"mv -f {nginx.NGINX_STREAM_CONF_PATH}.SkyLab.prev {nginx.NGINX_STREAM_CONF_PATH}" in command


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


def test_issue_certificate_builds_certbot_command_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch_exec(monkeypatch, [(0, "Successfully received certificate", ""), (1, "DNS problem", "")], client)

    assert nginx.issue_certificate(
        client, "example.com", ["example.com", "*.example.com"], acme_email="ops@example.com"
    )
    command = client.commands[0]
    assert command.startswith("certbot certonly --non-interactive --agree-tos")
    assert "--dns-cloudflare-credentials /etc/letsencrypt/skylab-cloudflare.ini" in command
    assert "--cert-name example.com -d example.com -d '*.example.com'" in command
    assert "--email ops@example.com" in command

    assert not nginx.issue_certificate(client, "bad.example.com", ["bad.example.com"], acme_email="ops@example.com")


def test_issue_certificate_rejects_unsafe_names() -> None:
    with pytest.raises(BadRequestError):
        nginx.issue_certificate(_FakeClient(), "x; rm -rf /", ["x; rm -rf /"], acme_email="ops@example.com")


def test_write_certbot_credentials_rejects_multiline_token() -> None:
    with pytest.raises(BadRequestError):
        nginx.write_certbot_credentials(_FakeClient(), "line1\nline2")


def test_ensure_certificates_only_issues_missing_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    issued: list[str] = []
    monkeypatch.setattr(nginx, "certificate_exists", lambda client, name: name == "have.example.com")
    monkeypatch.setattr(
        nginx,
        "issue_certificate",
        lambda client, name, domains, *, acme_email: issued.append(name) or name != "fail.example.com",
    )

    ready = nginx.ensure_certificates(
        object(),
        {
            "have.example.com": ["have.example.com"],
            "new.example.com": ["new.example.com"],
            "fail.example.com": ["fail.example.com"],
        },
        acme_email="ops@example.com",
    )

    assert issued == ["new.example.com", "fail.example.com"]
    assert ready == {"have.example.com", "new.example.com"}
