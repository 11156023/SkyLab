"""快照清理資格純函式測試。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.governance.snapshot_cleanup_policy import is_cleanup_eligible

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: int) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def test_old_snapshot_eligible() -> None:
    assert is_cleanup_eligible(
        name="snap-1", snaptime=_ts(8), now=NOW, retention_days=7
    )


def test_fresh_snapshot_not_eligible() -> None:
    assert not is_cleanup_eligible(
        name="snap-1", snaptime=_ts(3), now=NOW, retention_days=7
    )


def test_init_snapshot_protected() -> None:
    assert not is_cleanup_eligible(
        name="skylab-init", snaptime=_ts(100), now=NOW, retention_days=7
    )


def test_mining_evidence_protected() -> None:
    assert not is_cleanup_eligible(
        name="mining-202607011200", snaptime=_ts(100), now=NOW, retention_days=7
    )


def test_current_pseudo_snapshot_skipped() -> None:
    assert not is_cleanup_eligible(
        name="current", snaptime=None, now=NOW, retention_days=7
    )


def test_missing_snaptime_skipped() -> None:
    assert not is_cleanup_eligible(
        name="snap-1", snaptime=None, now=NOW, retention_days=7
    )
