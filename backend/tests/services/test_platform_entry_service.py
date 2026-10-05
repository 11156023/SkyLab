"""平台入口（主系統經 Gateway nginx 對外）：驗證、上游探測、儲存與還原、狀態比對。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.models.platform_entry_config import PlatformEntryConfig
from app.repositories import platform_entry as repo
from app.repositories import reverse_proxy as rp_repo
from app.schemas.cloudflare import CloudflareConfigPublic, CloudflareDNSRecordPublic
from app.schemas.gateway import PlatformEntryUpdate
from app.services.network import (
    cloudflare_service,
    gateway_certificate_service,
    gateway_service,
    platform_entry_service,
    reverse_proxy_service,
)
from app.services.network import nginx_gateway_service as nginx

_CERT = nginx.CertificatePaths(
    certificate="/etc/ssl/skylab/fullchain.pem", key="/etc/ssl/skylab/privkey.pem"
)


def _inspect_output(*sans: str, end: str = "Jan  5 12:00:00 2027 GMT") -> str:
    lines = [
        "cert_readable=1",
        "key_readable=1",
        "cert_valid=1",
        f"cert_end={end}",
        "key_valid=1",
        "key_match=1",
        *(f"san={name}" for name in sans),
    ]
    return "\n".join(lines) + "\n"


class _Session:
    """只會被問 singleton 的假 session。"""

    def __init__(self, config: PlatformEntryConfig | None = None) -> None:
        self.config = config
        self.rolled_back = False

    def get(self, model: type, _ident: int) -> Any:
        return self.config if model is PlatformEntryConfig else None

    def rollback(self) -> None:
        self.rolled_back = True


def _config(**overrides: Any) -> PlatformEntryConfig:
    values: dict[str, Any] = {
        "id": 1,
        "enabled": True,
        "domain": "skylab.example.com",
        "upstream_host": "192.168.100.20",
        "upstream_port": 8082,
        "enable_https": False,
    }
    values.update(overrides)
    return PlatformEntryConfig(**values)


class _Harness:
    """把 save_config／get_status 會碰到的外部依賴換掉，並記錄發生了什麼。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.probe_result: tuple[int, str, str] = (0, "", "")
        self.inspect_output = _inspect_output("example.com", "*.example.com")
        self.certificate: nginx.CertificatePaths | None = _CERT
        self.commands: list[str] = []
        self.upserts: list[dict[str, Any]] = []
        self.syncs = 0
        self.sync_error: Exception | None = None
        self.http_conf = ""
        self.domain_taken = False

        def fake_upsert(session: _Session, **values: Any) -> PlatformEntryConfig:
            self.upserts.append(values)
            # 真的 repo 是就地更新同一列，DNS 紀錄欄位不會被洗掉
            old = session.config
            session.config = PlatformEntryConfig(
                id=1,
                dns_zone_id=old.dns_zone_id if old else "",
                dns_record_id=old.dns_record_id if old else "",
                **values,
            )
            return session.config

        @contextmanager
        def fake_client(_session: object) -> Iterator[object]:
            yield object()

        def fake_exec(_client: Any, command: str, **_kwargs: Any) -> tuple[int, str, str]:
            self.commands.append(command)
            if command.startswith("cat "):
                return 0, self.http_conf, ""
            if "Subject Alternative Name" in command:
                return 0, self.inspect_output, ""
            return self.probe_result

        def fake_sync(_session: object) -> None:
            self.syncs += 1
            if self.sync_error is not None:
                raise self.sync_error

        monkeypatch.setattr(repo, "upsert_platform_entry_config", fake_upsert)
        monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_a, **_k: self.domain_taken)
        monkeypatch.setattr(gateway_service, "gateway_client_or_502", fake_client)
        monkeypatch.setattr(platform_entry_service, "_gateway_host", lambda _s: (True, "192.168.100.2"))
        monkeypatch.setattr(gateway_certificate_service, "load_paths", lambda _s: self.certificate)
        monkeypatch.setattr(nginx, "_exec", fake_exec)
        monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", fake_sync)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> _Harness:
    return _Harness(monkeypatch)


