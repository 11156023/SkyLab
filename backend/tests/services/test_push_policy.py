"""Web Push 純決策與送出失敗處理的單元測試（不需 DB、不打推播服務）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import PushSubscription
from app.schemas.course import CourseReminderStudent
from app.schemas.jobs import JobItem, JobKind, JobStatus
from app.services.notification import push_policy, web_push_service
from app.services.notification.push_policy import (
    JobBaseline,
    diff_job_snapshot,
    diff_reminders,
    job_message,
    job_touched_at,
    reminder_message,
)

# 名字以 test_ 開頭會被 pytest 當成測試收集，取別名
from app.services.notification.push_policy import test_message as push_test_message

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)


def _job(
    job_id: str,
    status: JobStatus,
    *,
    touched: datetime,
    kind: JobKind = JobKind.template,
    completed: datetime | None = None,
    message: str | None = None,
) -> JobItem:
    return JobItem(
        id=job_id,
        kind=kind,
        title=f"任務 {job_id}",
        status=status,
        message=message,
        user_id=uuid.uuid4(),
        created_at=touched - timedelta(seconds=5),
        updated_at=touched,
        completed_at=completed,
    )


def _reminder(reminder_id: str) -> CourseReminderStudent:
    return CourseReminderStudent(
        id=reminder_id,
        kind="resource_expiry",
        tone="warning",
        icon="schedule",
        title=f"提醒 {reminder_id}",
        description="說明",
        time_label="3 小時後",
        target="/resources/100",
        occurred_at=_T0,
    )


# ─── diff_job_snapshot ───────────────────────────────────────────────────────


def test_job_touched_at_takes_the_later_timestamp() -> None:
    job = _job(
        "a", JobStatus.completed, touched=_T0, completed=_T0 + timedelta(seconds=5)
    )
    assert job_touched_at(job) == _T0 + timedelta(seconds=5)


def test_first_round_only_builds_baseline() -> None:
    items = [
        _job("a", JobStatus.completed, touched=_T0),
        _job("b", JobStatus.running, touched=_T0),
    ]
    transitions, baseline = diff_job_snapshot(items, None)
    assert transitions == []
    assert baseline.status_by_id == {"a": JobStatus.completed, "b": JobStatus.running}
    assert baseline.high_water == _T0


def test_seen_job_turning_terminal_is_pushed() -> None:
    _, baseline = diff_job_snapshot([_job("b", JobStatus.running, touched=_T0)], None)
    transitions, _ = diff_job_snapshot(
        [_job("b", JobStatus.completed, touched=_T0 + timedelta(seconds=8))], baseline
    )
    assert [j.id for j in transitions] == ["b"]


def test_terminal_job_that_stays_terminal_is_not_pushed_again() -> None:
    _, baseline = diff_job_snapshot([_job("a", JobStatus.blocked, touched=_T0)], None)
    transitions, _ = diff_job_snapshot(
        [_job("a", JobStatus.blocked, touched=_T0 + timedelta(hours=1))], baseline
    )
    assert transitions == []


def test_job_created_and_finished_between_rounds_is_pushed_once() -> None:
    _, baseline = diff_job_snapshot(
        [_job("old", JobStatus.completed, touched=_T0)], None
    )
    quick = _job("quick", JobStatus.completed, touched=_T0 + timedelta(seconds=3))
    transitions, baseline = diff_job_snapshot(
        [quick, _job("old", JobStatus.completed, touched=_T0)], baseline
    )
    assert [j.id for j in transitions] == ["quick"]
    transitions, _ = diff_job_snapshot([quick], baseline)
    assert transitions == []


def test_unseen_terminal_job_older_than_high_water_is_history() -> None:
    _, baseline = diff_job_snapshot([_job("a", JobStatus.completed, touched=_T0)], None)
    stale = _job("stale", JobStatus.failed, touched=_T0 - timedelta(hours=1))
    transitions, _ = diff_job_snapshot([stale], baseline)
    assert transitions == []


def test_high_water_never_goes_backwards() -> None:
    _, baseline = diff_job_snapshot([_job("a", JobStatus.completed, touched=_T0)], None)
    _, baseline = diff_job_snapshot(
        [_job("b", JobStatus.running, touched=_T0 - timedelta(seconds=30))], baseline
    )
    assert baseline.high_water == _T0


def test_empty_baseline_high_water_treats_any_terminal_as_fresh() -> None:
    transitions, _ = diff_job_snapshot(
        [_job("x", JobStatus.completed, touched=_T0)], JobBaseline()
    )
    assert [j.id for j in transitions] == ["x"]


# ─── diff_reminders ──────────────────────────────────────────────────────────


def test_reminders_first_round_builds_baseline_then_only_new_ones_are_pushed() -> None:
    fresh, baseline = diff_reminders([_reminder("r1")], None)
    assert fresh == []
    fresh, baseline = diff_reminders([_reminder("r1"), _reminder("r2")], baseline)
    assert [r.id for r in fresh] == ["r2"]
    fresh, _ = diff_reminders([_reminder("r2")], baseline)
    assert fresh == []


# ─── 文案 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected_title"),
    [
        (JobStatus.completed, "範本任務已完成"),
        (JobStatus.failed, "範本任務失敗"),
        (JobStatus.blocked, "範本任務受阻"),
        (JobStatus.cancelled, "範本任務已取消"),
    ],
)
def test_job_message_zh_tw(status: JobStatus, expected_title: str) -> None:
    job = _job("template:1", status, touched=_T0, message="磁碟不足")
    message = job_message(job, "zh-TW")
    assert message is not None
    assert message.title == expected_title
    assert message.tag == "template:1"
    assert message.url == "/jobs?job=template:1"
    assert message.kind == "job"
    # 失敗／受阻用錯誤訊息當內文，其餘用任務名稱
    if status in (JobStatus.failed, JobStatus.blocked):
        assert message.body == "磁碟不足"
    else:
        assert message.body == "任務 template:1"


def test_job_message_en_and_non_terminal() -> None:
    job = _job(
        "vm_request:1", JobStatus.completed, touched=_T0, kind=JobKind.vm_request
    )
    message = job_message(job, "en")
    assert message is not None
    assert message.title == "Boot request completed"
    assert job_message(_job("r", JobStatus.running, touched=_T0), "en") is None


def test_reminder_and_test_messages() -> None:
    reminder = reminder_message(_reminder("r1"))
    assert reminder.kind == "reminder"
    assert reminder.url == "/resources/100"
    assert reminder.to_payload()["title"] == "提醒 r1"
    test = push_test_message("ja")
    assert test.kind == "test"
    assert test.title == "デスクトップ通知を有効にしました"


# ─── send_messages：失效訂閱處理 ─────────────────────────────────────────────


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


def _subscription(**overrides: Any) -> PushSubscription:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "endpoint": "https://push.example/x",
        "p256dh": "p",
        "auth": "a",
        "language": "zh-TW",
        "failure_count": 0,
    }
    base.update(overrides)
    return PushSubscription(**base)


@pytest.fixture
def fake_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_push_service.push_repo,
        "get_web_push_config",
        lambda *, session: SimpleNamespace(
            vapid_private_key_pem="PEM", subject="mailto:x@y"
        ),
    )


def test_send_messages_gone_subscription_is_removed(
    monkeypatch: pytest.MonkeyPatch, fake_config: None
) -> None:
    removed: list[list[uuid.UUID]] = []
    monkeypatch.setattr(web_push_service, "_send_one", lambda *a, **k: (False, 410))
    monkeypatch.setattr(
        web_push_service.push_repo,
        "delete_subscriptions_by_ids",
        lambda *, session, ids: removed.append(list(ids)) or len(ids),
    )
    sub = _subscription()
    report = web_push_service.send_messages(
        session=_FakeSession(),  # type: ignore[arg-type]
        subscriptions=[sub],
        message_for_language=push_test_message("zh-TW"),
    )
    assert report.sent == 0
    assert report.removed == 1
    assert removed == [[sub.id]]


def test_send_messages_transient_failures_accumulate_then_remove(
    monkeypatch: pytest.MonkeyPatch, fake_config: None
) -> None:
    removed: list[list[uuid.UUID]] = []
    monkeypatch.setattr(web_push_service, "_send_one", lambda *a, **k: (False, 500))
    monkeypatch.setattr(
        web_push_service.push_repo,
        "delete_subscriptions_by_ids",
        lambda *, session, ids: removed.append(list(ids)) or len(ids),
    )
    session = _FakeSession()
    sub = _subscription(failure_count=web_push_service.MAX_FAILURES - 2)
    web_push_service.send_messages(
        session=session,  # type: ignore[arg-type]
        subscriptions=[sub],
        message_for_language=push_test_message("zh-TW"),
    )
    assert sub.failure_count == web_push_service.MAX_FAILURES - 1
    assert removed == []
    web_push_service.send_messages(
        session=session,  # type: ignore[arg-type]
        subscriptions=[sub],
        message_for_language=push_test_message("zh-TW"),
    )
    assert removed == [[sub.id]]


def test_send_messages_picks_text_by_subscription_language(
    monkeypatch: pytest.MonkeyPatch, fake_config: None
) -> None:
    seen: list[str] = []

    def fake_send(
        subscription: PushSubscription, message: Any, **kwargs: Any
    ) -> tuple[bool, None]:
        seen.append(message.title)
        return True, None

    monkeypatch.setattr(web_push_service, "_send_one", fake_send)
    messages = {lang: push_test_message(lang) for lang in ("zh-TW", "en", "ja")}
    report = web_push_service.send_messages(
        session=_FakeSession(),  # type: ignore[arg-type]
        subscriptions=[
            _subscription(language="en"),
            _subscription(language="ja"),
            _subscription(language="xx"),  # 不支援的語言退回預設
        ],
        message_for_language=messages,
    )
    assert report.sent == 3
    assert seen == [
        "Desktop notifications enabled",
        "デスクトップ通知を有効にしました",
        push_policy.test_message("zh-TW").title,
    ]
