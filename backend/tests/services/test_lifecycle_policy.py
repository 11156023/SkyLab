"""TTL/閒置生命週期決策純函式測試（假時鐘，不依賴 PVE/DB）。"""

from datetime import date, datetime, timedelta, timezone

from app.services.governance.lifecycle_policy import (
    IdleAction,
    TtlAction,
    average_cpu_percent,
    decide_idle_action,
    decide_ttl_action,
)

NOW = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
WARN_DAYS = 3
GRACE_DAYS = 7


def _ttl(
    *,
    expiry: date | None,
    notified: datetime | None = None,
    scheduled: datetime | None = None,
    running: bool = True,
    now: datetime = NOW,
) -> TtlAction:
    return decide_ttl_action(
        expiry_date=expiry,
        expiry_notified_at=notified,
        scheduled_deletion_at=scheduled,
        is_running=running,
        now=now,
        warn_days=WARN_DAYS,
        grace_delete_days=GRACE_DAYS,
    )


class TestDecideTtlAction:
    def test_no_expiry(self) -> None:
        assert _ttl(expiry=None) is TtlAction.none

    def test_far_from_expiry(self) -> None:
        assert _ttl(expiry=NOW.date() + timedelta(days=30)) is TtlAction.none

    def test_warn_window_not_notified(self) -> None:
        assert _ttl(expiry=NOW.date() + timedelta(days=2)) is TtlAction.warn

    def test_warn_window_already_notified(self) -> None:
        assert (
            _ttl(
                expiry=NOW.date() + timedelta(days=2),
                notified=NOW - timedelta(days=1),
            )
            is TtlAction.none
        )

    def test_expired_running(self) -> None:
        assert _ttl(expiry=NOW.date() - timedelta(days=1)) is TtlAction.stop

    def test_expired_not_running(self) -> None:
        assert (
            _ttl(expiry=NOW.date() - timedelta(days=1), running=False)
            is TtlAction.none
        )

    def test_grace_elapsed_delete(self) -> None:
        assert (
            _ttl(expiry=NOW.date() - timedelta(days=GRACE_DAYS + 1), running=False)
            is TtlAction.delete
        )

    def test_grace_elapsed_already_scheduled(self) -> None:
        assert (
            _ttl(
                expiry=NOW.date() - timedelta(days=GRACE_DAYS + 1),
                scheduled=NOW - timedelta(hours=1),
                running=False,
            )
            is TtlAction.none
        )

    def test_grace_elapsed_still_running_deletes(self) -> None:
        # 寬限期滿即使還在跑也應進刪除佇列（刪除服務會處理關機）
        assert (
            _ttl(expiry=NOW.date() - timedelta(days=GRACE_DAYS + 1))
            is TtlAction.delete
        )


class TestAverageCpuPercent:
    def test_window_filter_and_mean(self) -> None:
        rrd = [
            # 視窗外（3 小時前，window=2）
            {"time": (NOW - timedelta(hours=3)).timestamp(), "cpu": 1.0},
            {"time": (NOW - timedelta(hours=1)).timestamp(), "cpu": 0.02},
            {"time": (NOW - timedelta(minutes=30)).timestamp(), "cpu": 0.04},
        ]
        avg = average_cpu_percent(rrd, window_hours=2, now=NOW)
        assert avg is not None
        assert abs(avg - 3.0) < 1e-6

    def test_empty_rrd(self) -> None:
        assert average_cpu_percent([], window_hours=2, now=NOW) is None

    def test_points_without_cpu_ignored(self) -> None:
        rrd = [
            {"time": (NOW - timedelta(minutes=10)).timestamp()},
            {"time": (NOW - timedelta(minutes=5)).timestamp(), "cpu": None},
        ]
        assert average_cpu_percent(rrd, window_hours=2, now=NOW) is None


class TestDecideIdleAction:
    THRESHOLD = 1.0
    GRACE_HOURS = 24

    def _idle(
        self,
        *,
        avg: float | None,
        idle_since: datetime | None = None,
        now: datetime = NOW,
    ) -> IdleAction:
        return decide_idle_action(
            avg_cpu=avg,
            idle_since=idle_since,
            now=now,
            threshold_percent=self.THRESHOLD,
            grace_hours=self.GRACE_HOURS,
        )

    def test_below_threshold_first_time(self) -> None:
        assert self._idle(avg=0.5) is IdleAction.mark

    def test_marked_within_grace(self) -> None:
        assert (
            self._idle(avg=0.5, idle_since=NOW - timedelta(hours=2))
            is IdleAction.none
        )

    def test_marked_grace_elapsed(self) -> None:
        assert (
            self._idle(avg=0.5, idle_since=NOW - timedelta(hours=25))
            is IdleAction.stop
        )

    def test_active_again_clears(self) -> None:
        assert (
            self._idle(avg=5.0, idle_since=NOW - timedelta(hours=2))
            is IdleAction.clear
        )

    def test_active_not_marked(self) -> None:
        assert self._idle(avg=5.0) is IdleAction.none

    def test_no_data(self) -> None:
        assert self._idle(avg=None) is IdleAction.none
        assert (
            self._idle(avg=None, idle_since=NOW - timedelta(hours=30))
            is IdleAction.none
        )
