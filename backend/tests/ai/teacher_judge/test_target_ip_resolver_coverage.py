"""Coverage for app.ai.teacher_judge.target_ip_resolver."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.teacher_judge import target_ip_resolver as svc


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"cached": None, "synced": []}
    monkeypatch.setattr(
        svc.resource_repo,
        "get_cached_ip_address",
        lambda *, session, vmid: state["cached"],
    )

    def sync(*, session: Any, vmid: int, live_ip: str) -> str:
        state["synced"].append((vmid, live_ip))
        return live_ip

    monkeypatch.setattr(svc.resource_repo, "sync_ip_cache", sync)
    return state


def _resolve(live_resource: dict[str, Any] | None = None) -> str:
    return svc.resolve_target_ip_address(
        session=object(),  # type: ignore[arg-type]
        vmid=101,
        live_resource=live_resource,
    )


def test_find_resource_failure_returns_empty(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(vmid: int) -> dict[str, Any]:
        raise RuntimeError("pve down")

    monkeypatch.setattr(svc.proxmox_ops, "find_resource", boom)
    assert _resolve() == ""


def test_found_resource_without_node_returns_empty(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc.proxmox_ops, "find_resource", lambda vmid: {"node": "", "type": "lxc"}
    )
    assert _resolve({"node": "pve1", "type": "storage"}) == ""


def test_live_ip_lookup_failure_returns_empty(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        svc.proxmox_ops, "find_resource", lambda vmid: {"node": "pve1", "type": "qemu"}
    )

    def boom(node: str, vmid: int, resource_type: str) -> str:
        raise RuntimeError("agent missing")

    monkeypatch.setattr(svc.proxmox_ops, "get_ip_address", boom)
    assert _resolve() == ""


def test_live_ip_blank_returns_empty(
    repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc.proxmox_ops, "get_ip_address", lambda n, v, t: "  ")
    assert _resolve({"node": "pve1", "type": "lxc"}) == ""
    assert repo["synced"] == []