# ─── 驗證 ────────────────────────────────────────────────────────────────────


def test_normalize_domain_lowercases_and_rejects_garbage() -> None:
    assert platform_entry_service.normalize_domain(" SkyLab.Example.com. ") == "skylab.example.com"
    assert platform_entry_service.normalize_domain("") == ""
    with pytest.raises(BadRequestError):
        platform_entry_service.normalize_domain("skylab")
    with pytest.raises(BadRequestError):
        platform_entry_service.normalize_domain("bad domain.example.com")


def test_normalize_upstream_host_accepts_ipv4_and_hostnames_only() -> None:
    assert platform_entry_service.normalize_upstream_host(" 192.168.100.20 ") == "192.168.100.20"
    assert platform_entry_service.normalize_upstream_host("Deploy-Host") == "deploy-host"
    assert platform_entry_service.normalize_upstream_host("deploy.lab.internal") == "deploy.lab.internal"
    # 這個值會寫進 nginx 設定與 Gateway 上的指令，不能夾帶其他字元
    for bad in ("10.0.0.1; rm -rf /", "host:8082", "$(id)", "fe80::1", "a b", "300.1.1.1"):
        with pytest.raises(BadRequestError):
            platform_entry_service.normalize_upstream_host(bad)


def test_probe_command_targets_nginx_health_with_timeout() -> None:
    command = platform_entry_service.build_upstream_probe_command("192.168.100.20", 8082)
    assert "curl -fsS -m 5 -o /dev/null http://192.168.100.20:8082/nginx-health" in command
    assert "wget -q -T 5" in command


# ─── 讀取 ────────────────────────────────────────────────────────────────────


def test_load_entry_only_when_enabled_and_complete() -> None:
    assert platform_entry_service.load_entry(_Session()) is None
    assert platform_entry_service.load_entry(_Session(_config(enabled=False))) is None
    assert platform_entry_service.load_entry(_Session(_config(upstream_host=""))) is None

    entry = platform_entry_service.load_entry(_Session(_config()))
    assert entry == nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, False)


def test_load_entry_ignores_sessions_that_return_other_objects() -> None:
    """別的測試會塞簡化的假 session 進同步流程，不能因此把假物件當成平台入口。"""
    session = SimpleNamespace(get=lambda *_a, **_k: SimpleNamespace(vmid=101))
    assert platform_entry_service.load_entry(session) is None
    assert platform_entry_service.is_platform_domain(session, "skylab.example.com") is False
    assert platform_entry_service.load_entry(SimpleNamespace()) is None


def test_platform_domain_stays_reserved_while_disabled() -> None:
    session = _Session(_config(enabled=False))
    assert platform_entry_service.is_platform_domain(session, "SkyLab.example.com.") is True
    assert platform_entry_service.is_platform_domain(session, "other.example.com") is False


def test_check_domain_availability_rejects_platform_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_a, **_k: False)

    result = reverse_proxy_service.check_domain_availability(
        _Session(_config()), "skylab.example.com"
    )

    assert result.available is False
    assert result.reason == "system"


# ─── 儲存 ────────────────────────────────────────────────────────────────────


def _update(**overrides: Any) -> PlatformEntryUpdate:
    values: dict[str, Any] = {
        "enabled": True,
        "domain": "SkyLab.example.com",
        "upstream_host": "192.168.100.20",
        "upstream_port": 8082,
        "enable_https": False,
    }
    values.update(overrides)
    return PlatformEntryUpdate(**values)


def test_save_probes_upstream_then_persists_and_syncs(harness: _Harness) -> None:
    session = _Session()

    result = platform_entry_service.save_config(session, _update())

    assert "/nginx-health" in harness.commands[0]
    assert harness.upserts == [
        {
            "enabled": True,
            "domain": "skylab.example.com",
            "upstream_host": "192.168.100.20",
            "upstream_port": 8082,
            "enable_https": False,
            "dns_proxied": False,
        }
    ]
    assert harness.syncs == 1
    assert result.enabled is True
    assert result.domain == "skylab.example.com"
    assert result.gateway_host == "192.168.100.2"


