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
from app.schemas.gateway import PlatformEntryUpdate
from app.services.network import (
    cloudflare_service,
    platform_entry_service,
    reverse_proxy_service,
)
from app.services.network import nginx_gateway_service as nginx


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
        self.commands: list[str] = []
        self.upserts: list[dict[str, Any]] = []
        self.syncs = 0
        self.sync_error: Exception | None = None
        self.http_conf = ""
        self.domain_taken = False

        def fake_upsert(session: _Session, **values: Any) -> PlatformEntryConfig:
            self.upserts.append(values)
            session.config = PlatformEntryConfig(id=1, **values)
            return session.config

        @contextmanager
        def fake_client(_session: object) -> Iterator[object]:
            yield object()

        def fake_exec(_client: Any, command: str, **_kwargs: Any) -> tuple[int, str, str]:
            self.commands.append(command)
            if command.startswith("cat "):
                return 0, self.http_conf, ""
            if "fullchain.pem" in command:
                return 0, "example.com\tJan  5 12:00:00 2027 GMT\n", ""
            return self.probe_result

        def fake_sync(_session: object) -> None:
            self.syncs += 1
            if self.sync_error is not None:
                raise self.sync_error

        monkeypatch.setattr(repo, "upsert_platform_entry_config", fake_upsert)
        monkeypatch.setattr(rp_repo, "is_domain_taken", lambda *_a, **_k: self.domain_taken)
        monkeypatch.setattr(platform_entry_service, "_gateway_client", fake_client)
        monkeypatch.setattr(platform_entry_service, "_gateway_host", lambda _s: (True, "192.168.100.2"))
        monkeypatch.setattr(nginx, "_exec", fake_exec)
        monkeypatch.setattr(reverse_proxy_service, "sync_to_gateway", fake_sync)
        monkeypatch.setattr(
            cloudflare_service, "get_public_config", lambda _s: SimpleNamespace(is_configured=True)
        )
        monkeypatch.setattr(
            reverse_proxy_service, "resolve_zone_for_domain", lambda _s, _d: ("zone-id", "skylab")
        )


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


def test_save_https_requires_cloudflare_zone(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_zone(_session: object, _domain: str) -> tuple[str, str]:
        raise BadRequestError("no zone")

    monkeypatch.setattr(reverse_proxy_service, "resolve_zone_for_domain", no_zone)

    with pytest.raises(BadRequestError, match="Cloudflare"):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []


def test_save_https_requires_cloudflare_token(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cloudflare_service, "get_public_config", lambda _s: SimpleNamespace(is_configured=False)
    )
    with pytest.raises(BadRequestError):
        platform_entry_service.save_config(_Session(), _update(enable_https=True))
    assert harness.upserts == []


# ─── 狀態 ────────────────────────────────────────────────────────────────────


def test_status_reports_applied_when_gateway_matches_saved_config(harness: _Harness) -> None:
    config = _config(enable_https=True)
    harness.http_conf = nginx.build_http_config(
        [],
        {"skylab.example.com": "example.com"},
        platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, True),
    )

    status = platform_entry_service.get_status(
        _Session(config), observed_client_ip="203.0.113.7", observed_scheme="https"
    )

    assert status.applied is True
    assert status.applied_upstream == "192.168.100.20:8082"
    assert status.certificate == "example.com"
    assert status.certificate_ready is True
    assert status.certificate_expires_at is not None
    assert status.upstream_reachable is True
    assert status.observed_client_ip == "203.0.113.7"
    assert status.observed_scheme == "https"


def test_status_flags_drift_between_gateway_and_saved_config(harness: _Harness) -> None:
    harness.http_conf = nginx.build_http_config(
        [], {}, platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.9", 8082, False)
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
        [], {}, platform=nginx.PlatformEntry("skylab.example.com", "192.168.100.20", 8082, False)
    )
    assert platform_entry_service.get_status(session).applied is False


def test_best_zone_name_prefers_longest_matching_zone() -> None:
    zones = ["example.com", "lab.example.com", "other.org"]
    assert reverse_proxy_service._best_zone_name("skylab.lab.example.com", zones) == "lab.example.com"
    assert reverse_proxy_service._best_zone_name("skylab.example.com", zones) == "example.com"
    assert reverse_proxy_service._best_zone_name("notexample.com", zones) is None
