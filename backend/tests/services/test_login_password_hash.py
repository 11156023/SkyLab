"""使用者自訂的登入密碼只以 SHA-512 crypt 雜湊保存，前端拿不到。

涵蓋：雜湊實作本身、送申請單時的轉換、憑證端點的回應，以及轉範本時
「平台設不設得了密碼」的偵測。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.security import decrypt_value, encrypt_value
from app.exceptions import BadRequestError
from app.services.proxmox import provisioning_service
from app.services.resource import credentials_service
from app.services.template import password_policy, template_service
from app.services.vm import vm_request_service
from app.utils import sha512_crypt as sha512_crypt_module
from app.utils.login_password import hash_login_password, is_login_password_hash
from app.utils.sha512_crypt import sha512_crypt
from tests.utils.login_password import hash_matches

# ─── SHA-512 crypt ───────────────────────────────────────────────────────────

# 這個平台上可用的實作：hashlib 版到處都有，libcrypt 版只有 Linux（CI 與正式映像）
BACKENDS: list[sha512_crypt_module.Backend] = [sha512_crypt_module.sha512_crypt_hashlib]
BACKEND_IDS = ["hashlib"]
if sha512_crypt_module.sha512_crypt_libcrypt is not None:
    BACKENDS.append(sha512_crypt_module.sha512_crypt_libcrypt)
    BACKEND_IDS.append("libcrypt")

requires_libcrypt = pytest.mark.skipif(
    sha512_crypt_module.sha512_crypt_libcrypt is None,
    reason="system libcrypt with SHA-512 crypt is only available on Linux",
)


@pytest.mark.parametrize(
    ("password", "salt", "expected"),
    [
        # Ulrich Drepper 的 SHA-crypt 規格測試向量
        (
            "Hello world!",
            "saltstring",
            "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJu"
            "esI68u4OTLiBFdcbYEdFCoEOfaS35inz1",
        ),
        # `openssl passwd -6 -salt abcdefghijklmnop 'P@ssw0rd!'` 的輸出
        (
            "P@ssw0rd!",
            "abcdefghijklmnop",
            "$6$abcdefghijklmnop$tJ.noQGUkvmNiuTzYOd0uevAKg0TXAfPfedJDocma5xE7QV"
            "GzFhwwOUcshftGfDMayNqLHTEJ/0oupRMlMLnS0",
        ),
    ],
)
@pytest.mark.parametrize("backend", BACKENDS, ids=BACKEND_IDS)
def test_sha512_crypt_matches_reference_output(
    backend: sha512_crypt_module.Backend, password: str, salt: str, expected: str
) -> None:
    assert backend(password.encode("utf-8"), salt) == expected
    assert sha512_crypt(password, salt) == expected


def test_salt_longer_than_sixteen_characters_is_truncated() -> None:
    assert sha512_crypt("pw", "0123456789abcdefXYZ") == sha512_crypt(
        "pw", "0123456789abcdef"
    )


@pytest.mark.parametrize("salt", ["", "has$dollar", "white space", "中文"])
def test_salt_outside_the_crypt_alphabet_is_rejected(salt: str) -> None:
    with pytest.raises(ValueError):
        sha512_crypt("pw", salt)


def test_password_with_nul_is_rejected() -> None:
    with pytest.raises(ValueError):
        sha512_crypt("pw\x00tail")


@requires_libcrypt
def test_linux_uses_the_system_libcrypt() -> None:
    """CI 與正式映像都是 Linux：雜湊走 C 實作且不佔 GIL，不能無聲退回純 Python。"""
    assert sha512_crypt_module.ACTIVE_BACKEND_NAME == "libcrypt"


@requires_libcrypt
@pytest.mark.parametrize(
    "password",
    ["Typed12345", "P@ssw0rd!", "密碼測試🔐", "x", "a" * 120, "stress-test-pw-123"],
)
def test_libcrypt_and_hashlib_backends_agree(password: str) -> None:
    assert sha512_crypt_module.sha512_crypt_libcrypt is not None
    for _ in range(3):
        salt = sha512_crypt_module.generate_salt()
        assert sha512_crypt_module.sha512_crypt_libcrypt(
            password.encode("utf-8"), salt
        ) == sha512_crypt_module.sha512_crypt_hashlib(password.encode("utf-8"), salt)


def test_login_password_hash_is_in_the_form_pve_passes_through() -> None:
    """PVE 只把 ``$6$<salt>$<hash>`` 當成已雜湊的密碼；帶 rounds= 會被再雜湊一次。"""
    crypt_hash = hash_login_password("Typed12345")

    assert is_login_password_hash(crypt_hash)
    assert "rounds=" not in crypt_hash
    assert hash_matches("Typed12345", crypt_hash)
    assert not hash_matches("Typed12346", crypt_hash)


@pytest.mark.parametrize(
    "value", [None, "", "Typed12345", "$6$rounds=5000$salt$abc", "$5$salt$abc"]
)
def test_non_hash_values_are_not_mistaken_for_a_hash(value: str | None) -> None:
    assert not is_login_password_hash(value)


# ─── 送申請單 ────────────────────────────────────────────────────────────────


def _request(**overrides: Any) -> SimpleNamespace:
    base = dict(resource_type="vm", template_id=9000, password="Typed12345")
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def no_registered_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(password_policy, "find_template", lambda *a, **kw: None)


def test_linux_vm_request_keeps_only_a_hash(
    monkeypatch: pytest.MonkeyPatch, no_registered_template: None
) -> None:
    monkeypatch.setattr(
        provisioning_service, "template_is_windows", lambda template_id: False
    )

    sealed = vm_request_service._seal_request_password(None, _request())  # type: ignore[arg-type]

    assert sealed.encrypted is None
    assert hash_matches("Typed12345", sealed.crypt_hash)


def test_lxc_request_never_asks_pve_about_windows(
    monkeypatch: pytest.MonkeyPatch, no_registered_template: None
) -> None:
    def _boom(template_id: int) -> bool:
        raise AssertionError("LXC 不是 Windows，不該查 PVE")

    monkeypatch.setattr(provisioning_service, "template_is_windows", _boom)

    sealed = vm_request_service._seal_request_password(
        None,  # type: ignore[arg-type]
        _request(resource_type="lxc", template_id=None),
    )

    assert hash_matches("Typed12345", sealed.crypt_hash)


def test_windows_request_keeps_the_password_encrypted(
    monkeypatch: pytest.MonkeyPatch, no_registered_template: None
) -> None:
    monkeypatch.setattr(
        provisioning_service, "template_is_windows", lambda template_id: True
    )

    sealed = vm_request_service._seal_request_password(None, _request())  # type: ignore[arg-type]

    assert sealed.crypt_hash is None
    assert sealed.encrypted is not None
    assert decrypt_value(sealed.encrypted) == "Typed12345"


def test_request_without_a_password_is_rejected(
    no_registered_template: None,
) -> None:
    with pytest.raises(BadRequestError):
        vm_request_service._seal_request_password(
            None,  # type: ignore[arg-type]
            _request(password=None),
        )


def test_request_from_an_unsettable_template_stores_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台設不了密碼的範本：送什麼都不存，機器沿用範本內的帳密。"""
    monkeypatch.setattr(
        password_policy,
        "find_template",
        lambda *a, **kw: SimpleNamespace(password_settable=False),
    )

    sealed = vm_request_service._seal_request_password(None, _request())  # type: ignore[arg-type]

    assert sealed == password_policy.SealedPassword()


