"""Coverage tests for app.services.resource.credentials_service."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest

from app.exceptions import BadRequestError, NotFoundError, ProxmoxError
from app.services.resource import _guest_helpers
from app.services.resource import credentials_service as svc

_PLATFORM_KEY = "ssh-ed25519 AAAAplatform SkyLab-vm150"
_USER_KEY = "ssh-rsa AAAAuser alice@laptop"
_NEW_PUBLIC = "ssh-ed25519 AAAAnew SkyLab-vm150"


class _FakePve:
    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []
        self.update_error: Exception | None = None
        self.control_error: Exception | None = None
        self.controls: list[str] = []

    def get_config(self, _node: str, _vmid: int, _rtype: str) -> dict[str, Any]:
        return dict(self.config)

    def update_config(self, _node: str, _vmid: int, _rtype: str, **params: Any) -> None:
        if self.update_error is not None:
            raise self.update_error
        self.updates.append(params)

    def control(self, _node: str, _vmid: int, _rtype: str, action: str) -> None:
        if self.control_error is not None:
            raise self.control_error
        self.controls.append(action)


class _FakeGuest:
    def __init__(self) -> None:
        self.results: list[Any] = []
        self.calls: list[tuple[str, str | None]] = []

    def exec_lxc(
        self, _node: str, _vmid: int, command: str, *, timeout: float, stdin: Any
    ) -> tuple[int, str, str]:
        self.calls.append((command, stdin))
        result = self.results.pop(0) if self.results else (0, "", "")
        if isinstance(result, Exception):
            raise result
        return result


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


def _db_resource(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "ssh_public_key": _PLATFORM_KEY,
        "ssh_private_key_encrypted": None,
        "login_password_encrypted": None,
        "login_password_hash": None,
        "login_password_pending_encrypted": None,
        "login_password_pending_hash": None,
        "template_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    pve = _FakePve()
    guest = _FakeGuest()
    ns = SimpleNamespace(pve=pve, guest=guest, db=_db_resource(), audits=[])
    monkeypatch.setattr(svc, "proxmox_service", pve)
    monkeypatch.setattr(_guest_helpers, "proxmox_service", pve)
    monkeypatch.setattr(svc, "guest", guest)
    monkeypatch.setattr(svc.resource_repo, "get_resource_by_vmid", lambda **_kw: ns.db)
    monkeypatch.setattr(
        svc.audit_service, "log_action", lambda **kw: ns.audits.append(kw)
    )
    monkeypatch.setattr(svc, "encrypt_value", lambda v: f"enc:{v}")
    monkeypatch.setattr(svc, "decrypt_value", lambda v: v.removeprefix("enc:"))
    monkeypatch.setattr(svc, "hash_login_password", lambda v: f"hash:{v}")
    monkeypatch.setattr(svc, "generate_login_password", lambda: "Gen3rated!")
    monkeypatch.setattr(
        svc,
        "generate_ed25519_keypair",
        lambda comment: ("PRIVATE-PEM", _NEW_PUBLIC),
    )
    return ns


def _qemu(status: str = "stopped") -> dict[str, Any]:
    return {"node": "pve1", "type": "qemu", "status": status}


def _lxc(status: str = "running") -> dict[str, Any]:
    return {"node": "pve1", "type": "lxc", "status": status}


# ── small helpers ──────────────────────────────────────────────────────────


def test_key_helpers() -> None:
    assert svc._key_identity("  single  ") == "single"
    assert svc._key_identity(_USER_KEY) == "ssh-rsa AAAAuser"
    assert svc._split_keys("# c\r\n\n a \nb") == ["a", "b"]
    assert svc._dedupe(["t k one", "t k two", "t x"]) == ["t k one", "t x"]


def test_get_db_resource_missing(env: SimpleNamespace) -> None:
    env.db = None
    with pytest.raises(NotFoundError):
        svc._get_db_resource(_FakeSession(), 150)  # type: ignore[arg-type]


def test_qemu_write_keys_set_delete_and_failure(env: SimpleNamespace) -> None:
    svc._qemu_write_keys(_qemu(), 150, ["a b", "c d"])
    svc._qemu_write_keys(_qemu(), 150, [])
    assert env.pve.updates == [
        {"sshkeys": quote("a b\nc d", safe="")},
        {"delete": "sshkeys"},
    ]
    env.pve.update_error = RuntimeError("locked")
    with pytest.raises(ProxmoxError):
        svc._qemu_write_keys(_qemu(), 150, [])


def test_lxc_exec_failures(env: SimpleNamespace) -> None:
    env.guest.results = [RuntimeError("ssh down"), (1, "", " denied "), (2, "out", "")]
    with pytest.raises(ProxmoxError):
        svc._lxc_exec(_lxc(), 150, "true")
    with pytest.raises(ProxmoxError, match="denied"):
        svc._lxc_exec(_lxc(), 150, "true")
    with pytest.raises(ProxmoxError, match="out"):
        svc._lxc_exec(_lxc(), 150, "true")


def test_lxc_authorized_keys_variants(env: SimpleNamespace) -> None:
    assert svc._lxc_authorized_keys(_lxc("stopped"), 150) == []
    env.guest.results = [(0, f"{_USER_KEY}\n", "")]
    assert svc._lxc_authorized_keys(_lxc(), 150) == [_USER_KEY]
    env.guest.results = [(1, "", "boom")]
    assert svc._lxc_authorized_keys(_lxc(), 150) == []
    env.guest.results = [(1, "", "boom")]
    with pytest.raises(ProxmoxError):
        svc._lxc_authorized_keys(_lxc(), 150, strict=True)


# ── get_credentials ────────────────────────────────────────────────────────


def test_get_credentials_qemu(env: SimpleNamespace) -> None:
    env.pve.config = {
        "ciuser": "alice",
        "ostype": "win11",
        "sshkeys": quote(f"{_PLATFORM_KEY}\n{_USER_KEY}", safe=""),
    }
    env.db.login_password_hash = "h"
    out = svc.get_credentials(session=_FakeSession(), vmid=150, resource_info=_qemu())  # type: ignore[arg-type]
    assert out.username == "alice"
    assert out.is_windows is True
    assert out.has_custom_login_password is True
    assert out.authorized_keys == [_PLATFORM_KEY, _USER_KEY]


def test_get_credentials_qemu_without_user_or_keys(env: SimpleNamespace) -> None:
    out = svc.get_credentials(session=_FakeSession(), vmid=150, resource_info=_qemu())  # type: ignore[arg-type]
    assert out.username is None
    assert out.is_windows is False
    assert out.authorized_keys == []


def test_get_credentials_lxc(env: SimpleNamespace) -> None:
    env.db.login_password_encrypted = "enc:pw"
    env.guest.results = [(0, _USER_KEY, "")]
    out = svc.get_credentials(session=_FakeSession(), vmid=150, resource_info=_lxc())  # type: ignore[arg-type]
    assert out.username == "root"
    assert out.has_login_password is True
    assert out.has_custom_login_password is False
    assert out.authorized_keys == [_USER_KEY]


# ── get_ssh_key ────────────────────────────────────────────────────────────


def test_get_ssh_key_missing_record(env: SimpleNamespace) -> None:
    env.db = None
    with pytest.raises(ProxmoxError):
        svc.get_ssh_key(session=_FakeSession(), vmid=150, resource_info=_lxc())  # type: ignore[arg-type]


def test_get_ssh_key_with_system_password_and_private_key(env: SimpleNamespace) -> None:
    env.db.ssh_private_key_encrypted = "enc:PEM"
    env.db.login_password_encrypted = "enc:pw"
    out = svc.get_ssh_key(session=_FakeSession(), vmid=150, resource_info=_lxc())  # type: ignore[arg-type]
    assert out.ssh_private_key == "PEM"
    assert out.login_password == "pw"
    assert out.login_username == "root"
    assert out.uses_template_credentials is False


def test_get_ssh_key_template_credentials_lookup(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.db.template_id = 9000
    env.pve.config = {"ciuser": "ubuntu"}
    seen: list[Any] = []

    def _find(_session: Any, *, pve_vmid: int) -> Any:
        seen.append(pve_vmid)
        return SimpleNamespace(password_settable=False)

    monkeypatch.setattr(svc.password_policy, "find_template", _find)
    out = svc.get_ssh_key(session=_FakeSession(), vmid=150, resource_info=_qemu())  # type: ignore[arg-type]
    assert seen == [9000]
    assert out.uses_template_credentials is True
    assert out.login_username == "ubuntu"
    assert out.login_password is None


def test_get_ssh_key_pending_password(env: SimpleNamespace) -> None:
    env.db.login_password_pending_hash = "h"
    out = svc.get_ssh_key(session=_FakeSession(), vmid=150, resource_info=_qemu())  # type: ignore[arg-type]
    assert out.login_password_pending is True
    assert out.login_username is None


# ── reset_password ─────────────────────────────────────────────────────────


def _reset(info: dict[str, Any], password: str | None, session: Any = None) -> Any:
    return svc.reset_password(
        session=session or _FakeSession(),
        vmid=150,
        resource_info=info,
        user_id=uuid.uuid4(),
        password=password,
    )


def test_reset_password_qemu_running_reboots(env: SimpleNamespace) -> None:
    session = _FakeSession()
    out = _reset(_qemu("running"), None, session)
    assert out.password == "Gen3rated!"
    assert out.rebooting is True
    assert out.applied_immediately is False
    assert env.pve.controls == ["reboot"]
    assert env.db.login_password_encrypted == "enc:Gen3rated!"
    assert env.db.login_password_hash is None
    assert session.commits == 1


def test_reset_password_qemu_reboot_failure_reported(env: SimpleNamespace) -> None:
    env.pve.control_error = RuntimeError("qmp timeout")
    out = _reset(_qemu("running"), "Custom-Pass1")
    assert out.rebooting is False
    assert "qmp timeout" in out.message
    assert env.db.login_password_hash == "hash:Custom-Pass1"
    assert env.db.login_password_encrypted is None


def test_reset_password_qemu_stopped_applies_next_boot(env: SimpleNamespace) -> None:
    out = _reset(_qemu("stopped"), None)
    assert out.rebooting is False
    assert env.pve.controls == []


def test_reset_password_windows_rejects_weak_custom(env: SimpleNamespace) -> None:
    env.pve.config = {"ostype": "win10"}
    with pytest.raises(BadRequestError):
        _reset(_qemu(), "weakpass")
    assert env.pve.updates == []


def test_reset_password_weak_custom_on_linux_allowed(env: SimpleNamespace) -> None:
    env.pve.config = {"ostype": "l26"}
    out = _reset(_qemu(), "weakpass")
    assert env.pve.updates == [{"cipassword": "weakpass"}]
    assert out.password == "weakpass"


def test_reset_password_qemu_update_failure(env: SimpleNamespace) -> None:
    env.pve.update_error = RuntimeError("locked")
    with pytest.raises(ProxmoxError):
        _reset(_qemu(), None)


def test_reset_password_lxc_uses_stdin(env: SimpleNamespace) -> None:
    out = _reset(_lxc(), None)
    assert out.applied_immediately is True
    assert env.guest.calls == [("chpasswd", "root:Gen3rated!\n")]


def test_reset_password_lxc_requires_running(env: SimpleNamespace) -> None:
    with pytest.raises(BadRequestError):
        _reset(_lxc("stopped"), None)


# ── regenerate_ssh_key ─────────────────────────────────────────────────────


def test_regenerate_ssh_key_qemu_replaces_platform_key(env: SimpleNamespace) -> None:
    env.pve.config = {"sshkeys": quote(f"{_PLATFORM_KEY}\n{_USER_KEY}", safe="")}
    session = _FakeSession()
    out = svc.regenerate_ssh_key(
        session=session,  # type: ignore[arg-type]
        vmid=150,
        resource_info=_qemu(),
        user_id=uuid.uuid4(),
    )
    assert out.applied_immediately is False
    assert env.pve.updates == [
        {"sshkeys": quote(f"{_USER_KEY}\n{_NEW_PUBLIC}", safe="")}
    ]
    assert env.db.ssh_public_key == _NEW_PUBLIC
    assert env.db.ssh_private_key_encrypted == "enc:PRIVATE-PEM"
    assert session.commits == 1


def test_regenerate_ssh_key_lxc_without_old_key(env: SimpleNamespace) -> None:
    env.db.ssh_public_key = None
    env.guest.results = [(0, _USER_KEY, ""), (0, "", "")]
    out = svc.regenerate_ssh_key(
        session=_FakeSession(),  # type: ignore[arg-type]
        vmid=150,
        resource_info=_lxc(),
        user_id=uuid.uuid4(),
    )
    assert out.applied_immediately is True
    assert "authorized_keys" in env.guest.calls[1][0]


# ── add / remove authorized key ────────────────────────────────────────────


def test_add_authorized_key_lxc(env: SimpleNamespace) -> None:
    env.guest.results = [(0, _PLATFORM_KEY, ""), (0, "", "")]
    out = svc.add_authorized_key(
        session=_FakeSession(),  # type: ignore[arg-type]
        vmid=150,
        resource_info=_lxc(),
        user_id=uuid.uuid4(),
        public_key=_USER_KEY,
    )
    assert out.authorized_keys == [_PLATFORM_KEY, _USER_KEY]
    assert out.applied_immediately is True
    assert env.audits[0]["action"] == "credential_update"


def test_add_authorized_key_duplicate_rejected(env: SimpleNamespace) -> None:
    env.pve.config = {"sshkeys": quote(_USER_KEY, safe="")}
    with pytest.raises(BadRequestError):
        svc.add_authorized_key(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            resource_info=_qemu(),
            user_id=uuid.uuid4(),
            public_key="ssh-rsa AAAAuser other-comment",
        )


def test_remove_authorized_key_platform_key_rejected(env: SimpleNamespace) -> None:
    with pytest.raises(BadRequestError):
        svc.remove_authorized_key(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            resource_info=_qemu(),
            user_id=uuid.uuid4(),
            public_key=_PLATFORM_KEY,
        )


def test_remove_authorized_key_not_found(env: SimpleNamespace) -> None:
    env.db.ssh_public_key = None
    env.pve.config = {"sshkeys": quote(_PLATFORM_KEY, safe="")}
    with pytest.raises(NotFoundError):
        svc.remove_authorized_key(
            session=_FakeSession(),  # type: ignore[arg-type]
            vmid=150,
            resource_info=_qemu(),
            user_id=uuid.uuid4(),
            public_key=_USER_KEY,
        )


def test_remove_authorized_key_qemu(env: SimpleNamespace) -> None:
    env.pve.config = {"sshkeys": quote(f"{_PLATFORM_KEY}\n{_USER_KEY}", safe="")}
    out = svc.remove_authorized_key(
        session=_FakeSession(),  # type: ignore[arg-type]
        vmid=150,
        resource_info=_qemu(),
        user_id=uuid.uuid4(),
        public_key=_USER_KEY,
    )
    assert out.authorized_keys == [_PLATFORM_KEY]
    assert out.applied_immediately is False
    assert env.pve.updates == [{"sshkeys": quote(_PLATFORM_KEY, safe="")}]
    assert "removed" in env.audits[0]["details"]
