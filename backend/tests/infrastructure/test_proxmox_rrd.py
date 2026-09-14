"""PVE rrddata 純函式輔助（時間框選擇、取樣間隔推估）。"""

from __future__ import annotations

import pytest

from app.infrastructure.proxmox.rrd import (
    sampling_step_seconds,
    timeframe_for_window,
)


@pytest.mark.parametrize(
    ("window_hours", "expected"),
    [
        (1, "hour"),
        (2, "day"),
        (24, "day"),
        # 預設 48h 閒置視窗、72h 挖礦視窗都超過 day 框（約 35h），必須升到 week
        (25, "week"),
        (48, "week"),
        (72, "week"),
        (24 * 7, "week"),
        (24 * 7 + 1, "month"),
        (24 * 30, "month"),
        (24 * 30 + 1, "year"),
    ],
)
def test_timeframe_for_window(window_hours: int, expected: str) -> None:
    assert timeframe_for_window(window_hours) == expected


def test_sampling_step_median_ignores_duplicates_and_missing_values() -> None:
    rrd = [{"time": 1800.0 * i, "cpu": 0.1} for i in range(10)]
    rrd += [{"time": 1800.0 * 9}] * 3  # 重複時間戳（缺 cpu）
    rrd += [{"time": 1800.0 * 9 + 300}]  # 尾端多一個較密的點
    rrd += [{"cpu": 0.5}]  # 沒有 time 的點忽略
    assert sampling_step_seconds(rrd) == pytest.approx(1800.0)


def test_sampling_step_needs_two_distinct_points() -> None:
    assert sampling_step_seconds([]) is None
    assert sampling_step_seconds([{"time": 100.0, "cpu": 0.2}]) is None
    assert sampling_step_seconds([{"time": 100.0}, {"time": 100.0}]) is None
