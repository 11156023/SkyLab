"""Web Push 的純決策邏輯：哪些任務／提醒該推、推什麼文案。

與前端 ``components/Jobs/jobSnapshotDiff.js`` 同一套規則（後端版），
讓分頁開著時的 toast 與分頁關掉後的推播口徑一致：

1. 上一輪見過且狀態從非終態變成終態 → 推。
2. 上一輪沒見過但已是終態、且時間晚於上一輪的高水位 → 推（涵蓋兩輪之間
   就建立並完成的任務）。
3. 第一輪只建立基準，不推（避免服務重啟時把歷史任務全轟一遍）。

只比對伺服器端時間戳，無 DB、無 I/O，方便單元測試。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.i18n import translate
from app.schemas.course import CourseReminderStudent
from app.schemas.jobs import JobItem, JobStatus

TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.completed, JobStatus.failed, JobStatus.blocked, JobStatus.cancelled}
)


@dataclass
class JobBaseline:
    """上一輪快照的基準：各 job 狀態 + 時間高水位。"""

    status_by_id: dict[str, JobStatus] = field(default_factory=dict)
    high_water: datetime | None = None


@dataclass
class ReminderBaseline:
    seen_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PushMessage:
    """送進推播服務的內容；``tag`` 相同的通知在瀏覽器端互相取代。"""

    title: str
    body: str
    tag: str
    url: str
    kind: str  # "job" | "reminder" | "test"
    id: str

    def to_payload(self) -> dict[str, str]:
        return {
            "title": self.title,
            "body": self.body,
            "tag": self.tag,
            "url": self.url,
            "kind": self.kind,
            "id": self.id,
        }


def job_touched_at(job: JobItem) -> datetime | None:
    """任務最後一次變動的伺服器時間；updated_at 與 completed_at 取較晚者。"""
    candidates = [dt for dt in (job.updated_at, job.completed_at) if dt is not None]
    return max(candidates) if candidates else None


def _later(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def diff_job_snapshot(
    items: list[JobItem], baseline: JobBaseline | None
) -> tuple[list[JobItem], JobBaseline]:
    """回傳 (需要推播的任務, 下一輪的基準)。``baseline`` 為 None 表示第一輪。"""
    next_baseline = JobBaseline(high_water=baseline.high_water if baseline else None)
    transitions: list[JobItem] = []

    for job in items:
        next_baseline.status_by_id[job.id] = job.status
        next_baseline.high_water = _later(next_baseline.high_water, job_touched_at(job))
        if baseline is None:
            continue
        if job.status not in TERMINAL_STATUSES:
            continue

        prev_status = baseline.status_by_id.get(job.id)
        if prev_status is not None:
            if prev_status != job.status:
                transitions.append(job)
            continue
        # 沒見過且已是終態：晚於上一輪高水位才算「剛剛結束」
        touched = job_touched_at(job)
        if touched is not None and (
            baseline.high_water is None or touched > baseline.high_water
        ):
            transitions.append(job)

    return transitions, next_baseline


def diff_reminders(
    reminders: list[CourseReminderStudent], baseline: ReminderBaseline | None
) -> tuple[list[CourseReminderStudent], ReminderBaseline]:
    """新出現的提醒；第一輪只建基準。"""
    next_baseline = ReminderBaseline(seen_ids={r.id for r in reminders})
    if baseline is None:
        return [], next_baseline
    fresh = [r for r in reminders if r.id not in baseline.seen_ids]
    return fresh, next_baseline


# ─── 文案 ────────────────────────────────────────────────────────────────────

_KIND_KEYS = {
    "vm_request": "push.kind_vm_request",
    "spec_change": "push.kind_spec_change",
    "deletion": "push.kind_deletion",
    "template": "push.kind_template",
}

_STATUS_KEYS = {
    JobStatus.completed: "push.job_completed",
    JobStatus.failed: "push.job_failed",
    JobStatus.blocked: "push.job_blocked",
    JobStatus.cancelled: "push.job_cancelled",
}


def job_message(job: JobItem, lang: str) -> PushMessage | None:
    """終態任務的推播內容；非終態回 None。

    URL 帶 ``?job=`` 讓沒有分頁時開新視窗也能直接開詳情；
    已有分頁時 Service Worker 改用 postMessage，頁面不重載。
    """
    status_key = _STATUS_KEYS.get(job.status)
    if status_key is None:
        return None
    kind_label = translate(_KIND_KEYS.get(job.kind.value, "push.kind_generic"), lang)
    title = translate(status_key, lang, kind=kind_label)
    # 失敗／受阻時把錯誤訊息當內文，其餘用任務名稱（與前端 toast 一致）
    if job.status in (JobStatus.failed, JobStatus.blocked):
        body = job.message or job.title
    else:
        body = job.title
    return PushMessage(
        title=title,
        body=body,
        tag=job.id,
        url=f"/jobs?job={job.id}",
        kind="job",
        id=job.id,
    )


def reminder_message(reminder: CourseReminderStudent) -> PushMessage:
    """提醒本身已由 reminder_service 依使用者情境產生文案，直接沿用。"""
    return PushMessage(
        title=reminder.title,
        body=reminder.description,
        tag=reminder.id,
        url=reminder.target or "/",
        kind="reminder",
        id=reminder.id,
    )


def test_message(lang: str) -> PushMessage:
    return PushMessage(
        title=translate("push.test_title", lang),
        body=translate("push.test_body", lang),
        tag="skylab-push-test",
        url="/",
        kind="test",
        id="test",
    )


__all__ = [
    "TERMINAL_STATUSES",
    "JobBaseline",
    "PushMessage",
    "ReminderBaseline",
    "diff_job_snapshot",
    "diff_reminders",
    "job_message",
    "job_touched_at",
    "reminder_message",
    "test_message",
]
