import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { useJobs } from "./JobsProvider";
import { JobEmpty, JobLoading, JobRow, ReminderRow } from "./JobRow";
import useDialogPresence from "../../hooks/useDialogPresence";
import styles from "./Jobs.module.scss";

const POPOVER_WIDTH = 360;
const GAP = 8;      // popover 與按鈕的間距
const MARGIN = 16;  // popover 與視窗邊緣的最小留白

/**
 * 側欄任務入口：有執行中任務時顯示數量（側欄收合時改為紅點），
 * 點開 popover 列出執行中任務，點單筆開詳情（dialog 由 JobsProvider 掛載）。
 * 需在 JobsProvider 內使用。
 *
 * popover 以 portal 掛在 document.body：側欄的 backdrop-filter 會成為
 * fixed 子元素的定位基準，加上 overflow-x: hidden 會把彈出內容裁掉。
 */
export default function JobsButton({ collapsed = false }) {
  const { t } = useTranslation("components");
  const {
    items,
    isAdmin,
    notifyOnlyMine,
    setNotifyOnlyMine,
    openJob,
    reminders,
    readReminderIds,
    refreshReminders,
    markReminderRead,
    markAllRemindersRead,
    desktopNotifications,
  } = useJobs();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  // 關閉時先播放離場動畫再卸載
  const presence = useDialogPresence(open, 130);
  const btnRef = useRef(null);
  const popRef = useRef(null);

  /* 依按鈕位置把 popover 放到側欄右側、底部對齊按鈕 */
  const updatePos = useCallback(() => {
    const btn = btnRef.current;
    const rect = btn?.getBoundingClientRect();
    if (!rect) return;
    // 以側欄外緣（而非按鈕右緣）為基準，才不會蓋到側欄內距與圓角邊框
    const anchorRight = btn.closest("aside")?.getBoundingClientRect().right ?? rect.right;
    const maxLeft = window.innerWidth - POPOVER_WIDTH - MARGIN;
    setPos({
      left: Math.max(MARGIN, Math.min(anchorRight + GAP, maxLeft)),
      bottom: Math.max(MARGIN, window.innerHeight - rect.bottom),
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePos();
    const onDown = (e) => {
      if (btnRef.current?.contains(e.target)) return;
      if (popRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", updatePos);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", updatePos);
    };
  }, [open, updatePos]);

  /* 側欄收合／展開時按鈕會位移且有寬度動畫，直接收起 popover 免得對不準 */
  useEffect(() => {
    setOpen(false);
  }, [collapsed]);

  const running = items?.length ?? 0;
  const hasRunning = running > 0;
  const unreadReminders = (reminders ?? []).filter((item) => !readReminderIds.includes(item.id));
  const attention = running + unreadReminders.length;

  const openReminder = (reminder) => {
    markReminderRead(reminder.id);
    setOpen(false);
    if (reminder.target) navigate(reminder.target);
  };

  const toggleOpen = () => {
    setOpen((v) => {
      // 開啟當下順手刷新提醒，讓期限／審核結果保持最新；
      // 也重讀瀏覽器通知權限（使用者可能剛在網站設定改過）
      if (!v) {
        refreshReminders();
        desktopNotifications.sync();
      }
      return !v;
    });
  };

  // denied：使用者在瀏覽器封鎖了；insecure：用 http://IP 之類的不安全來源開站，
  // 瀏覽器根本不會問權限，只能改走 https 或 localhost
  const desktopBlocked =
    desktopNotifications.permission === "denied" || desktopNotifications.permission === "insecure";
  const desktopBlockedKey =
    desktopNotifications.permission === "insecure"
      ? "JobsButton.desktopNotificationsInsecure"
      : "JobsButton.desktopNotificationsBlocked";
  // Web Push 狀態說明：訂閱成功代表分頁關掉也收得到；不支援／後端未啟用時提醒只有分頁開著才會通知
  const pushHintKey = {
    subscribed: "JobsButton.pushSubscribed",
    unsupported: "JobsButton.pushUnsupported",
    disabled: "JobsButton.pushDisabled",
    unsubscribed: "JobsButton.pushUnsubscribed",
  }[desktopNotifications.push] ?? null;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`${styles.sidebarBtn} ${collapsed ? styles.sidebarBtnCollapsed : ""} ${open ? styles.sidebarBtnActive : ""}`}
        onClick={toggleOpen}
        title={collapsed ? t("JobsButton.backgroundJobs") : undefined}
        aria-label={t("JobsButton.backgroundJobs")}
        aria-expanded={open}
      >
        <span className={styles.sidebarBtnIcon}>
          <MIcon name="notifications" size={20} />
          {collapsed && attention > 0 && <span className={styles.bellDot} />}
        </span>
        {!collapsed && (
          <>
            <span className={styles.sidebarBtnLabel}>{t("JobsButton.backgroundJobs")}</span>
            {attention > 0 && <span className={styles.countBadge}>{attention}</span>}
          </>
        )}
      </button>

      {presence.open && pos && createPortal(
        <div
          ref={popRef}
          className={`${styles.popover} ${presence.closing ? styles.popoverOut : ""}`}
          style={{ left: pos.left, bottom: pos.bottom }}
        >
          <div className={styles.popoverHeader}>
            <span className={styles.popoverTitle}>{t("JobsButton.runningJobsTitle")}</span>
            <span className={styles.popoverSub}>
              {hasRunning ? t("JobsButton.runningCount", { count: running }) : t("JobsButton.noRunningJobs")}
            </span>
          </div>
          {isAdmin && (
            <label className={styles.notifyToggle}>
              <input
                type="checkbox"
                checked={notifyOnlyMine}
                onChange={(e) => setNotifyOnlyMine(e.target.checked)}
              />
              <span>{t("JobsButton.notifyOnlyMine")}</span>
            </label>
          )}
          {desktopNotifications.supported && (
            <label className={`${styles.notifyToggle} ${desktopBlocked ? styles.notifyToggleDisabled : ""}`}>
              <input
                type="checkbox"
                checked={desktopNotifications.enabled}
                disabled={desktopBlocked}
                onChange={(e) => (e.target.checked ? desktopNotifications.enable() : desktopNotifications.disable())}
              />
              <span>
                {desktopBlocked ? t(desktopBlockedKey) : t("JobsButton.desktopNotifications")}
              </span>
            </label>
          )}
          {desktopNotifications.enabled && pushHintKey && (
            <p className={styles.notifyHint}>{t(pushHintKey)}</p>
          )}
          <div className={styles.popoverList}>
            {items === null ? (
              <JobLoading />
            ) : items.length === 0 ? (
              <JobEmpty message={t("JobsButton.noRunningJobsHint")} />
            ) : (
              items.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  onClick={(j) => {
                    openJob(j.id);
                    setOpen(false);
                  }}
                />
              ))
            )}
          </div>
          <div className={styles.popoverSection}>
            <div className={styles.popoverHeader}>
              <span className={styles.popoverTitle}>{t("JobsButton.remindersTitle")}</span>
              {unreadReminders.length > 0 ? (
                <button
                  type="button"
                  className={styles.markAllBtn}
                  onClick={markAllRemindersRead}
                >
                  {t("JobsButton.markAllRead")}
                  <MIcon name="done_all" size={14} />
                </button>
              ) : (
                <span className={styles.popoverSub}>
                  {(reminders?.length ?? 0) > 0 ? t("JobsButton.allRead") : t("JobsButton.noNewReminders")}
                </span>
              )}
            </div>
            <div className={styles.popoverList}>
              {reminders === null ? (
                <JobLoading />
              ) : reminders.length === 0 ? (
                <JobEmpty message={t("JobsButton.noRemindersHint")} />
              ) : (
                reminders.map((reminder) => (
                  <ReminderRow
                    key={reminder.id}
                    reminder={reminder}
                    unread={!readReminderIds.includes(reminder.id)}
                    onClick={openReminder}
                  />
                ))
              )}
            </div>
          </div>
          <div className={styles.popoverFooter}>
            <Link to="/jobs" className={styles.popoverLink} onClick={() => setOpen(false)}>
              <span>{t("JobsButton.viewAllJobs")}</span>
              <MIcon name="chevron_right" size={16} />
            </Link>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
