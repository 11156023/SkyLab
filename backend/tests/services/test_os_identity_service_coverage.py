"""Coverage tests for app.services.os_identity_service (guest OS detection)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import os_identity_service as svc


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushed += 1


def _patch_config(monkeypatch: pytest.MonkeyPatch, config=None, error=None) -> None:
    def _get_config(node, vmid, resource_type):
        if error is not None:
            raise error
        return config or {}

    monkeypatch.setattr(svc.proxmox_ops, "get_config", _get_config)


def _patch_qga(monkeypatch: pytest.MonkeyPatch, *, alive: bool, osinfo=None) -> None:
    monkeypatch.setattr(svc.guest, "ping_qemu_agent", lambda node, vmid: alive)
    monkeypatch.setattr(svc.guest, "get_osinfo_qemu", lambda node, vmid: osinfo)


# ── _read_ostype ───────────────────────────────────────────


# ── initial_guest_os ───────────────────────────────────────


def test_initial_guest_os_lxc_is_stamped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, {"ostype": "debian"})
    payload = svc.initial_guest_os(resource_type=" LXC ", node="pve1", vmid=101)
    assert payload is not None
    assert payload["family"] == "linux"
    assert payload["id"] == "debian"
    assert payload["detected_at"]


def test_initial_guest_os_unknown_type_or_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, {})
    assert svc.initial_guest_os(resource_type="docker", node="pve1", vmid=1) is None
    assert svc.initial_guest_os(resource_type=None, node="pve1", vmid=1) is None  # type: ignore[arg-type]
    assert svc.initial_guest_os(resource_type="qemu", node="pve1", vmid=1) is None


# ── ensure_guest_os ────────────────────────────────────────


def test_ensure_guest_os_none_resource() -> None:
    assert (
        svc.ensure_guest_os(_FakeSession(), None, node="pve1", resource_type="qemu")
        is None
    )


def test_ensure_guest_os_returns_existing_identity() -> None:
    existing = {"family": "windows", "id": "mswindows"}
    resource = SimpleNamespace(vmid=1, guest_os=existing)
    session = _FakeSession()
    assert (
        svc.ensure_guest_os(session, resource, node="pve1", resource_type="qemu")
        is existing
    )
    assert session.added == []


def test_ensure_guest_os_qemu_agent_probe_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_qga(
        monkeypatch,
        alive=True,
        osinfo={"id": "ubuntu", "version-id": "24.04", "pretty-name": "Ubuntu"},
    )
    resource = SimpleNamespace(vmid=200, guest_os={"family": "unknown"})
    session = _FakeSession()

    payload = svc.ensure_guest_os(session, resource, node="pve1", resource_type="QEMU")
    assert payload is not None
    assert payload["id"] == "ubuntu"
    assert payload["confidence"] == "high"
    assert resource.guest_os is payload
    assert session.added == [resource]
    assert session.flushed == 1


def test_ensure_guest_os_qemu_falls_back_to_ostype(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_qga(monkeypatch, alive=False)
    _patch_config(monkeypatch, {"ostype": "l26"})
    resource = SimpleNamespace(vmid=201, guest_os=None)
    session = _FakeSession()

    payload = svc.ensure_guest_os(session, resource, node="pve1", resource_type="qemu")
    assert payload is not None
    assert payload["family"] == "linux"
    assert payload["confidence"] == "low"


def test_ensure_guest_os_unknown_resource_type() -> None:
    resource = SimpleNamespace(vmid=203, guest_os=None)
    assert (
        svc.ensure_guest_os(_FakeSession(), resource, node="pve1", resource_type=None)  # type: ignore[arg-type]
        is None
    )


def test_ensure_guest_os_probe_exception_leaves_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(node, vmid):
        raise ConnectionError("agent timeout")

    monkeypatch.setattr(svc.guest, "ping_qemu_agent", _boom)
    resource = SimpleNamespace(vmid=204, guest_os=None)
    session = _FakeSession()
    assert (
        svc.ensure_guest_os(session, resource, node="pve1", resource_type="qemu")
        is None
    )
    assert session.added == []
