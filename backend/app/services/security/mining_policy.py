"""挖礦偵測決策 — 全部純函式，不碰 DB / PVE / SMTP。

偵測特徵：視窗內平均 CPU 持續高於閾值。coverage（樣本覆蓋率）防止
資料稀疏（剛開機、RRD 缺洞）造成的誤判 — 覆蓋不足時寧可不動作，
等下一輪掃描資料補齊再判。
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta
from typing import Any

# PVE day timeframe 的取樣密度 ≈ 每 30 分鐘一點
_DAY_TIMEFRAME_POINTS_PER_HOUR = 2.0

# 視窗內有效樣本覆蓋率下限 — 低於此值視為資料不足，不判定
MIN_SAMPLE_COVERAGE = 2.0 / 3.0


class MiningAction(str, enum.Enum):
    flag = "flag"
    none = "none"


def cpu_stats(
    rrd: list[dict[str, Any]], *, window_hours: int, now: datetime
) -> tuple[float, float] | None:
    """RRD 視窗內的 (平均 CPU percent, 樣本覆蓋率)。

    覆蓋率 = 視窗內有 cpu 值的點數 / 期望點數（day timeframe 每 30 分鐘
    一點），封頂 1.0。視窗內無任何有效點回傳 None。
    """
    window_start = (now - timedelta(hours=window_hours)).timestamp()
    values: list[float] = []
    for point in rrd:
        ts = point.get("time")
        cpu = point.get("cpu")
        if ts is None or cpu is None:
            continue
        if float(ts) >= window_start:
            values.append(float(cpu) * 100.0)
    if not values:
        return None
    expected_points = max(window_hours * _DAY_TIMEFRAME_POINTS_PER_HOUR, 1.0)
    coverage = min(len(values) / expected_points, 1.0)
    return sum(values) / len(values), coverage


def decide_mining_action(
    *,
    avg_cpu: float | None,
    coverage: float,
    exempt: bool,
    has_open_incident: bool,
    threshold_percent: float,
) -> MiningAction:
    """單台資源的挖礦判定。

    豁免、已有未結案事件、資料不足（無均值或覆蓋率低於 2/3）皆不動作。
    """
    if exempt or has_open_incident:
        return MiningAction.none
    if avg_cpu is None or coverage < MIN_SAMPLE_COVERAGE:
        return MiningAction.none
    if avg_cpu >= threshold_percent:
        return MiningAction.flag
    return MiningAction.none


__all__ = [
    "MIN_SAMPLE_COVERAGE",
    "MiningAction",
    "cpu_stats",
    "decide_mining_action",
]
