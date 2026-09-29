"""GPU 可用量扣除時段內已核准申請：/gpu/options 與 AI 推薦共用同一規則。

SR-IOV vGPU 一張卡可切成多份，used_count 的上限必須是 capacity_count，
不能是實體 device_count；否則預留一多就把已用數壓在卡數，實際容量被低報。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas.gpu import GPUSummary
from app.services.proxmox import gpu_service

_START = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
_END = _START + timedelta(hours=2)


def _patch_overlapping(
    monkeypatch: pytest.MonkeyPatch, requests: list[Any]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_overlapping(**kwargs: Any) -> list[Any]:
        calls.append(kwargs)
        return requests

    monkeypatch.setattr(
        gpu_service.vm_request_repo,
        "get_approved_vm_requests_overlapping_window",
        fake_overlapping,
    )
    return calls


def test_mdev_reservations_are_capped_by_capacity_not_device_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    option = GPUSummary(
        mapping_id="h200",
        device_count=1,
        capacity_count=8,
        used_count=2,
        available_count=6,
        is_sriov=True,
        has_mdev=True,
    )
    _patch_overlapping(
        monkeypatch,
        [SimpleNamespace(gpu_mapping_id="h200", vmid=None) for _ in range(3)],
    )

    (adjusted,) = gpu_service.apply_reservation_window(
        object(), [option], start_at=_START, end_at=_END
    )

    assert adjusted.used_count == 5
    assert adjusted.available_count == 3
    assert option.used_count == 2  # 原物件不被改動


def test_provisioned_requests_and_other_mappings_are_not_reserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    option = GPUSummary(mapping_id="a100", device_count=2, capacity_count=2)
    _patch_overlapping(
        monkeypatch,
        [
            SimpleNamespace(gpu_mapping_id="a100", vmid=101),  # 已開機，PVE 已計入
            SimpleNamespace(gpu_mapping_id="other", vmid=None),
            SimpleNamespace(gpu_mapping_id=None, vmid=None),
        ],
    )

    (adjusted,) = gpu_service.apply_reservation_window(
        object(), [option], start_at=_START, end_at=_END
    )

    assert adjusted is option


def test_usage_is_capped_and_availability_never_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    option = GPUSummary(
        mapping_id="m60", device_count=2, capacity_count=0, used_count=1, available_count=1
    )
    _patch_overlapping(
        monkeypatch,
        [SimpleNamespace(gpu_mapping_id="m60", vmid=None) for _ in range(5)],
    )

    (adjusted,) = gpu_service.apply_reservation_window(
        object(), [option], start_at=_START, end_at=_END
    )

    # capacity_count 為 0 時退回 device_count
    assert adjusted.used_count == 2
    assert adjusted.available_count == 0


def test_reversed_window_returns_options_without_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = [GPUSummary(mapping_id="a100")]
    calls = _patch_overlapping(monkeypatch, [])

    result = gpu_service.apply_reservation_window(
        object(), options, start_at=_END, end_at=_START
    )

    assert result is options
    assert calls == []
