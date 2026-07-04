"""挖礦偵測純函式的表驅動測試（無 DB / PVE）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.security.mining_policy import (
    MiningAction,
    cpu_stats,
    decide_mining_action,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _rrd_points(
    *,
    hours: float,
    cpu: float,
    now: datetime = NOW,
    step_minutes: int = 30,
) -> list[dict]:
    """產生視窗內每 step_minutes 一點的 RRD 假資料（PVE day timeframe 約 30 分鐘一點）。"""
    points = []
    steps = int(hours * 60 / step_minutes)
    for i in range(steps):
        ts = now - timedelta(minutes=step_minutes * i)
        points.append({"time": ts.timestamp(), "cpu": cpu})
    return points


# ── cpu_stats ────────────────────────────────────────────────────────────────


def test_cpu_stats_full_window_high_cpu() -> None:
    rrd = _rrd_points(hours=6, cpu=0.98)
    stats = cpu_stats(rrd, window_hours=6, now=NOW)
    assert stats is not None
    avg, coverage = stats
    assert avg == pytest.approx(98.0)
    assert coverage == pytest.approx(1.0)


def test_cpu_stats_half_window_coverage() -> None:
    # 只有最近 3 小時有資料，視窗 6 小時 → coverage ≈ 0.5
    rrd = _rrd_points(hours=3, cpu=0.95)
    stats = cpu_stats(rrd, window_hours=6, now=NOW)
    assert stats is not None
    avg, coverage = stats
    assert avg == pytest.approx(95.0)
    assert coverage == pytest.approx(0.5)


def test_cpu_stats_empty_rrd_returns_none() -> None:
    assert cpu_stats([], window_hours=6, now=NOW) is None


def test_cpu_stats_points_missing_cpu_key_ignored() -> None:
    rrd = _rrd_points(hours=6, cpu=0.9)
    # 混入缺 cpu 鍵的點（PVE 對缺資料時段就是不帶 cpu 鍵）
    rrd += [{"time": (NOW - timedelta(minutes=5)).timestamp()}] * 4
    stats = cpu_stats(rrd, window_hours=6, now=NOW)
    assert stats is not None
    avg, _coverage = stats
    assert avg == pytest.approx(90.0)


def test_cpu_stats_points_outside_window_filtered() -> None:
    # 全部點都在視窗外 → 無有效點 → None
    old = NOW - timedelta(hours=48)
    rrd = _rrd_points(hours=6, cpu=0.99, now=old)
    assert cpu_stats(rrd, window_hours=6, now=NOW) is None


def test_cpu_stats_coverage_capped_at_one() -> None:
    # 點密度高於預期（hour timeframe 混入）→ coverage 封頂 1.0
    rrd = _rrd_points(hours=6, cpu=0.97, step_minutes=10)
    stats = cpu_stats(rrd, window_hours=6, now=NOW)
    assert stats is not None
    _avg, coverage = stats
    assert coverage == pytest.approx(1.0)


# ── decide_mining_action ─────────────────────────────────────────────────────


def _decide(**overrides) -> MiningAction:
    kwargs = {
        "avg_cpu": 95.0,
        "coverage": 0.9,
        "exempt": False,
        "has_open_incident": False,
        "threshold_percent": 90.0,
    }
    kwargs.update(overrides)
    return decide_mining_action(**kwargs)


def test_decide_flags_sustained_high_cpu() -> None:
    assert _decide() is MiningAction.flag


def test_decide_below_threshold_none() -> None:
    assert _decide(avg_cpu=80.0) is MiningAction.none


def test_decide_exempt_none() -> None:
    assert _decide(exempt=True) is MiningAction.none


def test_decide_open_incident_none() -> None:
    assert _decide(has_open_incident=True) is MiningAction.none


def test_decide_insufficient_coverage_none() -> None:
    assert _decide(coverage=0.5) is MiningAction.none


def test_decide_no_data_none() -> None:
    assert _decide(avg_cpu=None) is MiningAction.none


def test_decide_boundary_at_threshold_flags() -> None:
    assert _decide(avg_cpu=90.0) is MiningAction.flag


def test_decide_boundary_coverage_two_thirds_flags() -> None:
    assert _decide(coverage=2.0 / 3.0) is MiningAction.flag
