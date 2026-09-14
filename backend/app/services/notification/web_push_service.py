"""Web Push 服務：訂閱管理、送出推播、排程 tick。

推播鏈：後端（本模組）→ 推播服務（FCM／Mozilla autopush，由瀏覽器決定）→
瀏覽器 Service Worker（frontend/public/sw.js）→ 系統通知。

排程 tick（``process_push_notifications``）只替「有訂閱的使用者」計算任務快照與
提醒，與 ``/ws/jobs`` 用同一份 ``list_recent_for_user``，再交給 ``push_policy`` 判斷
該推哪些。基準存在行程記憶體：服務重啟後第一輪只建基準不推，避免轟炸。

推播只涵蓋使用者本人的任務（管理員看得到全站任務，但推播不替別人的任務吵他）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from app.core.db import engine
from app.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.models import PushSubscription, User
from app.repositories import push as push_repo
from app.services.notification import push_policy
from app.services.notification.push_policy import (
    JobBaseline,
    PushMessage,
    ReminderBaseline,
)

logger = logging.getLogger(__name__)

# 推播迴圈的輪詢間隔：任務結束後最多這麼久會推到關掉分頁的使用者
PUSH_POLL_SECONDS = 10
# 推播服務保留訊息的秒數（使用者離線時）；任務結果過了這段時間就沒意義了
PUSH_TTL_SECONDS = 600
PUSH_TIMEOUT_SECONDS = 10.0
# 非 404／410 的送達失敗連續超過此數就當訂閱已死
MAX_FAILURES = 5
# 提醒變化以小時計，不必每輪重算：每 N 輪查一次
REMINDER_REFRESH_ROUNDS = 3
# 每位使用者每輪最多看幾筆任務（與 /ws/jobs 相同）
SNAPSHOT_LIMIT = 20


def is_available() -> bool:
    """pywebpush 是否可用；缺套件時整個功能靜默關閉（前端會顯示「後端未啟用」）。"""
    try:
        import pywebpush  # noqa: F401, PLC0415 — 只為偵測套件存在
    except ImportError:
        return False
    return True


def normalize_language(value: str | None) -> str:
    if value and value in SUPPORTED_LANGUAGES:
        return value
    return DEFAULT_LANGUAGE


# ─── 送出 ────────────────────────────────────────────────────────────────────


@dataclass
class SendReport:
    sent: int = 0
    removed: int = 0
    failed_ids: list[uuid.UUID] = field(default_factory=list)


def _send_one(
    subscription: PushSubscription,
    message: PushMessage,
    *,
    private_key_pem: str,
    subject: str,
) -> tuple[bool, int | None]:
    """回傳 (成功?, 推播服務的 HTTP 狀態碼或 None)。不丟例外。"""
    from py_vapid import Vapid  # noqa: PLC0415 — 缺套件時 is_available() 已擋
    from pywebpush import WebPushException, webpush  # noqa: PLC0415

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(message.to_payload(), ensure_ascii=False),
            vapid_private_key=Vapid.from_pem(private_key_pem.encode("ascii")),
            # webpush() 會把 aud／exp 塞進這個 dict，每次都要給新的
            vapid_claims={"sub": subject},
            ttl=PUSH_TTL_SECONDS,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        logger.warning(
            "Web Push delivery failed: subscription=%s status=%s error=%s",
            subscription.id,
            status,
            exc,
        )
        return False, status
    except Exception as exc:  # noqa: BLE001 — 網路／編碼問題都不該讓排程中斷
        logger.warning(
            "Web Push delivery error: subscription=%s error=%s", subscription.id, exc
        )
        return False, None
    return True, None


def send_messages(
    *,
    session: Session,
    subscriptions: list[PushSubscription],
    message_for_language: dict[str, PushMessage] | PushMessage,
) -> SendReport:
    """對一組訂閱送同一則通知（依各訂閱語言取文案），並處理失效訂閱。"""
    report = SendReport()
    if not subscriptions:
        return report
    config = push_repo.get_web_push_config(session=session)
    dead: list[uuid.UUID] = []

    for subscription in subscriptions:
        if isinstance(message_for_language, PushMessage):
            message = message_for_language
        else:
            message = (
                message_for_language.get(normalize_language(subscription.language))
                or message_for_language[DEFAULT_LANGUAGE]
            )
        ok, status = _send_one(
            subscription,
            message,
            private_key_pem=config.vapid_private_key_pem,
            subject=config.subject,
        )
        if ok:
            report.sent += 1
            if subscription.failure_count:
                subscription.failure_count = 0
                session.add(subscription)
            continue
        report.failed_ids.append(subscription.id)
        # 404／410：推播服務說這個訂閱已經不存在（使用者退訂、清了瀏覽器資料）
        if status in (404, 410):
            dead.append(subscription.id)
            continue
        subscription.failure_count += 1
        if subscription.failure_count >= MAX_FAILURES:
            dead.append(subscription.id)
        else:
            session.add(subscription)

    session.commit()
    if dead:
        report.removed = push_repo.delete_subscriptions_by_ids(
            session=session, ids=dead
        )
    return report


def send_to_user(
    *,
    session: Session,
    user_id: uuid.UUID,
    message_for_language: dict[str, PushMessage] | PushMessage,
) -> SendReport:
    subscriptions = list(
        push_repo.list_subscriptions_for_user(session=session, user_id=user_id)
    )
    return send_messages(
        session=session,
        subscriptions=subscriptions,
        message_for_language=message_for_language,
    )


def send_test(*, session: Session, user: User) -> SendReport:
    messages = {lang: push_policy.test_message(lang) for lang in SUPPORTED_LANGUAGES}
    return send_to_user(session=session, user_id=user.id, message_for_language=messages)


# ─── 排程 tick ───────────────────────────────────────────────────────────────


@dataclass
class _UserState:
    jobs: JobBaseline | None = None
    reminders: ReminderBaseline | None = None


@dataclass
class _TickState:
    users: dict[uuid.UUID, _UserState] = field(default_factory=dict)
    round_index: int = 0


_tick_state = _TickState()


def reset_tick_state() -> None:
    """測試用：清掉行程內的基準。"""
    _tick_state.users.clear()
    _tick_state.round_index = 0


def _job_messages_by_language(job: Any) -> dict[str, PushMessage] | None:
    messages: dict[str, PushMessage] = {}
    for lang in SUPPORTED_LANGUAGES:
        message = push_policy.job_message(job, lang)
        if message is None:
            return None
        messages[lang] = message
    return messages


def _process_user(
    *,
    session: Session,
    user: User,
    subscriptions: list[PushSubscription],
    state: _UserState,
    include_reminders: bool,
) -> int:
    from app.services.course import (
        reminder_service,  # noqa: PLC0415 — 避免 import cycle
    )
    from app.services.jobs import jobs_service  # noqa: PLC0415

    sent = 0
    snapshot = jobs_service.list_recent_for_user(
        session=session, user=user, limit=SNAPSHOT_LIMIT
    )
    own_jobs = [job for job in snapshot.items if job.user_id == user.id]
    transitions, state.jobs = push_policy.diff_job_snapshot(own_jobs, state.jobs)
    for job in transitions:
        messages = _job_messages_by_language(job)
        if messages is None:
            continue
        sent += send_messages(
            session=session, subscriptions=subscriptions, message_for_language=messages
        ).sent

    if include_reminders:
        reminders = reminder_service.list_student_reminders(session, user_id=user.id)
        fresh, state.reminders = push_policy.diff_reminders(reminders, state.reminders)
        for reminder in fresh:
            sent += send_messages(
                session=session,
                subscriptions=subscriptions,
                message_for_language=push_policy.reminder_message(reminder),
            ).sent
    return sent


def process_push_notifications() -> int:
    """Scheduler tick：替有訂閱的使用者比對任務／提醒並送推播。回傳送出的則數。"""
    if not is_available():
        return 0
    include_reminders = _tick_state.round_index % REMINDER_REFRESH_ROUNDS == 0
    _tick_state.round_index += 1
    sent_total = 0

    with Session(engine) as session:
        user_ids = push_repo.list_subscribed_user_ids(session=session)
        # 沒訂閱了的人把基準丟掉，重新訂閱時再從頭建
        for stale in set(_tick_state.users) - set(user_ids):
            _tick_state.users.pop(stale, None)

        for user_id in user_ids:
            try:
                user = session.get(User, user_id)
                if user is None or not user.is_active:
                    continue
                subscriptions = list(
                    push_repo.list_subscriptions_for_user(
                        session=session, user_id=user_id
                    )
                )
                if not subscriptions:
                    continue
                state = _tick_state.users.setdefault(user_id, _UserState())
                sent_total += _process_user(
                    session=session,
                    user=user,
                    subscriptions=subscriptions,
                    state=state,
                    include_reminders=include_reminders,
                )
            except Exception:  # noqa: BLE001 — 單一使用者失敗不影響其他人
                logger.exception("Web Push tick failed for user %s", user_id)
                session.rollback()
    return sent_total


async def run_push_notifier(stop_event: asyncio.Event) -> None:
    """lifespan 啟動的推播迴圈：沿用主排程的 runner，但用自己的短週期。"""
    from app.domain.scheduling.models import ScheduledTask  # noqa: PLC0415
    from app.domain.scheduling.runner import run_polling_scheduler  # noqa: PLC0415

    if not is_available():
        logger.info("pywebpush not installed; Web Push notifier disabled")
        return
    await run_polling_scheduler(
        stop_event=stop_event,
        interval_seconds=PUSH_POLL_SECONDS,
        tasks=[
            ScheduledTask(
                name="process_push_notifications", handler=process_push_notifications
            )
        ],
    )


__all__ = [
    "MAX_FAILURES",
    "PUSH_POLL_SECONDS",
    "SendReport",
    "is_available",
    "normalize_language",
    "process_push_notifications",
    "reset_tick_state",
    "run_push_notifier",
    "send_messages",
    "send_test",
    "send_to_user",
]
