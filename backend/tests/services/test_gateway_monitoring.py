"""Gateway 監控：健康判定、SSH 探測解析、平台健康元件、Prometheus http_sd。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from app.api import prometheus_sd
from app.core import metrics as metrics_module
from app.services.monitoring import (
    health_policy,
    prometheus_sd_service,
    system_health_service,
)
from app.services.network import nginx_gateway_service

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _probe(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "nginx": "active",
        "wireguard": "active",
        "config_valid": True,
        "certificates": [{"name": "example.com", "expires_at": NOW + timedelta(days=60)}],
    }
    base.update(overrides)
    return base


# ─── health_policy.gateway_status ──────────────────────────────────────────


def test_gateway_status_ok_when_services_up_and_certs_fresh() -> None:
    assert health_policy.gateway_status(_probe(), now=NOW) == ("ok", None, None)


def test_gateway_status_down_lists_every_broken_service() -> None:
    status, detail, message = health_policy.gateway_status(
        _probe(nginx="inactive", wireguard="failed", config_valid=False), now=NOW
    )
    assert status == "down"
    assert detail == "nginx 未執行（inactive）；WireGuard 未執行（failed）；nginx 設定未通過 nginx -t"
    assert message == f"Gateway 異常：{detail}"


def test_gateway_status_missing_service_state_counts_as_down() -> None:
    status, detail, _ = health_policy.gateway_status(_probe(nginx=None), now=NOW)
    assert status == "down"
    assert "nginx 未執行（unknown）" in (detail or "")


def test_gateway_status_attention_for_expiring_or_expired_certs() -> None:
    probe = _probe(
        certificates=[
            {"name": "fresh.example.com", "expires_at": NOW + timedelta(days=40)},
            {"name": "soon.example.com", "expires_at": NOW + timedelta(days=5, hours=3)},
            {"name": "old.example.com", "expires_at": NOW - timedelta(days=1)},
            {"name": "unknown.example.com", "expires_at": None},
        ]
    )
    status, detail, message = health_policy.gateway_status(probe, now=NOW)
    assert status == "attention"
    assert detail == "soon.example.com 剩 5 天到期；old.example.com 已過期"
    assert message is not None and "certbot" in message


def test_gateway_status_down_wins_over_cert_attention() -> None:
    probe = _probe(
        nginx="failed",
        certificates=[{"name": "soon.example.com", "expires_at": NOW + timedelta(days=2)}],
    )
    assert health_policy.gateway_status(probe, now=NOW)[0] == "down"


def test_attention_component_degrades_overall_and_raises_finding() -> None:
    gateway = {
        "name": "gateway",
        "label": "Gateway · 10.0.0.2",
        "status": "attention",
        "detail": "soon.example.com 剩 5 天到期",
        "alert_message": "Gateway 憑證需要處理：soon.example.com 剩 5 天到期",
    }
    ok = {"name": "database", "label": "PostgreSQL", "status": "ok"}
    assert health_policy.overall_status([ok, gateway], [], []) == "degraded"

    findings = health_policy.build_findings([ok, gateway], [], [])
    assert findings == [
        health_policy.SystemFinding(
            target="component:gateway",
            value=0.0,
            threshold=1.0,
            message="Gateway 憑證需要處理：soon.example.com 剩 5 天到期",
        )
    ]


def test_down_component_without_alert_message_keeps_default_wording() -> None:
    worker = {"name": "worker", "label": "Worker", "status": "down", "detail": "no heartbeat"}
    assert health_policy.build_findings([worker], [], [])[0].message == "Worker 無法連線：no heartbeat"


# ─── nginx_gateway_service：探測指令與解析 ─────────────────────────────────


def test_health_command_quotes_unit_and_prefixes_cert_lines() -> None:
    command = nginx_gateway_service.build_health_command("wg-quick@wg0")
    assert "systemctl is-active wg-quick@wg0" in command
    assert "systemctl is-active nginx" in command
    assert "nginx -t" in command
    assert "sed 's/^/cert=/'" in command
    # 單元名稱會拼進指令：奇怪的字元要被引號包住
    assert "'wg-quick@wg0; rm -rf /'" in nginx_gateway_service.build_health_command(
        "wg-quick@wg0; rm -rf /"
    )


def test_parse_health_output() -> None:
    output = "\n".join(
        [
            "nginx=active",
            "wireguard=inactive",
            "config=fail",
            "cert=example.com\tJan  5 12:00:00 2027 GMT",
            "cert=a.b.example.com\t",
            "garbage line",
        ]
    )
    assert nginx_gateway_service.parse_health_output(output) == {
        "nginx": "active",
        "wireguard": "inactive",
        "config_valid": False,
        "certificates": [
            {"name": "example.com", "expires_at": datetime(2027, 1, 5, 12, 0, tzinfo=timezone.utc)},
            {"name": "a.b.example.com", "expires_at": None},
        ],
    }


def test_parse_health_output_empty_values_become_unknown() -> None:
    parsed = nginx_gateway_service.parse_health_output("nginx=\nwireguard=\nconfig=ok\n")
    assert parsed["nginx"] == "unknown"
    assert parsed["wireguard"] == "unknown"
    assert parsed["config_valid"] is True
    assert parsed["certificates"] == []


# ─── system_health_service.check_gateway ───────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_gateway_cache():
    system_health_service.reset_gateway_cache()
    yield
    system_health_service.reset_gateway_cache()


def _gateway_config() -> SimpleNamespace:
    return SimpleNamespace(host="10.10.0.2", ssh_port=22, ssh_user="root")


def test_check_gateway_disabled_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_health_service, "_load_gateway_config", lambda: None)
    [component] = system_health_service.check_gateway()
    assert component["name"] == "gateway"
    assert component["status"] == "disabled"


def test_check_gateway_down_when_ssh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_health_service, "_load_gateway_config", _gateway_config)

    def boom(config: Any) -> dict[str, Any]:
        raise OSError("connection refused")

    monkeypatch.setattr(system_health_service, "_probe_gateway", boom)
    [component] = system_health_service.check_gateway()
    assert component["status"] == "down"
    assert component["label"] == "Gateway · 10.10.0.2"
    assert "connection refused" in component["detail"]
    assert "SSH" in component["alert_message"]


def test_check_gateway_uses_policy_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(system_health_service, "_load_gateway_config", _gateway_config)

    def probe(config: Any) -> dict[str, Any]:
        calls.append(config.host)
        return _probe(
            certificates=[
                {
                    "name": "soon.example.com",
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
                }
            ]
        )

    monkeypatch.setattr(system_health_service, "_probe_gateway", probe)
    [component] = system_health_service.check_gateway()
    assert component["status"] == "attention"
    assert component["latency_ms"] is not None
    assert "soon.example.com" in component["alert_message"]

    # 快取期間不再開 SSH；/metrics 讀的也是同一份
    system_health_service.check_gateway()
    assert calls == ["10.10.0.2"]
    assert system_health_service.cached_gateway_components()[0]["status"] == "attention"

    system_health_service.check_gateway(use_cache=False)
    assert calls == ["10.10.0.2", "10.10.0.2"]


@pytest.mark.skipif(not metrics_module._AVAILABLE, reason="prometheus_client not installed")
def test_collect_metrics_treats_attention_as_up(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = {"status": "ok", "latency_ms": None}
    monkeypatch.setattr(system_health_service, "check_database", lambda: {**ok, "name": "database", "status": "down"})
    monkeypatch.setattr(system_health_service, "check_redis", lambda: {**ok, "name": "redis", "status": "disabled"})
    monkeypatch.setattr(system_health_service, "check_worker", lambda **_: {**ok, "name": "worker", "status": "disabled"})
    monkeypatch.setattr(system_health_service, "cached_pve_components", lambda: [])
    monkeypatch.setattr(
        system_health_service,
        "cached_gateway_components",
        lambda: [{"name": "gateway", "status": "attention", "latency_ms": 120.0}],
    )
    system_health_service.collect_metrics()
    gauge = metrics_module.DEPENDENCY_UP.labels(component="gateway")
    assert gauge._value.get() == 1

    monkeypatch.setattr(
        system_health_service,
        "cached_gateway_components",
        lambda: [{"name": "gateway", "status": "down", "latency_ms": None}],
    )
    system_health_service.collect_metrics()
    assert gauge._value.get() == 0


# ─── prometheus_sd_service / 端點 ──────────────────────────────────────────


def test_build_gateway_targets() -> None:
    assert prometheus_sd_service.build_gateway_targets("192.168.100.143", "node") == [
        {
            "targets": ["192.168.100.143:9100"],
            "labels": {"skylab_role": "gateway", "exporter": "node"},
        }
    ]
    assert prometheus_sd_service.build_gateway_targets(" gw.example.edu ", "nginx")[0]["targets"] == [
        "gw.example.edu:9113"
    ]
    assert prometheus_sd_service.build_gateway_targets("fd00::2", "node")[0]["targets"] == ["[fd00::2]:9100"]


def test_build_gateway_targets_empty_when_unconfigured_or_unknown_exporter() -> None:
    assert prometheus_sd_service.build_gateway_targets(None, "node") == []
    assert prometheus_sd_service.build_gateway_targets("  ", "node") == []
    assert prometheus_sd_service.build_gateway_targets("10.0.0.2", "haproxy") == []


def _sd_request(query: str, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics/gateway-targets",
            "query_string": query.encode(),
            "headers": headers or [],
        }
    )


async def test_gateway_targets_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics_module.settings, "METRICS_TOKEN", None)
    monkeypatch.setattr(
        prometheus_sd_service,
        "gateway_targets",
        lambda exporter: prometheus_sd_service.build_gateway_targets("10.10.0.2", exporter),
    )

    response = await prometheus_sd.gateway_targets_endpoint(_sd_request("exporter=nginx"))
    assert response.status_code == 200
    assert json.loads(response.body) == [
        {"targets": ["10.10.0.2:9113"], "labels": {"skylab_role": "gateway", "exporter": "nginx"}}
    ]

    bad = await prometheus_sd.gateway_targets_endpoint(_sd_request("exporter=traefik"))
    assert bad.status_code == 400


async def test_gateway_targets_endpoint_requires_metrics_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics_module.settings, "METRICS_TOKEN", "s3cret")
    monkeypatch.setattr(prometheus_sd_service, "gateway_targets", lambda exporter: [])

    denied = await prometheus_sd.gateway_targets_endpoint(_sd_request("exporter=node"))
    assert denied.status_code == 401

    ok = await prometheus_sd.gateway_targets_endpoint(
        _sd_request("exporter=node", [(b"authorization", b"Bearer s3cret")])
    )
    assert ok.status_code == 200


async def test_gateway_targets_endpoint_returns_503_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metrics_module.settings, "METRICS_TOKEN", None)

    def broken(exporter: str) -> list[dict[str, Any]]:
        raise RuntimeError("db down")

    monkeypatch.setattr(prometheus_sd_service, "gateway_targets", broken)
    response = await prometheus_sd.gateway_targets_endpoint(_sd_request("exporter=node"))
    # 非 200 時 Prometheus 會沿用上次的目標，不會讓 Gateway 從監控裡消失
    assert response.status_code == 503
