"""Coverage tests for app.services.notification.web_push_service."""

from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import contextmanager
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.models import PushSubscription
from app.services.notification import push_policy, web_push_service
from app.services.notification.push_policy import PushMessage

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


def _message(text: str = "hi") -> PushMessage:
    return PushMessage(title=text, body=text, tag="t", url="/", kind="test", id="1")


def _subscription(**overrides: Any) -> PushSubscription:
    values: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "endpoint": ENDPOINT,
        "p256dh": "p",
        "auth": "a",
        "language": DEFAULT_LANGUAGE,
        "failure_count": 0,
    }
    values.update(overrides)
    return PushSubscription(**values)


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _clean_tick_state():
    web_push_service.reset_tick_state()
    yield
    web_push_service.reset_tick_state()


# ---------------------------------------------------------------- availability


def test_is_available_reports_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pywebpush", None)
    assert web_push_service.is_available() is False


def test_is_available_reports_installed_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pywebpush", ModuleType("pywebpush"))
    assert web_push_service.is_available() is True


def test_is_public_address_rejects_non_ip() -> None:
    assert web_push_service._is_public_address("not-an-ip") is False


def test_check_endpoint_unresolved_when_no_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_push_service,
        "socket",
        SimpleNamespace(getaddrinfo=lambda *a, **kw: [], IPPROTO_TCP=6),
    )
    assert (
        web_push_service.check_endpoint(ENDPOINT)
        is web_push_service.EndpointVerdict.UNRESOLVED
    )


# ---------------------------------------------------------------- _send_one


def _install_fake_pywebpush(monkeypatch: pytest.MonkeyPatch, webpush) -> None:
    fake_pywebpush = ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    fake_pywebpush.WebPushException = WebPushException  # type: ignore[attr-defined]
    fake_pywebpush.webpush = webpush  # type: ignore[attr-defined]
    fake_vapid = ModuleType("py_vapid")
    fake_vapid.Vapid = SimpleNamespace(from_pem=lambda pem: "vapid")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pywebpush", fake_pywebpush)
    monkeypatch.setitem(sys.modules, "py_vapid", fake_vapid)
    monkeypatch.setattr(
        web_push_service,
        "check_endpoint",
        lambda endpoint: web_push_service.EndpointVerdict.ALLOWED,
    )


def test_send_one_swallows_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def webpush(**_kwargs: Any) -> None:
        raise RuntimeError("socket closed")

    _install_fake_pywebpush(monkeypatch, webpush)

    result = web_push_service._send_one(
        _subscription(), _message(), private_key_pem="PEM", subject="mailto:a@b.c"
    )

    assert result == (False, None)


# ---------------------------------------------------------------- send_messages


def _patch_repo(monkeypatch: pytest.MonkeyPatch) -> list[list[uuid.UUID]]:
    deleted: list[list[uuid.UUID]] = []
    repo = web_push_service.push_repo
    monkeypatch.setattr(
        repo,
        "get_web_push_config",
        lambda *, session: SimpleNamespace(subject="mailto:admin@example.edu"),
    )
    monkeypatch.setattr(repo, "get_vapid_private_key", lambda *, session, config: "PEM")

    def delete(*, session, ids):
        deleted.append(list(ids))
        return len(ids)

    monkeypatch.setattr(repo, "delete_subscriptions_by_ids", delete)
    return deleted


def test_send_messages_without_subscriptions_is_noop() -> None:
    report = web_push_service.send_messages(
        session=_FakeSession(), subscriptions=[], message_for_language=_message()
    )
    assert report.sent == 0 and report.removed == 0


def test_send_messages_resets_failure_count_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_repo(monkeypatch)
    monkeypatch.setattr(web_push_service, "_send_one", lambda *a, **kw: (True, None))
    subscription = _subscription(failure_count=3)
    session = _FakeSession()

    report = web_push_service.send_messages(
        session=session,
        subscriptions=[subscription],
        message_for_language=_message(),
    )

    assert report.sent == 1
    assert subscription.failure_count == 0
    assert session.added == [subscription]


# ---------------------------------------------------------------- send_to_user / test


def test_send_to_user_and_send_test(monkeypatch: pytest.MonkeyPatch) -> None:
    subscription = _subscription(language="unknown-lang")
    monkeypatch.setattr(
        web_push_service.push_repo,
        "list_subscriptions_for_user",
        lambda *, session, user_id: [subscription],
    )
    captured: list[Any] = []

    def fake_send_messages(*, session, subscriptions, message_for_language):
        captured.append((subscriptions, message_for_language))
        return web_push_service.SendReport(sent=len(subscriptions))

    monkeypatch.setattr(web_push_service, "send_messages", fake_send_messages)

    report = web_push_service.send_test(
        session=_FakeSession(), user=SimpleNamespace(id=uuid.uuid4())
    )

    assert report.sent == 1
    subscriptions, messages = captured[0]
    assert subscriptions == [subscription]
    assert set(messages) == set(SUPPORTED_LANGUAGES)


