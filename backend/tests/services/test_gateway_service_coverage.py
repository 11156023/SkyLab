"""Coverage tests for app.services.network.gateway_service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError, ProxmoxError, UpstreamServiceError
from app.repositories import gateway_config as gw_repo
from app.repositories import wireguard_peer as peer_repo
from app.services.network import gateway_service, ip_management_service
from app.services.network import nginx_gateway_service as nginx


class _Client:
    def __init__(self, sftp=None) -> None:
        self.closed = False
        self._sftp = sftp

    def close(self) -> None:
        self.closed = True

    def open_sftp(self):
        if isinstance(self._sftp, Exception):
            raise self._sftp
        return self._sftp


class _Handle:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class _Sftp:
    def __init__(self, data: bytes | None = None, missing: bool = False) -> None:
        self._data = data
        self._missing = missing
        self.closed = False

    def open(self, _path: str, _mode: str):
        if self._missing:
            raise FileNotFoundError(_path)
        return _Handle(self._data or b"")

    def close(self) -> None:
        self.closed = True


def _config(**overrides):
    values = {
        "host": "10.0.0.1",
        "ssh_port": 22,
        "ssh_user": "root",
        "public_key": "ssh-ed25519 AAA",
        "encrypted_private_key": "enc",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def configured(monkeypatch):
    config = _config()
    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: config)
    monkeypatch.setattr(gw_repo, "get_decrypted_private_key", lambda _c: "PEM")
    return config


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(gateway_service, "make_client", lambda *a, **k: client)
    return client


# ── 基本 helper ──────────────────────────────────────────────────────────


def test_systemd_unit_rejects_unknown_service() -> None:
    with pytest.raises(BadRequestError):
        gateway_service._systemd_unit("haproxy")


def test_make_client_passes_timeout(monkeypatch) -> None:
    seen = {}

    def fake_create(host, port, user, pem, *, timeout):
        seen.update(host=host, port=port, user=user, pem=pem, timeout=timeout)
        return "client"

    monkeypatch.setattr(gateway_service, "create_key_client", fake_create)
    assert gateway_service.make_client("h", 2222, "u", "pem") == "client"
    assert seen == {"host": "h", "port": 2222, "user": "u", "pem": "pem", "timeout": 10}


def test_exec_checked_raises_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway_service, "exec_command", lambda *_a, **_k: (1, "", "boom")
    )
    with pytest.raises(ProxmoxError):
        gateway_service.exec_checked(object(), "false", "failed")

    monkeypatch.setattr(gateway_service, "exec_command", lambda *_a, **_k: (2, "", ""))
    with pytest.raises(ProxmoxError):
        gateway_service.exec_checked(object(), "false", "failed")


# ── 已儲存連線 / host key ────────────────────────────────────────────────


def test_saved_connection_uses_decrypted_key(monkeypatch, configured) -> None:
    seen = {}

    def fake_test(**kwargs):
        seen.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(gateway_service, "test_connection", fake_test)
    assert gateway_service.test_saved_connection(object()) == (True, "ok")
    assert seen["private_key_pem"] == "PEM"
    assert seen["host"] == "10.0.0.1"


def test_reset_host_key(monkeypatch) -> None:
    forgotten: list[str] = []
    monkeypatch.setattr(gateway_service, "forget_host_key", forgotten.append)

    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: None)
    with pytest.raises(BadRequestError):
        gateway_service.reset_host_key(object())

    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: _config(host=""))
    with pytest.raises(BadRequestError):
        gateway_service.reset_host_key(object())

    monkeypatch.setattr(gw_repo, "get_gateway_config", lambda _s: _config())
    assert gateway_service.reset_host_key(object()) == "10.0.0.1"
    assert forgotten == ["10.0.0.1"]


def test_get_config_requires_host_and_key(monkeypatch) -> None:
    monkeypatch.setattr(
        gw_repo, "get_gateway_config", lambda _s: _config(encrypted_private_key="")
    )
    with pytest.raises(BadRequestError):
        gateway_service._get_credentials(object())


# ── SSH client context managers ──────────────────────────────────────────


def test_gateway_client_closes_connection(monkeypatch, configured) -> None:
    client = _patch_client(monkeypatch, _Client())
    with gateway_service.gateway_client(object()) as opened:
        assert opened is client
    assert client.closed is True


def test_gateway_client_or_502_success_and_failure(monkeypatch, configured) -> None:
    client = _patch_client(monkeypatch, _Client())
    with gateway_service.gateway_client_or_502(object()) as opened:
        assert opened is client
    assert client.closed is True

    def boom(*_a, **_k):
        raise OSError("unreachable")

    monkeypatch.setattr(gateway_service, "make_client", boom)
    with pytest.raises(UpstreamServiceError):
        with gateway_service.gateway_client_or_502(object()):
            pass


# ── test_connection ──────────────────────────────────────────────────────


def test_connection_ok_and_unexpected(monkeypatch) -> None:
    client = _patch_client(monkeypatch, _Client())
    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: (0, "ok\n", ""))
    ok, _ = gateway_service.test_connection("h", 22, "root", "pem")
    assert ok is True
    assert client.closed is True

    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: (0, "nope", ""))
    ok, message = gateway_service.test_connection("h", 22, "root", "pem")
    assert ok is False
    assert "nope" in message


# ── 設定檔讀寫 ───────────────────────────────────────────────────────────


def test_read_service_config_unknown_service(configured) -> None:
    with pytest.raises(BadRequestError):
        gateway_service.read_service_config(object(), "wireguard")


def test_read_service_config_returns_content(monkeypatch, configured) -> None:
    sftp = _Sftp(data=b"worker_processes 1;")
    _patch_client(monkeypatch, _Client(sftp=sftp))
    assert gateway_service.read_service_config(object(), "nginx") == (
        "worker_processes 1;"
    )
    assert sftp.closed is True


def test_read_service_config_missing_file(monkeypatch, configured) -> None:
    sftp = _Sftp(missing=True)
    _patch_client(monkeypatch, _Client(sftp=sftp))
    assert gateway_service.read_service_config(object(), "nginx") == ""
    assert sftp.closed is True


def test_read_service_config_sftp_failure(monkeypatch, configured) -> None:
    _patch_client(monkeypatch, _Client(sftp=OSError("no sftp")))
    with pytest.raises(ProxmoxError):
        gateway_service.read_service_config(object(), "nginx")


def test_write_service_config_unknown_service(configured) -> None:
    with pytest.raises(BadRequestError):
        gateway_service.write_service_config(object(), "wireguard", "x")


def test_write_service_config_paths(monkeypatch, configured) -> None:
    _patch_client(monkeypatch, _Client())
    calls: list[tuple] = []

    def ok(client, path, content, *, reload):
        calls.append((path, content, reload))

    monkeypatch.setattr(nginx, "write_validated_config", ok)
    gateway_service.write_service_config(object(), "nginx", "conf")
    assert calls == [(nginx.NGINX_CONF_PATH, "conf", False)]

    def proxmox_fail(*_a, **_k):
        raise ProxmoxError("nginx -t failed")

    monkeypatch.setattr(nginx, "write_validated_config", proxmox_fail)
    with pytest.raises(ProxmoxError, match="nginx -t failed"):
        gateway_service.write_service_config(object(), "nginx", "conf")

    def other_fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(nginx, "write_validated_config", other_fail)
    with pytest.raises(ProxmoxError):
        gateway_service.write_service_config(object(), "nginx", "conf")


# ── 服務控制 / 狀態 / 日誌 ──────────────────────────────────────────────


def test_control_service_invalid_action(configured) -> None:
    with pytest.raises(BadRequestError):
        gateway_service.control_service(object(), "nginx", "explode")


def test_control_service_restart_not_active(monkeypatch, configured) -> None:
    _patch_client(monkeypatch, _Client())
    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: (3, "failed\n", ""))
    ok, message = gateway_service.control_service(object(), "nginx", "restart")
    assert ok is False
    assert "failed" in message


def test_control_service_empty_output_and_connect_error(
    monkeypatch, configured
) -> None:
    _patch_client(monkeypatch, _Client())
    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: (0, "", ""))
    ok, message = gateway_service.control_service(object(), "nginx", "reload")
    assert ok is True
    assert message

    def boom(*_a, **_k):
        raise OSError("unreachable")

    monkeypatch.setattr(gateway_service, "make_client", boom)
    assert gateway_service.control_service(object(), "nginx", "start") == (
        False,
        "unreachable",
    )


def test_get_service_logs(monkeypatch, configured) -> None:
    _patch_client(monkeypatch, _Client())
    seen: list[str] = []

    def fake_exec(_client, command, **_k):
        seen.append(command)
        return 0, "line1\n", "line2\n"

    monkeypatch.setattr(gateway_service, "_exec", fake_exec)
    ok, logs = gateway_service.get_service_logs(object(), "nginx", lines=5)
    assert ok is True
    assert logs == "line1\nline2"
    assert seen == ["journalctl -u nginx --no-pager -n 5 2>&1"]


def test_get_service_status(monkeypatch, configured) -> None:
    _patch_client(monkeypatch, _Client())
    results = iter([(0, "active\n", ""), (0, "ActiveState=active\n", "")])
    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: next(results))
    assert gateway_service.get_service_status(object(), "nginx") == (
        True,
        "ActiveState=active",
    )

    def boom(*_a, **_k):
        raise OSError("down")

    monkeypatch.setattr(gateway_service, "make_client", boom)
    assert gateway_service.get_service_status(object(), "wireguard") == (False, "down")


# ── WireGuard ────────────────────────────────────────────────────────────


def test_parse_wireguard_dump_tolerates_bad_numbers() -> None:
    dump = "priv\tpub\tnotaport\toff\npeer\tpsk\tep\tips\tbad\tx\ty\n"
    metrics = gateway_service._parse_wireguard_dump(dump, now_timestamp=1000)
    assert metrics["listen_port"] is None
    assert metrics["live_peers"] == 1
    assert metrics["recent_handshakes"] == 0
    assert metrics["transfer_rx_bytes"] == 0
    assert metrics["transfer_tx_bytes"] == 0


def _patch_overview_deps(monkeypatch, subnet) -> None:
    monkeypatch.setattr(
        peer_repo, "list_active_unexpired", lambda *, session, now: [1, 2]
    )
    monkeypatch.setattr(peer_repo, "list_expired_active", lambda *, session, now: [3])
    monkeypatch.setattr(ip_management_service, "get_subnet_config", lambda _s: subnet)


def test_wireguard_overview_with_runtime_metrics(monkeypatch, configured) -> None:
    _patch_overview_deps(monkeypatch, SimpleNamespace(cidr="10.20.0.0/24"))
    _patch_client(monkeypatch, _Client())
    dump = "priv\tpub\t51820\toff\npeer\tpsk\tep\tips\t0\t10\t20\n"
    monkeypatch.setattr(gateway_service, "_exec", lambda *_a, **_k: (0, dump, ""))

    overview = gateway_service.get_wireguard_overview(object())

    assert overview.authorized_sessions == 2
    assert overview.expired_sessions == 1
    assert overview.vm_subnet == "10.20.0.0/24"
    assert overview.listen_port == 51820
    assert overview.transfer_rx_bytes == 10


def test_wireguard_overview_ssh_failure_is_logged(monkeypatch, configured) -> None:
    _patch_overview_deps(monkeypatch, None)

    def boom(*_a, **_k):
        raise OSError("unreachable")

    monkeypatch.setattr(gateway_service, "make_client", boom)

    overview = gateway_service.get_wireguard_overview(object())

    assert overview.inspection_available is False
    assert overview.live_peers == 0


# ── 版本偵測 ─────────────────────────────────────────────────────────────


def test_version_helpers_edge_cases() -> None:
    assert gateway_service._normalize_version("v1.2.3") == "1.2.3"
    assert gateway_service._normalize_version("v") is None
    assert gateway_service._extract_current_version("   ") is None
    assert gateway_service._extract_current_version("no version here") is None


def test_service_versions_report_detection_errors(monkeypatch) -> None:
    def fake_exec(_client, command: str, **_kwargs):
        if "apt-cache policy" in command:
            return 100, "", ""
        if command.startswith("nginx -v"):
            return 127, "", ""
        return 1, "", "wg: command not found"

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(gateway_service, "gateway_client", lambda _s: _Ctx())
    monkeypatch.setattr(gateway_service, "_exec", fake_exec)

    result = gateway_service.get_service_versions(object())

    by_service = {item.service: item for item in result.items}
    assert by_service["nginx"].current_version is None
    assert by_service["nginx"].target_version is None
    assert by_service["nginx"].detection_error
    assert by_service["wireguard"].detection_error == "wg: command not found"