def test_template_is_windows_refuses_to_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    """讀不到 ostype 要拋錯；當成非 Windows 會把雜湊字串寫成 Windows 的明文密碼。"""
    monkeypatch.setattr(
        provisioning_service.proxmox_service,
        "find_vm_template",
        lambda template_id: {"node": "pve1", "vmid": template_id},
    )

    def _fail(node: str, vmid: int, resource_type: str) -> dict[str, Any]:
        raise RuntimeError("PVE unreachable")

    monkeypatch.setattr(provisioning_service.proxmox_service, "get_config", _fail)

    with pytest.raises(RuntimeError, match="PVE unreachable"):
        provisioning_service.template_is_windows(9000)
    # 既有的寬鬆版本維持不變：查不到當成非 Windows
    assert provisioning_service.is_windows_template(9000) is False


@pytest.mark.parametrize(
    ("ostype", "expected"), [("win11", True), ("l26", False), (None, False)]
)
def test_template_is_windows_reads_ostype(
    monkeypatch: pytest.MonkeyPatch, ostype: str | None, expected: bool
) -> None:
    monkeypatch.setattr(
        provisioning_service.proxmox_service,
        "find_vm_template",
        lambda template_id: {"node": "pve1", "vmid": template_id},
    )
    monkeypatch.setattr(
        provisioning_service.proxmox_service,
        "get_config",
        lambda node, vmid, resource_type: {"ostype": ostype},
    )

    assert provisioning_service.template_is_windows(9000) is expected


