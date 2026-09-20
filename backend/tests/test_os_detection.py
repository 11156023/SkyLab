"""Guest OS 身份偵測契約（純函式）測試。"""

from __future__ import annotations

from app.infrastructure.proxmox.os_detection import (
    format_os_token,
    is_windows_guest_identity,
    normalize_lxc_ostype,
    normalize_os_release_text,
    normalize_qemu_ostype,
    normalize_qga_osinfo,
    unknown_guest_os,
)


def test_qga_ubuntu_maps_to_linux_ubuntu_high() -> None:
    guest_os = normalize_qga_osinfo(
        {
            "id": "ubuntu",
            "name": "Ubuntu",
            "pretty-name": "Ubuntu 24.04.3 LTS",
            "version": "24.04.3 (LTS)",
        }
    )
    assert guest_os is not None
    assert guest_os["family"] == "linux"
    assert guest_os["id"] == "ubuntu"
    assert guest_os["version"] == "24.04.3 (LTS)"
    assert guest_os["pretty_name"] == "Ubuntu 24.04.3 LTS"
    assert guest_os["source"] == "qemu_guest_agent"
    assert guest_os["confidence"] == "high"
    assert format_os_token(guest_os) == "linux/ubuntu 24.04.3 (LTS) (high)"


def test_qga_windows_maps_to_windows_family() -> None:
    guest_os = normalize_qga_osinfo(
        {"id": "mswindows", "name": "Microsoft Windows", "version": "11"}
    )
    assert guest_os is not None
    assert guest_os["family"] == "windows"
    assert guest_os["confidence"] == "high"
    assert format_os_token(guest_os) == "windows 11 (high)"


def test_qga_payload_without_id_is_not_detectable() -> None:
    assert normalize_qga_osinfo(None) is None
    assert normalize_qga_osinfo({"name": "Unknown"}) is None


def test_lxc_ostype_debian_is_real_distro_identity() -> None:
    guest_os = normalize_lxc_ostype("debian")
    assert guest_os["family"] == "linux"
    assert guest_os["id"] == "debian"
    assert guest_os["source"] == "pve_ostype"
    assert guest_os["confidence"] == "medium"


def test_lxc_ostype_unmanaged_and_empty_stay_unknown() -> None:
    assert normalize_lxc_ostype("unmanaged") == unknown_guest_os()
    assert normalize_lxc_ostype(None) == unknown_guest_os()


def test_qemu_ostype_l26_is_only_a_linux_hint() -> None:
    guest_os = normalize_qemu_ostype("l26")
    assert guest_os["family"] == "linux"
    # 不得推導成特定發行版
    assert guest_os["id"] is None
    assert guest_os["confidence"] == "low"
    assert format_os_token(guest_os) == "linux (low)"


def test_qemu_ostype_windows_is_family_only() -> None:
    guest_os = normalize_qemu_ostype("win11")
    assert guest_os["family"] == "windows"
    assert guest_os["id"] is None
    assert guest_os["confidence"] == "medium"


def test_qemu_ostype_other_is_unknown() -> None:
    assert normalize_qemu_ostype("other") == unknown_guest_os()


def test_os_release_text_parses_ubuntu() -> None:
    guest_os = normalize_os_release_text(
        'NAME="Ubuntu Linux"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
    )
    assert guest_os is not None
    assert guest_os["family"] == "linux"
    assert guest_os["id"] == "ubuntu"
    assert guest_os["version"] == "24.04"
    assert guest_os["pretty_name"] == "Ubuntu 24.04 LTS"
    assert guest_os["source"] == "os_release"
    assert guest_os["confidence"] == "high"


def test_os_release_text_garbage_returns_none() -> None:
    assert normalize_os_release_text("") is None
    assert normalize_os_release_text("not an os-release file") is None


def test_format_os_token_defaults_to_unknown() -> None:
    assert format_os_token(None) == "unknown"
    assert format_os_token({}) == "unknown"
    assert format_os_token(unknown_guest_os()) == "unknown"


def test_is_windows_guest_identity_three_states() -> None:
    assert is_windows_guest_identity({"family": "windows"}) is True
    assert is_windows_guest_identity({"family": "linux", "id": "ubuntu"}) is False
    assert is_windows_guest_identity({"family": "unknown"}) is None
    assert is_windows_guest_identity(None) is None
    assert is_windows_guest_identity("Windows 11") is None
