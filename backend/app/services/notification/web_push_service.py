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
import enum
import ipaddress
import json
import logging
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlmodel import Session

from app.core.db import engine
from app.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES
from app.models import PushSubscription, User
from app.repositories import push as push_repo
from app.schemas.push import is_allowed_push_endpoint
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
        import pywebpush  # noqa: F401 — 只為偵測套件存在
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


class EndpointVerdict(enum.Enum):
    """送出前對訂閱 endpoint 的判定。"""

    ALLOWED = "allowed"
    # 指向非公網位址（或 scheme／主機本身就不合法）：永遠不送，訂閱直接刪除
    BLOCKED = "blocked"
    # 此刻解析不到：這輪不送，算一次送達失敗（連續失敗才會刪訂閱）
    UNRESOLVED = "unresolved"


# 被封鎖的 endpoint 在 _send_one 回報成這個狀態碼，沿用「訂閱已不存在」的刪除路徑
_BLOCKED_ENDPOINT_STATUS = 410


def _is_public_address(raw: str) -> bool:
    try:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped  # ::ffff:10.0.0.5 依內嵌的 IPv4 判斷
    return addr.is_global and not addr.is_multicast


def check_endpoint(endpoint: str) -> EndpointVerdict:
    """在「實際送出前」重新解析 endpoint 主機，擋掉指向內網的推播（SSRF）。

    訂閱當下的檢查（``app.schemas.push`` 的格式驗證、``routes/push.py`` 的解析
    檢查）擋不住 DNS rebinding（名稱之後改指向內網），也沒檢查過修正前就存好的
    訂閱，所以每次送出前都要在這裡再判一次。任一解析結果不是公網位址就封鎖；
    解析失敗一律不送（不能在看不到位址時放行）。URL 本身不合法（無法解析、
    非 https、非 443 埠）或主機不在推播服務白名單內，不解析直接封鎖，絕不丟例外。
    """
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
        port = parts.port
    except ValueError:
        # 例如 "https://[::1/x" 這種殘缺的 IPv6 字面值、或超出範圍的埠號
        return EndpointVerdict.BLOCKED
    if parts.scheme != "https" or not host or port not in (None, 443):
        return EndpointVerdict.BLOCKED
    # 白名單建立前就存好的訂閱可能指向任意主機；不是推播服務就不送、也不解析
    if not is_allowed_push_endpoint(endpoint):
        return EndpointVerdict.BLOCKED
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return EndpointVerdict.UNRESOLVED
    if not infos:
        return EndpointVerdict.UNRESOLVED
    if all(_is_public_address(str(info[4][0])) for info in infos):
        return EndpointVerdict.ALLOWED
    return EndpointVerdict.BLOCKED


def _send_one(
    subscription: PushSubscription,
    message: PushMessage,
    *,
    private_key_pem: str,
    subject: str,
) -> tuple[bool, int | None]:
    """回傳 (成功?, 推播服務的 HTTP 狀態碼或 None)。不丟例外。

    送出前先過 ``check_endpoint``：指向非公網位址的訂閱回報成 410（與推播服務
    說訂閱已不存在同樣處理，由 ``send_messages`` 刪除）；解析不到則這輪不送、
    回 (False, None)，照一般送達失敗累計，連續失敗才刪。
    """
    verdict = check_endpoint(subscription.endpoint)
    if verdict is EndpointVerdict.BLOCKED:
        logger.warning(
            "Dropping push subscription %s: endpoint host is not a public address",
            subscription.id,
        )
        return False, _BLOCKED_ENDPOINT_STATUS
    if verdict is EndpointVerdict.UNRESOLVED:
        logger.warning(
            "Skipping push to subscription %s: endpoint host did not resolve",
            subscription.id,
        )
        return False, None

    from py_vapid import Vapid
    from pywebpush import WebPushException, webpush

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
    except Exception as exc:
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
    private_key_pem = push_repo.get_vapid_private_key(session=session, config=config)
    dead: list[uuid.UUID] = []

    for subscription in subscriptions:
        # 訂閱當下已檢查過白名單，但修正前就存好的訂閱沒有；送出前一律重判，
        # 不在白名單內（任意公網主機、非 443 埠、格式殘缺）直接刪除不送。
        if not is_allowed_push_endpoint(subscription.endpoint):
            logger.warning(
                "Dropping push subscription %s: endpoint not allowed", subscription.id
            )
            dead.append(subscription.id)
            continue
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
            private_key_pem=private_key_pem,
            subject=config.subject,
        )
        if ok:
            report.sent += 1
            if subscription.failure_count:
                subscription.failure_count = 0
                session.add(subscription)
            continue
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
        reminder_service,
    )
    from app.services.jobs import jobs_service

    sent = 0
    # own_only：管理員也只在 SQL 層取本人任務，否則全站任務先截到 SNAPSHOT_LIMIT，
    # 管理員自己剛結束的任務會被擠掉而漏推
    snapshot = jobs_service.list_recent_for_user(
        session=session, user=user, limit=SNAPSHOT_LIMIT, own_only=True
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
            except Exception:
                logger.exception("Web Push tick failed for user %s", user_id)
                session.rollback()
    return sent_total


@contextmanager
def push_notifier_leader_gate() -> Iterator[bool]:
    """推播迴圈專用的 leader 鎖：多副本時只有一個行程推，避免重複通知。

    沒拿到鎖的那一輪順手清掉行程內的去重基準：否則這個行程之後搶回 leader
    時，會拿幾小時前的舊基準去 diff，把中間所有終態變化一次補推出去。
    """
    from app.services.scheduling.leader import (
        PUSH_NOTIFIER_LEADER_LOCK_KEY,
        scheduler_leader_lock,
    )

    with scheduler_leader_lock(PUSH_NOTIFIER_LEADER_LOCK_KEY) as is_leader:
        if not is_leader:
            reset_tick_state()
        yield is_leader


async def run_push_notifier(stop_event: asyncio.Event) -> None:
    """lifespan 啟動的推播迴圈：沿用主排程的 runner，但用自己的短週期。

    去重基準 ``_tick_state`` 只在行程內；leader 換手時新 leader 第一輪視為
    初始快照（不推），所以換手期間的事件最多漏一輪，但不會重複推送。
    """
    from app.domain.scheduling.models import ScheduledTask
    from app.domain.scheduling.runner import run_polling_scheduler
    from app.services.monitoring.heartbeat_service import HeartbeatObserver

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
        leader_gate=push_notifier_leader_gate,
        observer=HeartbeatObserver("web_push", interval_seconds=PUSH_POLL_SECONDS),
    )


__all__ = [
    "MAX_FAILURES",
    "PUSH_POLL_SECONDS",
    "EndpointVerdict",
    "SendReport",
    "check_endpoint",
    "is_available",
    "normalize_language",
    "process_push_notifications",
    "reset_tick_state",
    "run_push_notifier",
    "send_messages",
    "send_test",
    "send_to_user",
]