# ─── 憑證端點 ────────────────────────────────────────────────────────────────


def _db_resource(**overrides: Any) -> SimpleNamespace:
    base = dict(
        ssh_public_key="ssh-ed25519 AAAA platform",
        ssh_private_key_encrypted=None,
        login_password_encrypted=None,
        login_password_pending_encrypted=None,
        login_password_hash=None,
        login_password_pending_hash=None,
        template_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def ssh_key(monkeypatch: pytest.MonkeyPatch):
    """呼叫 get_ssh_key，資源列與 PVE 設定都用替身。"""

    def _call(
        db_resource: SimpleNamespace,
        *,
        resource_info: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
        template: SimpleNamespace | None = None,
    ):
        monkeypatch.setattr(
            credentials_service.resource_repo,
            "get_resource_by_vmid",
            lambda session, vmid: db_resource,
        )
        monkeypatch.setattr(
            credentials_service, "_qemu_config", lambda info, vmid: config or {}
        )
        monkeypatch.setattr(password_policy, "find_template", lambda *a, **kw: template)
        return credentials_service.get_ssh_key(
            session=None,  # type: ignore[arg-type]
            vmid=101,
            resource_info=resource_info or {"node": "pve1", "type": "qemu"},
        )

    return _call


def test_custom_password_is_not_returned(ssh_key) -> None:
    crypt_hash = hash_login_password("Typed12345")

    res = ssh_key(_db_resource(login_password_hash=crypt_hash))

    assert res.login_password is None
    assert res.login_password_custom is True
    assert res.login_password_pending is False
    assert res.uses_template_credentials is False
    assert crypt_hash not in res.model_dump_json()


def test_pending_custom_password_is_reported_as_pending(ssh_key) -> None:
    res = ssh_key(
        _db_resource(login_password_pending_hash=hash_login_password("x" * 8))
    )

    assert res.login_password is None
    assert res.login_password_custom is True
    assert res.login_password_pending is True


def test_generated_password_is_still_returned(ssh_key) -> None:
    res = ssh_key(_db_resource(login_password_encrypted=encrypt_value("Rand0mPass12")))

    assert res.login_password == "Rand0mPass12"
    assert res.login_password_custom is False
    assert res.login_password_pending is False


def test_machine_from_an_unsettable_template_points_at_the_template(ssh_key) -> None:
    res = ssh_key(
        _db_resource(template_id=9000),
        template=SimpleNamespace(password_settable=False),
    )

    assert res.login_password is None
    assert res.login_password_custom is False
    assert res.uses_template_credentials is True


def test_login_username_comes_from_cloud_init(ssh_key) -> None:
    res = ssh_key(_db_resource(), config={"ciuser": "student"})

    assert res.login_username == "student"


def test_login_username_is_unknown_without_ciuser(ssh_key) -> None:
    """Windows 範本的帳號由 cloudbase-init 設定檔決定，平台不知道。"""
    assert ssh_key(_db_resource(), config={}).login_username is None


def test_container_login_username_is_root(ssh_key) -> None:
    res = ssh_key(_db_resource(), resource_info={"node": "pve1", "type": "lxc"})

    assert res.login_username == "root"


# ─── 轉範本時偵測平台設不設得了密碼 ──────────────────────────────────────────

_WITH_DRIVE = {
    "scsi0": "local-lvm:vm-9000-disk-0,size=20G",
    "ide2": "local-lvm:vm-9000-cloudinit,media=cdrom",
}
_WITHOUT_DRIVE = {
    "scsi0": "local-lvm:vm-9000-disk-0,size=20G",
    "ide2": "none,media=cdrom",
}


def test_cloud_init_drive_is_recognised_on_any_bus() -> None:
    assert template_service.has_cloud_init_drive(_WITH_DRIVE)
    assert template_service.has_cloud_init_drive(
        {"sata1": "ceph:vm-1-cloudinit,media=cdrom"}
    )
    assert not template_service.has_cloud_init_drive(_WITHOUT_DRIVE)
    # 名稱裡碰巧有 cloudinit 的非磁碟欄位不算
    assert not template_service.has_cloud_init_drive({"name": "cloudinit-test"})


@pytest.fixture
def template_config(monkeypatch: pytest.MonkeyPatch):
    def _set(config: dict[str, Any] | Exception) -> None:
        def _get_config(node: str, vmid: int, resource_type: str) -> dict[str, Any]:
            if isinstance(config, Exception):
                raise config
            return config

        monkeypatch.setattr(template_service.proxmox_ops, "get_config", _get_config)

    return _set


def test_container_password_is_always_settable(template_config) -> None:
    template_config(RuntimeError("不該讀設定"))

    assert template_service._detect_password_settable("pve1", 1, "lxc", None) is True


@pytest.mark.parametrize("guest", [True, None])
def test_vm_with_cloud_init_is_settable(template_config, guest: bool | None) -> None:
    template_config(_WITH_DRIVE)

    assert template_service._detect_password_settable("pve1", 1, "qemu", guest) is True


def test_vm_without_cloud_init_drive_is_not_settable(template_config) -> None:
    template_config(_WITHOUT_DRIVE)

    assert template_service._detect_password_settable("pve1", 1, "qemu", True) is False


def test_vm_whose_guest_lacks_cloud_init_is_not_settable(template_config) -> None:
    """PVE 掛了 cloud-init 碟，但 guest 裡沒裝：cipassword 一樣不會生效。"""
    template_config(_WITH_DRIVE)

    assert template_service._detect_password_settable("pve1", 1, "qemu", False) is False


def test_unreadable_config_leaves_the_flag_alone(template_config) -> None:
    template_config(RuntimeError("PVE unreachable"))

    assert template_service._detect_password_settable("pve1", 1, "qemu", None) is None


@pytest.fixture
def guest_agent(monkeypatch: pytest.MonkeyPatch):
    from app.infrastructure.proxmox import guest

    commands: list[list[str]] = []

    def _set(*, alive: bool = True, os_id: str = "ubuntu", exit_code: int = 0) -> None:
        monkeypatch.setattr(guest, "ping_qemu_agent", lambda node, vmid: alive)
        monkeypatch.setattr(guest, "get_osinfo_qemu", lambda node, vmid: {"id": os_id})

        def _exec(node: str, vmid: int, command: list[str]):
            commands.append(command)
            return exit_code, "", ""

        monkeypatch.setattr(guest, "exec_qemu", _exec)

    _set.commands = commands  # type: ignore[attr-defined]
    return _set


def test_probe_reports_cloud_init_present_or_absent(guest_agent) -> None:
    guest_agent(exit_code=0)
    assert template_service._probe_guest_cloud_init("pve1", 1, "qemu") is True
    guest_agent(exit_code=1)
    assert template_service._probe_guest_cloud_init("pve1", 1, "qemu") is False
    assert guest_agent.commands[0][0] == "/bin/sh"


def test_probe_asks_windows_about_cloudbase_init(guest_agent) -> None:
    guest_agent(os_id="mswindows", exit_code=0)

    assert template_service._probe_guest_cloud_init("pve1", 1, "qemu") is True
    assert guest_agent.commands[0][0] == "powershell.exe"
    assert "cloudbase-init" in guest_agent.commands[0][-1]


def test_probe_is_unknown_without_a_guest_agent(guest_agent) -> None:
    guest_agent(alive=False)

    assert template_service._probe_guest_cloud_init("pve1", 1, "qemu") is None
    assert guest_agent.commands == []


def test_probe_skips_containers(guest_agent) -> None:
    guest_agent()

    assert template_service._probe_guest_cloud_init("pve1", 1, "lxc") is None
    assert guest_agent.commands == []