def test_save_refuses_when_gateway_cannot_reach_upstream(harness: _Harness) -> None:
    harness.probe_result = (7, "curl: (7) Failed to connect", "")

    with pytest.raises(BadRequestError, match="Failed to connect"):
        platform_entry_service.save_config(_Session(), _update())

    # 主系統自己的入口指錯位址會讓管理介面進不來，所以連不到就不存也不同步
    assert harness.upserts == []
    assert harness.syncs == 0


def test_save_restores_previous_config_when_sync_fails(harness: _Harness) -> None:
    harness.sync_error = ProxmoxError("nginx -t failed")
    session = _Session(_config(domain="old.example.com", upstream_host="192.168.100.9"))

    with pytest.raises(ProxmoxError):
        platform_entry_service.save_config(session, _update())

    assert session.rolled_back is True
    assert [u["domain"] for u in harness.upserts] == ["skylab.example.com", "old.example.com"]
    assert session.config is not None
    assert session.config.upstream_host == "192.168.100.9"


def test_save_disabled_draft_touches_neither_gateway_nor_nginx(harness: _Harness) -> None:
    result = platform_entry_service.save_config(
        _Session(), _update(enabled=False, upstream_host="")
    )

    assert harness.commands == []
    assert harness.syncs == 0
    assert result.enabled is False
    assert result.domain == "skylab.example.com"


def test_save_disabling_an_active_entry_resyncs_without_probe(harness: _Harness) -> None:
    session = _Session(_config())

    platform_entry_service.save_config(session, _update(enabled=False))

    # 停用要把 http.conf 裡的區塊拿掉；上游通不通這時候不重要
    assert harness.commands == []
    assert harness.syncs == 1


def test_save_requires_domain_and_upstream_when_enabled(harness: _Harness) -> None:
    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update(domain=""))
    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update(upstream_host=""))
    assert harness.upserts == []


def test_save_rejects_domain_already_published_for_a_vm(harness: _Harness) -> None:
    harness.domain_taken = True
    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update())
    assert harness.upserts == []


def test_save_https_requires_gateway_certificate(harness: _Harness) -> None:
    harness.certificate = None

    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []
    assert harness.syncs == 0


def test_save_https_checks_certificate_then_probes_and_persists(harness: _Harness) -> None:
    result = platform_entry_service.save_config(_Session(), _update(enable_https=True))

    assert "Subject Alternative Name" in harness.commands[0]
    assert "/nginx-health" in harness.commands[1]
    assert harness.upserts[0]["enable_https"] is True
    assert harness.syncs == 1
    assert result.certificate_configured is True


def test_save_https_rejects_certificate_not_covering_platform_domain(harness: _Harness) -> None:
    harness.inspect_output = _inspect_output("other.org", "*.other.org")

    with pytest.raises(BadRequestError, match="skylab.example.com"):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []


def test_save_https_rejects_expired_certificate(harness: _Harness) -> None:
    harness.inspect_output = _inspect_output(
        "*.example.com", end="Jan  5 12:00:00 2020 GMT"
    )

    with pytest.raises(BadRequestError, match="2020-01-05"):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []


def test_save_https_rejects_mismatched_key(harness: _Harness) -> None:
    harness.inspect_output = _inspect_output("*.example.com").replace(
        "key_match=1", "key_match=0"
    )

    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []


# ─── 狀態 ────────────────────────────────────────────────────────────────────


def test_status_reports_applied_when_gateway_matches_saved_config(harness: _Harness) -> None:
    config = _config(enable_https=True)
    harness.http_conf = nginx.build_http_config(
        [],
        _CERT,
        platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, True),
    )

    status = platform_entry_service.get_status(
        _Session(config), observed_client_ip="203.0.113.7", observed_scheme="https"
    )

    assert status.applied is True
    assert status.applied_upstream == "192.168.100.20:8082"
    assert status.certificate == "/etc/ssl/skylab/fullchain.pem"
    assert status.certificate_ready is True
    assert status.certificate_matches_domain is True
    assert status.certificate_expires_at is not None
    assert status.upstream_reachable is True
    assert status.observed_client_ip == "203.0.113.7"
    assert status.observed_scheme == "https"