# ---------------------------------------------------------------- tick helpers


def test_job_messages_by_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(push_policy, "job_message", lambda job, lang: _message(lang))
    messages = web_push_service._job_messages_by_language(object())
    assert messages is not None and set(messages) == set(SUPPORTED_LANGUAGES)

    monkeypatch.setattr(push_policy, "job_message", lambda job, lang: None)
    assert web_push_service._job_messages_by_language(object()) is None


def test_process_user_pushes_job_transitions_and_reminders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.course import reminder_service
    from app.services.jobs import jobs_service

    user = SimpleNamespace(id=uuid.uuid4())
    own = SimpleNamespace(id="a", user_id=user.id)
    other = SimpleNamespace(id="b", user_id=uuid.uuid4())
    silent = SimpleNamespace(id="c", user_id=user.id)
    monkeypatch.setattr(
        jobs_service,
        "list_recent_for_user",
        lambda **_kwargs: SimpleNamespace(items=[own, other, silent]),
    )
    seen_jobs: list[list[Any]] = []

    def diff_jobs(items, baseline):
        seen_jobs.append(items)
        return items, push_policy.JobBaseline()

    monkeypatch.setattr(push_policy, "diff_job_snapshot", diff_jobs)
    monkeypatch.setattr(
        web_push_service,
        "_job_messages_by_language",
        lambda job: None if job is silent else {DEFAULT_LANGUAGE: _message()},
    )
    reminder = SimpleNamespace(id="r1")
    monkeypatch.setattr(
        reminder_service,
        "list_student_reminders",
        lambda session, *, user_id: [reminder],
    )
    monkeypatch.setattr(
        push_policy,
        "diff_reminders",
        lambda reminders, baseline: (reminders, push_policy.ReminderBaseline()),
    )
    monkeypatch.setattr(push_policy, "reminder_message", lambda r: _message("rem"))
    sent_messages: list[Any] = []

    def fake_send_messages(*, session, subscriptions, message_for_language):
        sent_messages.append(message_for_language)
        return web_push_service.SendReport(sent=1)

    monkeypatch.setattr(web_push_service, "send_messages", fake_send_messages)
    state = web_push_service._UserState()

    sent = web_push_service._process_user(
        session=_FakeSession(),
        user=user,
        subscriptions=[_subscription()],
        state=state,
        include_reminders=True,
    )

    assert sent == 2
    assert seen_jobs == [[own, silent]]
    assert sent_messages[1] == _message("rem")
    assert state.reminders is not None


# ---------------------------------------------------------------- tick


def test_process_push_notifications_disabled_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_push_service, "is_available", lambda: False)
    assert web_push_service.process_push_notifications() == 0


def test_process_push_notifications_walks_subscribed_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_push_service, "is_available", lambda: True)
    active = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    inactive = SimpleNamespace(id=uuid.uuid4(), is_active=False)
    no_subs = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    broken = SimpleNamespace(id=uuid.uuid4(), is_active=True)
    missing_id = uuid.uuid4()
    users = {u.id: u for u in (active, inactive, no_subs, broken)}
    session = _FakeSession()
    session.get = lambda _model, user_id: users.get(user_id)  # type: ignore[attr-defined]

    @contextmanager
    def fake_session(_engine):
        yield session

    monkeypatch.setattr(web_push_service, "Session", fake_session)
    monkeypatch.setattr(
        web_push_service.push_repo,
        "list_subscribed_user_ids",
        lambda *, session: [missing_id, inactive.id, no_subs.id, active.id, broken.id],
    )
    subscription = _subscription()

    def list_subs(*, session, user_id):
        if user_id == no_subs.id:
            return []
        if user_id == broken.id:
            raise RuntimeError("db hiccup")
        return [subscription]

    monkeypatch.setattr(
        web_push_service.push_repo, "list_subscriptions_for_user", list_subs
    )
    processed: list[tuple[Any, bool]] = []

    def fake_process_user(*, session, user, subscriptions, state, include_reminders):
        processed.append((user, include_reminders))
        return 3

    monkeypatch.setattr(web_push_service, "_process_user", fake_process_user)
    stale_id = uuid.uuid4()
    web_push_service._tick_state.users[stale_id] = web_push_service._UserState()

    sent = web_push_service.process_push_notifications()

    assert sent == 3
    assert processed == [(active, True)]
    assert stale_id not in web_push_service._tick_state.users
    assert active.id in web_push_service._tick_state.users
    assert session.rollbacks == 1
    assert web_push_service._tick_state.round_index == 1


def test_run_push_notifier_returns_when_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_push_service, "is_available", lambda: False)
    asyncio.run(web_push_service.run_push_notifier(asyncio.Event()))
