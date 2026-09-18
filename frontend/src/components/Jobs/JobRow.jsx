import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import styles from "./Jobs.module.scss";
import { formatShortDateTime } from "../../utils/formatDate";

/** 任務類型顯示名稱（與 JobsPage 的 KIND_LABELS 對齊，popover 用短版） */
export const JOB_KIND_LABEL_KEYS = {
  migration:     "JobRow.kindMigration",
  vm_request:    "JobRow.kindVmRequest",
  spec_change:   "JobRow.kindSpecChange",
  deletion:      "JobRow.kindDeletion",
  template:      "JobRow.kindTemplate",
};

/** 狀態顯示名稱 + MIcon 名稱 + 色調 class key */
export const JOB_STATUS_META_KEYS = {
  pending:   { labelKey: "JobRow.statusPending",   icon: "pause",         tone: "toneMuted" },
  running:   { labelKey: "JobRow.statusRunning",   icon: "autorenew",     tone: "toneInfo", spin: true },
  completed: { labelKey: "JobRow.statusCompleted", icon: "check_circle",  tone: "toneSuccess" },
  failed:    { labelKey: "JobRow.statusFailed",    icon: "cancel",        tone: "toneDanger" },
  blocked:   { labelKey: "JobRow.statusBlocked",   icon: "error_outline", tone: "tonePending" },
  cancelled: { labelKey: "JobRow.statusCancelled", icon: "cancel",        tone: "toneMuted" },
};

export function JobRow({ job, onClick }) {
  const { t } = useTranslation("components");
  const meta = JOB_STATUS_META_KEYS[job.status] ?? JOB_STATUS_META_KEYS.pending;
  const showProgress =
    job.progress !== null &&
    job.status !== "completed" &&
    job.status !== "cancelled" &&
    job.status !== "failed";
  const clickable = typeof onClick === "function";

  return (
    <button
      type="button"
      className={styles.jobRow}
      onClick={clickable ? () => onClick(job) : undefined}
      disabled={!clickable}
    >
      <span className={`${styles.jobRowIcon} ${styles[meta.tone]} ${meta.spin ? styles.spin : ""}`}>
        <MIcon name={meta.icon} size={16} />
      </span>
      <span className={styles.jobRowBody}>
        <span className={styles.jobRowHead}>
          <span className={styles.jobKindChip}>{JOB_KIND_LABEL_KEYS[job.kind] ? t(JOB_KIND_LABEL_KEYS[job.kind]) : job.kind}</span>
          <span className={styles.jobRowTitle} title={job.title}>{job.title}</span>
        </span>
        {job.message && (
          <span className={styles.jobRowMessage} title={job.message}>{job.message}</span>
        )}
        {showProgress && (
          <span className={styles.jobRowProgress}>
            <span
              className={`${styles.jobRowProgressFill} ${job.status === "blocked" ? styles.jobRowProgressBlocked : ""}`}
              style={{ width: `${job.progress ?? 5}%` }}
            />
          </span>
        )}
        <span className={styles.jobRowFoot}>
          <span>{t(meta.labelKey)}</span>
          <span>{formatShortDateTime(job.updated_at)}</span>
        </span>
      </span>
    </button>
  );
}

/* 提醒 tone（後端 courses/reminders 的 tone 欄位）→ JobRow 色調 class */
const REMINDER_TONE_CLASS = {
  success: "toneSuccess",
  warning: "tonePending",
  danger:  "toneDanger",
};

/** 提醒列：機器期限、審核結果與近期課堂任務，樣式與 JobRow 一致 */
export function ReminderRow({ reminder, unread = false, onClick }) {
  const { t } = useTranslation("components");
  const tone = REMINDER_TONE_CLASS[reminder.tone] ?? "toneInfo";

  return (
    <button type="button" className={styles.jobRow} onClick={() => onClick(reminder)}>
      <span className={`${styles.jobRowIcon} ${styles[tone]}`}>
        <MIcon name={reminder.icon || "notifications"} size={16} />
      </span>
      <span className={styles.jobRowBody}>
        <span className={styles.jobRowHead}>
          <span className={styles.jobRowTitle} title={reminder.title}>{reminder.title}</span>
          {unread && <span className={styles.unreadDot} aria-label={t("JobRow.unread")} />}
        </span>
        {reminder.description && (
          <span className={styles.jobRowMessage} title={reminder.description}>
            {reminder.description}
          </span>
        )}
        <span className={styles.jobRowFoot}>
          <span>{unread ? t("JobRow.unread") : t("JobRow.read")}</span>
          <span>{reminder.time_label}</span>
        </span>
      </span>
    </button>
  );
}

export function JobEmpty({ message }) {
  const { t } = useTranslation("components");
  return (
    <div className={styles.jobEmpty}>
      <MIcon name="auto_awesome" size={24} />
      <span>{message ?? t("JobRow.noJobs")}</span>
    </div>
  );
}

export function JobLoading() {
  const { t } = useTranslation("components");
  return (
    <div className={styles.jobLoading}>
      <span className={styles.spin}>
        <MIcon name="refresh" size={16} />
      </span>
      <span>{t("JobRow.loading")}</span>
    </div>
  );
}
