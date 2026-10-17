"""app.services.scheduling.recurrence 的預設政策與視窗邊界分支測試。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.scheduling import recurrence


def test_schedule_policy_defaults_without_config(monkeypatch) -> None:
    monkeypatch.setattr(
        recurrence.proxmox_config_repo, "get_proxmox_config", lambda _session: None
    )
    policy = recurrence.get_schedule_policy(session=None)  # type: ignore[arg-type]
    assert policy == recurrence._DEFAULT_POLICY


def test_active_or_next_window_rejects_empty_rule_or_duration() -> None:
    now = datetime(2026, 10, 14, 2, 0, tzinfo=UTC)
    assert recurrence.compute_active_or_next_window("", 60, None, now) is None
    assert recurrence.compute_active_or_next_window("FREQ=DAILY", 0, None, now) is None


def test_active_or_next_window_skips_window_ending_exactly_now() -> None:
    # 台北 10:00、每日 09:00 開始 60 分鐘：當天視窗剛好在 now 結束，
    # 應改回傳隔天 09:00 的視窗
    now = datetime(2026, 10, 14, 2, 0, tzinfo=UTC)
    window = recurrence.compute_active_or_next_window(
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0", 60, "Asia/Taipei", now
    )
    assert window == (
        datetime(2026, 10, 15, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 15, 2, 0, tzinfo=UTC),
    )
