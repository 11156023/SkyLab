import styles from "./TemplatesPage.module.scss";

export const TEMPLATE_STATUS_LABEL = {
  creating: "建立中",
  ready: "就緒",
  updating: "更新循環中",
  failed: "失敗",
  deleted: "已刪除",
};

const TEMPLATE_STATUS_CLASS = {
  creating: "badge_info",
  ready: "badge_ok",
  updating: "badge_info",
  failed: "badge_err",
  deleted: "badge_muted",
};

export function TemplateStatusBadge({ status }) {
  return (
    <span className={`${styles.badge} ${styles[TEMPLATE_STATUS_CLASS[status] ?? "badge_muted"]}`}>
      {TEMPLATE_STATUS_LABEL[status] ?? status}
    </span>
  );
}

export const TASK_STATUS_LABEL = {
  queued: "排隊中",
  running: "執行中",
  succeeded: "成功",
  failed: "失敗",
};

const TASK_STATUS_CLASS = {
  queued: "badge_muted",
  running: "badge_info",
  succeeded: "badge_ok",
  failed: "badge_err",
};

export function TaskStatusBadge({ status }) {
  return (
    <span className={`${styles.badge} ${styles[TASK_STATUS_CLASS[status] ?? "badge_muted"]}`}>
      {TASK_STATUS_LABEL[status] ?? status}
    </span>
  );
}

export function TaskProgressBar({ progress, status }) {
  const value = status === "succeeded" ? 100 : Math.max(0, Math.min(100, progress ?? 0));
  return (
    <div className={styles.progressBar}>
      <div
        className={`${styles.progressFill} ${status === "failed" ? styles.progressFill_danger : ""}`}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}
