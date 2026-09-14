"""PVE ``rrddata`` 的純函式輔助：時間框選擇與取樣間隔推估，不做任何 I/O。

PVE 各 timeframe 的涵蓋範圍（傳統 70 點 RRA）：hour ≈ 70 分鐘、day ≈ 35 小時、
week ≈ 8.75 天、month ≈ 35 天。取樣密度隨 timeframe 與 PVE 版本不同，
所以覆蓋率之類的計算不要寫死密度，改用 ``sampling_step_seconds`` 從資料推估。
"""

from __future__ import annotations

from itertools import pairwise
from statistics import median
from typing import Any


def timeframe_for_window(window_hours: int) -> str:
    """挑選能完整涵蓋觀察視窗的最短 timeframe（以整數單位保守取界）。

    例：預設 48 小時視窗超出 day 框的涵蓋範圍，必須升到 week。
    """
    if window_hours <= 1:
        return "hour"
    if window_hours <= 24:
        return "day"
    if window_hours <= 24 * 7:
        return "week"
    if window_hours <= 24 * 30:
        return "month"
    return "year"


def sampling_step_seconds(rrd: list[dict[str, Any]]) -> float | None:
    """由相鄰點 ``time`` 差的中位數推估取樣間隔（秒）。

    缺 cpu 等欄位的點也算在內（PVE 對缺資料時段仍回帶 time 的點），
    重複時間戳忽略；不足兩個相異時間戳回 None。
    """
    times = sorted(
        {float(point["time"]) for point in rrd if point.get("time") is not None}
    )
    if len(times) < 2:
        return None
    return float(median(later - earlier for earlier, later in pairwise(times)))