def test_status_flags_certificate_path_not_yet_applied(harness: _Harness) -> None:
    """憑證路徑換了但 Gateway 上還是舊的（或還在掛自簽）就算沒套用。"""
    harness.http_conf = nginx.build_http_config(
        [],
        None,
        platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, True),
    )

    status = platform_entry_service.get_status(_Session(_config(enable_https=True)))

    assert status.applied is False
    assert status.certificate is None
    assert status.certificate_ready is False


def test_status_flags_certificate_not_covering_domain(harness: _Harness) -> None:
    harness.inspect_output = _inspect_output("other.org")
    harness.http_conf = nginx.build_http_config(
        [],
        _CERT,
        platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, True),
    )

    status = platform_entry_service.get_status(_Session(_config(enable_https=True)))

    assert status.certificate_ready is True
    assert status.certificate_matches_domain is False


def test_status_flags_drift_between_gateway_and_saved_config(harness: _Harness) -> None:
    harness.http_conf = nginx.build_http_config(
        [], None, platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.9", 8082, False)
    )
    harness.probe_result = (28, "curl: (28) Connection timed out", "")

    status = platform_entry_service.get_status(_Session(_config()))

    assert status.applied is False
    assert status.applied_upstream == "192.168.100.9:8082"
    assert status.upstream_reachable is False
    assert "timed out" in (status.upstream_detail or "")


def test_status_disabled_entry_is_applied_only_when_block_is_gone(harness: _Harness) -> None:
    session = _Session(_config(enabled=False))
    assert platform_entry_service.get_status(session).applied is True

    harness.http_conf = nginx.build_http_config(
        [], None, platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, False)
    )
    assert platform_entry_service.get_status(session).applied is False


# ─── DNS ─────────────────────────────────────────────────────────────────────

_ZONE = "a" * 32
_OTHER_ZONE = "b" * 32


def _record(
    record_id: str,
    *,
    type_: str = "A",
    content: str = "203.0.113.5",
    proxied: bool = False,
    name: str = "skylab.example.com",
) -> CloudflareDNSRecordPublic:
    return CloudflareDNSRecordPublic(
        id=record_id, zone_id=_ZONE, type=type_, name=name, content=content, ttl=1, proxied=proxied
    )


