"""Coverage tests for app.services.notification.push_policy."""

from datetime import UTC, datetime

from app.services.notification import push_policy


def test_later_keeps_first_value_when_second_is_missing() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    assert push_policy._later(moment, None) == moment
    assert push_policy._later(None, moment) == moment
