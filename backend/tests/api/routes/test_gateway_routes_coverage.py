"""app/api/routes/gateway.py 路由層的單元測試（直接呼叫路由函式，service 全部替換）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.routes import gateway as gw
from app.exceptions import BadRequestError
from app.schemas.gateway import ServiceConfigWrite

SESSION = object()


def _user() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


def _raiser(exc: Exception):
    def _fn(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _fn


@pytest.fixture()
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(gw.audit_service, "log_action", lambda **kw: calls.append(kw))
    return calls


@pytest.fixture()
def to_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "to_public_config", lambda config: ("public", config)
    )


# ─── 服務名稱驗證 ─────────────────────────────────────────────────────────────


def test_require_valid_service_rejects_unknown() -> None:
    gw._require_valid_service("nginx")
    with pytest.raises(HTTPException) as info:
        gw._require_valid_service("haproxy")
    assert info.value.status_code == 400


def test_require_configurable_service_rejects_wireguard() -> None:
    gw._require_configurable_service("nginx")
    with pytest.raises(HTTPException) as info:
        gw._require_configurable_service("wireguard")
    assert info.value.status_code == 400


# ─── 連線設定 ─────────────────────────────────────────────────────────────────


def test_get_config(monkeypatch: pytest.MonkeyPatch, to_public: None) -> None:
    monkeypatch.setattr(gw.gw_repo, "get_gateway_config", lambda session: "cfg")
    assert gw.get_config(session=SESSION, _=_user()) == ("public", "cfg")


def test_update_config(
    monkeypatch: pytest.MonkeyPatch, to_public: None, audits: list
) -> None:
    captured: dict[str, Any] = {}

    def _upsert(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "saved"

    monkeypatch.setattr(gw.gw_repo, "upsert_connection_settings", _upsert)
    data = SimpleNamespace(host="10.0.0.1", ssh_port=22, ssh_user="root")
    assert gw.update_config(data=data, session=SESSION, current_user=_user()) == (
        "public",
        "saved",
    )
    assert captured["host"] == "10.0.0.1"
    assert "host=10.0.0.1 port=22 user=root" in audits[0]["details"]


def test_generate_keypair(
    monkeypatch: pytest.MonkeyPatch, to_public: None, audits: list
) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "generate_ed25519_keypair", lambda: ("PRIV", "PUB")
    )
    saved: dict[str, Any] = {}

    def _save(**kwargs: Any) -> str:
        saved.update(kwargs)
        return "kp"

    monkeypatch.setattr(gw.gw_repo, "save_keypair", _save)
    assert gw.generate_keypair(session=SESSION, current_user=_user()) == (
        "public",
        "kp",
    )
    assert saved["private_key_pem"] == "PRIV"
    assert saved["public_key"] == "PUB"
    assert len(audits) == 1


def test_test_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "test_saved_connection", lambda session: (True, "ok")
    )
    result = gw.test_connection(session=SESSION, _=_user())
    assert result.success is True
    assert result.message == "ok"


def test_reset_host_key(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "reset_host_key", lambda session: "gw.local"
    )
    result = gw.reset_host_key(session=SESSION, current_user=_user())
    assert result.message
    assert "gw.local" in audits[0]["details"]


# ─── 安裝腳本 ─────────────────────────────────────────────────────────────────


def test_download_install_script_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        gw.gateway_install_service,
        "install_script_path",
        lambda: tmp_path / "missing.sh",
    )
    with pytest.raises(HTTPException) as info:
        gw.download_install_script(_=_user())
    assert info.value.status_code == 404


def test_download_install_script_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "install.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(
        gw.gateway_install_service, "install_script_path", lambda: script
    )
    response = gw.download_install_script(_=_user())
    assert isinstance(response, FileResponse)
    assert response.filename == "install-gateway.sh"


def test_get_install_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_install_service, "get_install_status", lambda session: "status"
    )
    assert gw.get_install_status(session=SESSION, _=_user()) == "status"


@pytest.mark.parametrize(
    ("allow", "expected"), [(["10.0.0.0/8"], "10.0.0.0/8"), ([], "-")]
)
def test_start_install(
    monkeypatch: pytest.MonkeyPatch, audits: list, allow: list[str], expected: str
) -> None:
    monkeypatch.setattr(
        gw.gateway_install_service,
        "start_install",
        lambda session, options: "started",
    )
    options = SimpleNamespace(
        ingress_interface="eth0",
        vm_interface="eth1",
        snat_address="1.2.3.4",
        listen_port=51820,
        forward_port_start=30000,
        forward_port_end=31000,
        monitoring_allow_from=allow,
    )
    assert (
        gw.start_install(options=options, session=SESSION, current_user=_user())
        == "started"
    )
    assert f"monitoring={expected}" in audits[0]["details"]


# ─── HTTPS 憑證 ───────────────────────────────────────────────────────────────


def test_certificate_routes(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    monkeypatch.setattr(gw.gateway_certificate_service, "get_config", lambda s: "cfg")
    monkeypatch.setattr(gw.gateway_certificate_service, "get_status", lambda s: "st")
    assert gw.get_certificate(session=SESSION, _=_user()) == "cfg"
    assert gw.get_certificate_status(session=SESSION, _=_user()) == "st"

    results = iter(
        [
            SimpleNamespace(
                ssl_certificate_path="/c.pem", ssl_certificate_key_path="/k.pem"
            ),
            SimpleNamespace(ssl_certificate_path=None, ssl_certificate_key_path=None),
        ]
    )
    monkeypatch.setattr(
        gw.gateway_certificate_service, "save_config", lambda s, d: next(results)
    )
    gw.update_certificate(data=object(), session=SESSION, current_user=_user())
    gw.update_certificate(data=object(), session=SESSION, current_user=_user())
    assert "cert=/c.pem key=/k.pem" in audits[0]["details"]
    assert "cert=- key=-" in audits[1]["details"]


# ─── 平台入口 ─────────────────────────────────────────────────────────────────


def test_platform_entry_routes(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    monkeypatch.setattr(gw.platform_entry_service, "get_config", lambda s: "cfg")
    assert gw.get_platform_entry(session=SESSION, _=_user()) == "cfg"

    results = iter(
        [
            SimpleNamespace(
                enabled=True,
                domain="skylab.example.com",
                upstream_host="10.0.0.5",
                upstream_port=8082,
                enable_https=True,
            ),
            SimpleNamespace(
                enabled=False,
                domain=None,
                upstream_host=None,
                upstream_port=80,
                enable_https=False,
            ),
        ]
    )
    monkeypatch.setattr(
        gw.platform_entry_service, "save_config", lambda s, d: next(results)
    )
    gw.update_platform_entry(data=object(), session=SESSION, current_user=_user())
    gw.update_platform_entry(data=object(), session=SESSION, current_user=_user())
    assert "domain=skylab.example.com upstream=10.0.0.5:8082" in audits[0]["details"]
    assert "domain=- upstream=-:80" in audits[1]["details"]

    monkeypatch.setattr(
        gw.platform_entry_service,
        "test_upstream",
        lambda s, host, port: (host, port),
    )
    body = SimpleNamespace(upstream_host="10.0.0.5", upstream_port=8082)
    assert gw.test_platform_entry_upstream(body=body, session=SESSION, _=_user()) == (
        "10.0.0.5",
        8082,
    )


# ─── 服務設定檔 ───────────────────────────────────────────────────────────────


def test_read_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "read_service_config", lambda **kw: "server {}"
    )
    result = gw.read_config(service="nginx", session=SESSION, _=_user())
    assert result.content == "server {}"

    monkeypatch.setattr(
        gw.gateway_service, "read_service_config", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.read_config(service="nginx", session=SESSION, _=_user())
    assert info.value.status_code == 500


def test_write_config(monkeypatch: pytest.MonkeyPatch, audits: list) -> None:
    written: dict[str, Any] = {}
    monkeypatch.setattr(
        gw.gateway_service, "write_service_config", lambda **kw: written.update(kw)
    )
    body = ServiceConfigWrite(content="abc")
    result = gw.write_config(
        service="nginx", body=body, session=SESSION, current_user=_user()
    )
    assert result.message
    assert written["content"] == "abc"
    assert "(3 bytes)" in audits[0]["details"]

    monkeypatch.setattr(
        gw.gateway_service, "write_service_config", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.write_config(
            service="nginx", body=body, session=SESSION, current_user=_user()
        )
    assert info.value.status_code == 500


# ─── 服務控制 ─────────────────────────────────────────────────────────────────


def test_service_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "get_service_status", lambda **kw: (True, "active")
    )
    result = gw.service_status(service="wireguard", session=SESSION, _=_user())
    assert result.active is True
    assert result.status_text == "active"

    monkeypatch.setattr(
        gw.gateway_service, "get_service_status", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.service_status(service="nginx", session=SESSION, _=_user())
    assert info.value.status_code == 500


def test_get_service_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw.gateway_service, "get_service_versions", lambda **kw: "v")
    assert gw.get_service_versions(session=SESSION, _=_user()) == "v"
    monkeypatch.setattr(
        gw.gateway_service, "get_service_versions", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.get_service_versions(session=SESSION, _=_user())
    assert info.value.status_code == 500


def test_get_wireguard_overview(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw.gateway_service, "get_wireguard_overview", lambda **kw: "wg")
    assert gw.get_wireguard_overview(session=SESSION, _=_user()) == "wg"
    monkeypatch.setattr(
        gw.gateway_service, "get_wireguard_overview", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.get_wireguard_overview(session=SESSION, _=_user())
    assert info.value.status_code == 502


def test_service_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _logs(**kwargs: Any) -> tuple[bool, str]:
        captured.update(kwargs)
        return True, "log line"

    monkeypatch.setattr(gw.gateway_service, "get_service_logs", _logs)
    response = gw.service_logs(service="nginx", session=SESSION, _=_user(), lines=10)
    assert isinstance(response, PlainTextResponse)
    assert response.body == b"log line"
    assert captured["lines"] == 10


def test_service_logs_bad_request_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "get_service_logs", _raiser(BadRequestError("bad"))
    )
    with pytest.raises(BadRequestError):
        gw.service_logs(service="nginx", session=SESSION, _=_user())


def test_service_logs_other_error_is_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "get_service_logs", _raiser(RuntimeError("secret detail"))
    )
    with pytest.raises(HTTPException) as info:
        gw.service_logs(service="wireguard", session=SESSION, _=_user())
    assert info.value.status_code == 502
    assert "secret detail" not in str(info.value.detail)


def test_control_service_invalid_action() -> None:
    with pytest.raises(HTTPException) as info:
        gw.control_service(
            service="nginx", action="destroy", session=SESSION, current_user=_user()
        )
    assert info.value.status_code == 400


def test_control_service_success_and_error(
    monkeypatch: pytest.MonkeyPatch, audits: list
) -> None:
    monkeypatch.setattr(
        gw.gateway_service, "control_service", lambda **kw: (True, "restarted")
    )
    result = gw.control_service(
        service="nginx", action="restart", session=SESSION, current_user=_user()
    )
    assert (result.success, result.output, result.action) == (
        True,
        "restarted",
        "restart",
    )
    assert "restart: nginx (success=True)" in audits[0]["details"]

    monkeypatch.setattr(
        gw.gateway_service, "control_service", _raiser(RuntimeError("ssh"))
    )
    with pytest.raises(HTTPException) as info:
        gw.control_service(
            service="nginx", action="stop", session=SESSION, current_user=_user()
        )
    assert info.value.status_code == 500