class _Dns:
    """Cloudflare 那一側的替身：記錄建了／刪了哪些紀錄、DB 記下了哪筆。"""

    def __init__(self, harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        self.configured = True
        self.zones: dict[str, str] = {"example.com": _ZONE, "example.org": _OTHER_ZONE}
        self.events: list[str] = []
        self.upserts: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.tracked: list[tuple[str, str]] = []
        self.upsert_error: Exception | None = None
        self.next_record_id = "rec-new"
        self.found: CloudflareDNSRecordPublic | None = None

        def fake_public_config(_session: object) -> CloudflareConfigPublic:
            return CloudflareConfigPublic(
                account_id=None,
                is_configured=self.configured,
                has_api_token=self.configured,
                has_default_dns_target=self.configured,
                default_dns_target_type="A" if self.configured else None,
                default_dns_target_value="203.0.113.5" if self.configured else None,
            )

        def fake_find_zone(_session: object, domain: str) -> tuple[str, str] | None:
            for name, zone_id in self.zones.items():
                if domain == name or domain.endswith(f".{name}"):
                    return zone_id, domain[: -(len(name) + 1)]
            return None

        def fake_upsert(**kwargs: Any) -> CloudflareDNSRecordPublic:
            self.events.append("dns")
            if self.upsert_error is not None:
                raise self.upsert_error
            self.upserts.append(kwargs)
            return _record(self.next_record_id, name=kwargs["domain"])

        def fake_delete(*, session: object, zone_id: str, record_id: str) -> None:
            self.deleted.append((zone_id, record_id))

        def fake_track(session: _Session, *, zone_id: str, record_id: str) -> None:
            self.tracked.append((zone_id, record_id))
            assert session.config is not None
            session.config.dns_zone_id = zone_id
            session.config.dns_record_id = record_id

        def fake_sync(_session: object) -> None:
            self.events.append("sync")
            harness.syncs += 1
            if harness.sync_error is not None:
                raise harness.sync_error

        monkeypatch.setattr(cloudflare_service, "get_public_config", fake_public_config)
        monkeypatch.setattr(reverse_proxy_service, "find_zone_for_domain", fake_find_zone)
        monkeypatch.setattr(cloudflare_service, "upsert_platform_dns_record", fake_upsert)
        monkeypatch.setattr(cloudflare_service, "delete_reverse_proxy_dns_record", fake_delete)
        monkeypatch.setattr(repo, "set_platform_dns_record", fake_track)
        monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", fake_sync)
        monkeypatch.setattr(cloudflare_service, "find_dns_record", lambda **_k: self.found)
        monkeypatch.setattr(
            cloudflare_service, "get_default_dns_target", lambda _s: ("A", "203.0.113.5")
        )


@pytest.fixture
def dns(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> _Dns:
    return _Dns(harness, monkeypatch)


def test_save_points_dns_to_gateway_after_nginx_is_ready(harness: _Harness, dns: _Dns) -> None:
    session = _Session()

    result = platform_entry_service.save_config(session, _update())

    # nginx 先接好網域，DNS 才指過來
    assert dns.events == ["sync", "dns"]
    assert dns.upserts == [
        {
            "session": session,
            "zone_id": _ZONE,
            "domain": "skylab.example.com",
            "proxied": False,
            "managed_record_id": "",
        }
    ]
    assert dns.tracked == [(_ZONE, "rec-new")]
    assert dns.deleted == []
    assert result.dns_managed is True
    assert result.dns_proxied is False


def test_save_with_cloudflare_proxy_creates_proxied_record(harness: _Harness, dns: _Dns) -> None:
    session = _Session()

    result = platform_entry_service.save_config(session, _update(dns_proxied=True))

    assert harness.upserts[0]["dns_proxied"] is True
    assert dns.upserts[0]["proxied"] is True
    assert result.dns_proxied is True
    # Gateway 的 nginx 要跟著改從 CF-Connecting-IP 取使用者 IP
    entry = platform_entry_service.load_entry(session)
    assert entry is not None and entry.cloudflare_proxy is True


def test_save_leaves_dns_to_admin_when_domain_is_outside_cloudflare(
    harness: _Harness, dns: _Dns
) -> None:
    result = platform_entry_service.save_config(
        _Session(), _update(domain="skylab.campus.edu.tw")
    )

    assert dns.events == ["sync"]
    assert dns.tracked == []
    assert result.dns_managed is False


def test_save_leaves_dns_to_admin_when_cloudflare_is_not_configured(
    harness: _Harness, dns: _Dns
) -> None:
    dns.configured = False

    result = platform_entry_service.save_config(_Session(), _update())

    assert dns.events == ["sync"]
    assert result.dns_managed is False


def test_save_restores_config_and_nginx_when_dns_fails(harness: _Harness, dns: _Dns) -> None:
    dns.upsert_error = BadRequestError("conflicting CNAME")
    session = _Session(_config(domain="old.example.com", upstream_host="192.168.100.9"))

    with pytest.raises(BadRequestError, match="conflicting CNAME"):
        platform_entry_service.save_config(session, _update())

    # DB 還原，nginx 再同步一次把剛寫上去的新區塊蓋回舊設定
    assert dns.events == ["sync", "dns", "sync"]
    assert session.rolled_back is True
    assert session.config is not None
    assert session.config.domain == "old.example.com"
    assert dns.tracked == []


def test_save_resaving_same_domain_updates_the_managed_record_in_place(
    harness: _Harness, dns: _Dns
) -> None:
    dns.next_record_id = "rec-1"
    session = _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1"))

    platform_entry_service.save_config(session, _update(upstream_host="192.168.100.21"))

    assert dns.upserts[0]["managed_record_id"] == "rec-1"
    assert dns.deleted == []
    assert dns.tracked == []


def test_save_changing_domain_removes_the_old_record(harness: _Harness, dns: _Dns) -> None:
    session = _Session(
        _config(domain="old.example.org", dns_zone_id=_OTHER_ZONE, dns_record_id="rec-old")
    )

    result = platform_entry_service.save_config(session, _update())

    # 新網域在另一個 zone，舊紀錄不能拿來就地改
    assert dns.upserts[0]["managed_record_id"] == ""
    assert dns.deleted == [(_OTHER_ZONE, "rec-old")]
    assert dns.tracked == [(_ZONE, "rec-new")]
    assert result.dns_managed is True


def test_save_disabling_removes_the_managed_record(harness: _Harness, dns: _Dns) -> None:
    session = _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1"))

    result = platform_entry_service.save_config(session, _update(enabled=False))

    assert dns.events == ["sync"]
    assert dns.deleted == [(_ZONE, "rec-1")]
    assert dns.tracked == [("", "")]
    assert result.dns_managed is False


def test_save_keeps_record_when_same_domain_is_no_longer_managed(
    harness: _Harness, dns: _Dns
) -> None:
    """Cloudflare 設定被拿掉不代表入口不用了：刪掉紀錄會讓正在用的網域斷線。"""
    dns.configured = False
    session = _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1"))

    result = platform_entry_service.save_config(session, _update())

    assert dns.deleted == []
    assert dns.tracked == []
    assert result.dns_managed is True


def test_save_old_record_cleanup_failure_does_not_undo_the_new_entry(
    harness: _Harness, dns: _Dns, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_delete(**_kwargs: Any) -> None:
        raise RuntimeError("cloudflare down")

    monkeypatch.setattr(cloudflare_service, "delete_reverse_proxy_dns_record", broken_delete)
    session = _Session(
        _config(domain="old.example.com", dns_zone_id=_ZONE, dns_record_id="rec-old")
    )

    platform_entry_service.save_config(session, _update())

    assert dns.tracked == [(_ZONE, "rec-new")]


def test_status_reports_managed_dns_record(harness: _Harness, dns: _Dns) -> None:
    session = _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1"))

    dns.found = _record("rec-1")
    status = platform_entry_service.get_status(session)
    assert status.dns_record == "A 203.0.113.5"
    assert status.dns_record_ok is True

    # 設定是 DNS only 卻被人改成經 Cloudflare 代理：Gateway 沒信任 Cloudflare，
    # 主系統看到的來源 IP 全是 Cloudflare 的位址
    dns.found = _record("rec-1", proxied=True)
    assert platform_entry_service.get_status(session).dns_record_ok is False

    dns.found = None
    status = platform_entry_service.get_status(session)
    assert status.dns_record_ok is False
    assert status.dns_detail


def test_status_flags_proxied_record_switched_back_to_dns_only(
    harness: _Harness, dns: _Dns
) -> None:
    session = _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1", dns_proxied=True))

    dns.found = _record("rec-1", proxied=True)
    assert platform_entry_service.get_status(session).dns_record_ok is True

    dns.found = _record("rec-1", proxied=False)
    assert platform_entry_service.get_status(session).dns_record_ok is False


def test_status_flags_cloudflare_proxy_not_yet_applied_on_gateway(harness: _Harness) -> None:
    """改成經 Cloudflare 代理但 Gateway 上還沒有 realip 設定：還沒套用。"""
    harness.http_conf = nginx.build_http_config(
        [], None, platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, False)
    )
    session = _Session(_config(dns_proxied=True))
    assert platform_entry_service.get_status(session).applied is False

    harness.http_conf = nginx.build_http_config(
        [],
        None,
        platform=nginx.PlatformEntry(
            "skylab.example.com", "192.168.100.20", 8082, False, cloudflare_proxy=True
        ),
    )
    assert platform_entry_service.get_status(session).applied is True


def test_status_without_managed_dns_has_no_dns_fields(harness: _Harness, dns: _Dns) -> None:
    status = platform_entry_service.get_status(_Session(_config()))
    assert status.dns_record is None
    assert status.dns_record_ok is None


# ─── Cloudflare 紀錄寫入 ─────────────────────────────────────────────────────


def _api_record(record_id: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record_id,
        "type": record["type"],
        "name": record["name"],
        "content": record["content"],
        "ttl": 1,
        "proxied": record.get("proxied"),
    }


class _FakeCloudflareClient:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []

    def create_dns_record(self, *, zone_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self.created.append(record)
        return _api_record("rec-created", record)

    def update_dns_record(
        self, *, zone_id: str, record_id: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        self.updated.append((record_id, record))
        return _api_record(record_id, record)

    def delete_dns_record(self, *, zone_id: str, record_id: str) -> None:
        self.deleted.append(record_id)


@pytest.fixture
def cf_client(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_FakeCloudflareClient, list[CloudflareDNSRecordPublic]]:
    client = _FakeCloudflareClient()
    existing: list[CloudflareDNSRecordPublic] = []
    config = SimpleNamespace(default_dns_target_type="A", default_dns_target_value="203.0.113.5")
    monkeypatch.setattr(
        cloudflare_service, "_build_client_from_session", lambda _s: (client, config)
    )
    monkeypatch.setattr(
        cloudflare_service,
        "list_dns_records",
        lambda **_k: SimpleNamespace(items=list(existing)),
    )
    return client, existing


def test_platform_dns_record_is_dns_only(cf_client: Any) -> None:
    client, _existing = cf_client

    record = cloudflare_service.upsert_platform_dns_record(
        session=object(),  # type: ignore[arg-type]
        zone_id=_ZONE,
        domain="SkyLab.example.com",
        proxied=False,
    )

    assert record.id == "rec-created"
    assert client.created[0]["proxied"] is False
    assert client.created[0]["name"] == "skylab.example.com"
    assert client.created[0]["content"] == "203.0.113.5"


def test_platform_dns_record_can_be_proxied(cf_client: Any) -> None:
    client, _existing = cf_client

    cloudflare_service.upsert_platform_dns_record(
        session=object(),  # type: ignore[arg-type]
        zone_id=_ZONE,
        domain="skylab.example.com",
        proxied=True,
    )

    assert client.created[0]["proxied"] is True


def test_platform_dns_record_updates_existing_same_type_record(cf_client: Any) -> None:
    client, existing = cf_client
    existing.append(_record("rec-old", content="198.51.100.7", proxied=True))

    cloudflare_service.upsert_platform_dns_record(
        session=object(),  # type: ignore[arg-type]
        zone_id=_ZONE,
        domain="skylab.example.com",
        proxied=False,
    )

    assert client.created == []
    assert client.updated[0][0] == "rec-old"
    assert client.updated[0][1]["proxied"] is False


def test_platform_dns_record_refuses_foreign_record_of_other_type(cf_client: Any) -> None:
    client, existing = cf_client
    existing.append(_record("rec-ipv6", type_="AAAA", content="2001:db8::1"))

    with pytest.raises(BadRequestError, match="AAAA"):
        cloudflare_service.upsert_platform_dns_record(
            session=object(),  # type: ignore[arg-type]
            zone_id=_ZONE,
            domain="skylab.example.com",
            proxied=False,
        )
    assert client.created == []
    assert client.updated == []
    assert client.deleted == []


def test_platform_dns_record_replaces_own_record_of_other_type(cf_client: Any) -> None:
    """預設 DNS 目標從 CNAME 改成 A：自己先前建的 CNAME 先刪，否則 Cloudflare 不給建。"""
    client, existing = cf_client
    existing.append(_record("rec-1", type_="CNAME", content="gw.example.com"))

    cloudflare_service.upsert_platform_dns_record(
        session=object(),  # type: ignore[arg-type]
        zone_id=_ZONE,
        domain="skylab.example.com",
        proxied=False,
        managed_record_id="rec-1",
    )

    assert client.deleted == ["rec-1"]
    assert client.created[0]["type"] == "A"


def test_annotate_marks_platform_record_as_system_managed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rp_repo, "list_rules", lambda _s: [])
    records = [_record("rec-1"), _record("rec-2", name="other.example.com")]

    reverse_proxy_service.annotate_dns_records_with_system_rules(
        _Session(_config(dns_zone_id=_ZONE, dns_record_id="rec-1")), records
    )

    assert records[0].managed_by_system is True
    assert records[0].managed_vmid is None
    assert records[1].managed_by_system is False
