"""Coverage tests for app.services.resource.settings_service."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.exceptions import BadRequestError, ProxmoxError
from app.schemas.resource_settings import BootOptionsUpdate
from app.services.resource import _guest_helpers
from app.services.resource import settings_service as svc

_QEMU_CONFIG: dict[str, Any] = {
    "boot": "order=scsi0;net0",
    "scsi0": "local-lvm:vm-150-disk-0,size=32G",
    "ide2": "local-lvm:vm-150-cloudinit,media=cdrom",
    "net0": "virtio=AA:BB,bridge=vmbr1,firewall=1",
}


class _FakePve:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.updates: list[dict[str, Any]] = []
        self.isos: list[dict[str, Any]] | Exception = [
            {"volid": "local:iso/Zeta.iso", "size": 2},
            {"volid": "", "size": 0},
            {"volid": "local:iso/alpha.iso", "size": 1},
        ]
        self.specs: dict[str, Any] | Exception = {"cpu": 2, "memory": 2048, "disk": 32}
        self.update_error: Exception | None = None

    def get_config(self, _node: str, _vmid: int, _rtype: str) -> dict[str, Any]:
        return dict(self.config)

    def update_config(self, _node: str, _vmid: int, _rtype: str, **params: Any) -> None:
        if self.update_error is not None:
            raise self.update_error
        self.updates.append(params)

    def list_iso_images(self, _node: str) -> list[dict[str, Any]]:
        if isinstance(self.isos, Exception):
            raise self.isos
        return self.isos

    def get_current_specs(self, _node: str, _vmid: int, _rtype: str) -> dict[str, Any]:
        if isinstance(self.specs, Exception):
            raise self.specs
        return self.specs


@pytest.fixture()
def pve(monkeypatch: pytest.MonkeyPatch) -> _FakePve:
    fake = _FakePve(dict(_QEMU_CONFIG))
    monkeypatch.setattr(svc, "proxmox_service", fake)
    monkeypatch.setattr(_guest_helpers, "proxmox_service", fake)
    monkeypatch.setattr(
        svc,
        "get_proxmox_settings_for_node",
        lambda _node: SimpleNamespace(iso_storage="local"),
    )
    monkeypatch.setattr(svc.audit_service, "log_action", lambda **_kw: None)
    return fake


def _info(rtype: str = "qemu") -> dict[str, Any]:
    return {"node": "pve1", "type": rtype, "status": "stopped"}


def _update(data: BootOptionsUpdate, rtype: str = "qemu") -> Any:
    return svc.update_boot_options(
        session=None,  # type: ignore[arg-type]
        vmid=150,
        resource_info=_info(rtype),
        user_id=None,  # type: ignore[arg-type]
        data=data,
    )


# ── get_specs ──────────────────────────────────────────────────────────────


def test_get_specs_maps_values(pve: _FakePve) -> None:
    specs = svc.get_specs(vmid=150, resource_info=_info("lxc"))
    assert specs.resource_type == "lxc"
    assert (specs.cpu_cores, specs.memory_mb, specs.disk_gb) == (2, 2048, 32)


def test_get_specs_failure_raises_proxmox_error(pve: _FakePve) -> None:
    pve.specs = RuntimeError("down")
    with pytest.raises(ProxmoxError):
        svc.get_specs(vmid=150, resource_info=_info())


# ── parsing helpers ────────────────────────────────────────────────────────


def test_parse_boot_order_variants() -> None:
    assert svc._parse_boot_order(None) == []
    assert svc._parse_boot_order("cdn") == []
    assert svc._parse_boot_order("legacy=1, order=scsi0;;net0") == ["scsi0", "net0"]


def test_collect_devices_ignores_non_string_and_empty_cdrom() -> None:
    devices, slot = svc._collect_devices(
        {
            "cores": 2,
            "ide0": "none,media=cdrom",
            "ide1": "local:iso/b.iso,media=cdrom",
            "net0": "virtio=AA",
        }
    )
    by_key = {d.key: d for d in devices}
    assert slot == "ide0"
    assert by_key["ide0"].description is None
    assert by_key["ide1"].description == "b.iso"
    assert by_key["net0"].description is None


# ── get_boot_options ───────────────────────────────────────────────────────


def test_get_boot_options_iso_storage_lookup_failure(
    pve: _FakePve, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_node: str) -> Any:
        raise RuntimeError("no connection")

    monkeypatch.setattr(svc, "get_proxmox_settings_for_node", _boom)
    opts = svc.get_boot_options(vmid=150, resource_info=_info())
    assert opts.iso_storage is None
    assert opts.cdrom_slot is None
    assert opts.cdrom_iso is None


# ── list_iso_images ────────────────────────────────────────────────────────


def test_list_iso_images_failure(pve: _FakePve) -> None:
    pve.isos = RuntimeError("boom")
    with pytest.raises(ProxmoxError):
        svc.list_iso_images(resource_info=_info())


# ── _pick_cdrom_slot ───────────────────────────────────────────────────────


def test_pick_cdrom_slot_branches() -> None:
    assert svc._pick_cdrom_slot({}, "ide1") == "ide1"
    assert svc._pick_cdrom_slot({"ide2": "x"}, None) == "ide0"
    full = dict.fromkeys(svc._CDROM_SLOT_CANDIDATES, "x")
    with pytest.raises(BadRequestError):
        svc._pick_cdrom_slot(full, None)


# ── update_boot_options ────────────────────────────────────────────────────


def test_update_empty_boot_order_deletes_boot(pve: _FakePve) -> None:
    _update(BootOptionsUpdate(boot_order=[]))
    assert pve.updates == [{"delete": "boot"}]


def test_update_mount_iso_picks_free_slot(pve: _FakePve) -> None:
    _update(BootOptionsUpdate(cdrom_iso=" local:iso/alpha.iso "))
    assert pve.updates == [{"ide0": "local:iso/alpha.iso,media=cdrom"}]


def test_update_mount_unknown_iso_rejected(pve: _FakePve) -> None:
    with pytest.raises(BadRequestError):
        _update(BootOptionsUpdate(cdrom_iso="local:iso/missing.iso"))
    assert pve.updates == []


def test_update_eject_without_cdrom_rejected(pve: _FakePve) -> None:
    with pytest.raises(BadRequestError):
        _update(BootOptionsUpdate(eject_cdrom=True))


def test_update_nothing_to_update_for_lxc(pve: _FakePve) -> None:
    # cdrom_iso="" passes schema validation but carries no change
    with pytest.raises(BadRequestError):
        _update(BootOptionsUpdate(cdrom_iso=""), rtype="lxc")
    assert pve.updates == []


def test_update_config_failure_raises_proxmox_error(pve: _FakePve) -> None:
    pve.update_error = RuntimeError("locked")
    with pytest.raises(ProxmoxError):
        _update(BootOptionsUpdate(boot_order=["net0", "scsi0"]))
