"""Coverage tests for app.services.network.publish_target_policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.repositories.resource as resource_repo
from app.exceptions import BadRequestError
from app.services.network import ip_management_service, publish_target_policy


def test_parse_helpers_skip_empty_and_invalid_values() -> None:
    assert publish_target_policy._parse_ipv4(None) is None
    assert publish_target_policy._parse_ipv4("") is None
    networks = publish_target_policy._parse_networks(
        ["", "not-a-network", "10.0.0.0/24", "fd00::/64"]
    )
    assert [str(n) for n in networks] == ["10.0.0.0/24"]


def test_allocation_lookup_failure_falls_back_to_holder_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(resource_repo, "get_allocated_ip_address", broken)

    # 查不到配發紀錄就退回「不可是別人的位址」檢查；簡化 session 沒有任何紀錄
    publish_target_policy.assert_publishable_vm_ip(object(), "10.0.0.5", vmid=101)


def test_invalid_target_skips_holder_check_and_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_repo, "get_allocated_ip_address", lambda **_kwargs: None
    )
    lookups: list[str] = []
    monkeypatch.setattr(
        ip_management_service,
        "get_allocation_by_ip",
        lambda session, ip: lookups.append(ip),
    )

    with pytest.raises(BadRequestError):
        publish_target_policy.assert_publishable_vm_ip(object(), "garbage", vmid=101)
    assert lookups == []


def test_holder_matching_the_same_vmid_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ip_management_service,
        "get_allocation_by_ip",
        lambda session, ip: SimpleNamespace(vmid=101),
    )

    publish_target_policy._assert_not_held_by_others(object(), "10.0.0.5", vmid=101)
